import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from backend.db import SHOTS, fetch_all, fetch_one, get_profile, get_settings, log
from backend.matcher import score_job

_lock = threading.Lock()
_state = {
    "running": False,
    "phase": "idle",
    "last_error": "",
    "last_cycle": None,
    "stop": False,
}


def state():
    return dict(_state)


def request_stop():
    _state["stop"] = True


def _reset_night_counter(conn, settings):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if settings.get("night_date") != today:
        conn.execute(
            "UPDATE settings SET applied_tonight = 0, night_date = ? WHERE id = 1",
            (today,),
        )
        conn.commit()
        settings["applied_tonight"] = 0
        settings["night_date"] = today


def cycle_once(conn, headless=True, skip_discover=False, job_ids=None):
    from backend.agent.apply import apply_on_page
    from backend.agent.browser import close_browser, launch_browser
    from backend.agent.discover import harvest_listing_page

    with _lock:
        if _state["running"]:
            return {"ok": False, "error": "already running"}
        _state["running"] = True
        _state["stop"] = False
        _state["last_error"] = ""
        _state["phase"] = "starting"

    try:
        settings = get_settings(conn)
        profile = get_profile(conn)
        _reset_night_counter(conn, settings)
        companies = fetch_all(
            conn, "SELECT * FROM companies WHERE enabled = 1 ORDER BY id"
        )
        queued = fetch_all(
            conn,
            "SELECT id FROM jobs WHERE status IN ('queued', 'new') LIMIT 1",
        )
        if not companies and not queued:
            log(conn, "No career pages and no queued job URLs.", "warn")
            _state["phase"] = "idle"
            return {"ok": True, "discovered": 0, "applied": 0}

        pw = browser = None
        discovered = 0
        applied = 0
        try:
            _state["phase"] = "browser"
            pw, browser, context, page = launch_browser(headless=headless)

            _state["phase"] = "discover"
            if skip_discover:
                companies = []
            for company in companies:
                if _state["stop"]:
                    break
                name = company["name"]
                url = company["career_url"]
                log(conn, f"Scanning {name}: {url}")
                try:
                    jobs = harvest_listing_page(page, url, name)
                except Exception as exc:
                    log(conn, f"Scan failed for {name}: {exc}", "error")
                    continue
                for job in jobs:
                    if _state["stop"]:
                        break
                    existing = fetch_one(
                        conn, "SELECT id FROM jobs WHERE url = ?", (job["url"],)
                    )
                    score = score_job(
                        profile, job["title"], job.get("description") or "", job.get("location") or ""
                    )
                    if existing:
                        conn.execute(
                            "UPDATE jobs SET title=?, location=?, description=?, match_score=?, updated_at=datetime('now') WHERE id=?",
                            (
                                job["title"],
                                job.get("location") or "",
                                job.get("description") or "",
                                score,
                                existing["id"],
                            ),
                        )
                    else:
                        conn.execute(
                            """INSERT INTO jobs (company_id, company_name, title, url, location, description, match_score, status)
                               VALUES (?, ?, ?, ?, ?, ?, ?, 'queued')""",
                            (
                                company["id"],
                                name,
                                job["title"],
                                job["url"],
                                job.get("location") or "",
                                job.get("description") or "",
                                score,
                            ),
                        )
                        discovered += 1
                    conn.commit()
                log(conn, f"{name}: processed {len(jobs)} listing links")

            settings = get_settings(conn)
            min_score = float(settings.get("min_score") or 35)
            max_night = int(settings.get("max_per_night") or 8)
            applied_tonight = int(settings.get("applied_tonight") or 0)
            dry_run = bool(settings.get("dry_run"))
            auto_submit = bool(settings.get("auto_submit")) and not dry_run
            remaining = max(0, max_night - applied_tonight)
            if skip_discover:
                dry_run = False
                auto_submit = True
                if job_ids:
                    placeholders = ",".join("?" * len(job_ids))
                    candidates = fetch_all(
                        conn,
                        f"SELECT * FROM jobs WHERE id IN ({placeholders}) AND status IN ('queued', 'new') ORDER BY id DESC",
                        tuple(job_ids),
                    )
                else:
                    candidates = fetch_all(
                        conn,
                        """SELECT * FROM jobs
                           WHERE status IN ('queued', 'new')
                             AND company_name IN ('ChatGPT', 'Pasted')
                           ORDER BY id DESC""",
                    )
            else:
                candidates = fetch_all(
                    conn,
                    """SELECT * FROM jobs
                       WHERE status IN ('queued', 'new') AND match_score >= ?
                       ORDER BY match_score DESC, id DESC
                       LIMIT ?""",
                    (min_score, remaining),
                )

            _state["phase"] = "apply"
            resume_path = profile.get("resume_path") or ""
            for job in candidates:
                if _state["stop"]:
                    break
                log(
                    conn,
                    f"Applying to {job['title']} @ {job['company_name']} ({job['match_score']})",
                )
                conn.execute(
                    "INSERT INTO applications (job_id, status, notes) VALUES (?, 'started', ?)",
                    (job["id"], "browser apply"),
                )
                conn.execute(
                    "UPDATE jobs SET status='applying', updated_at=datetime('now') WHERE id=?",
                    (job["id"],),
                )
                conn.commit()
                app_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                try:
                    result = apply_on_page(
                        page,
                        job["url"],
                        profile,
                        resume_path,
                        auto_submit=auto_submit,
                        screenshot_dir=Path(SHOTS),
                        job_id=job["id"],
                    )
                    page_title = result.get("page_title") or ""
                    if page_title and (
                        job["title"].startswith("Ingested role")
                        or job["title"].startswith("Pasted role")
                    ):
                        conn.execute(
                            "UPDATE jobs SET title=? WHERE id=?",
                            (page_title[:160], job["id"]),
                        )
                    notes = (
                        f"title={page_title or job['title']} filled={result['fields_filled']} "
                        f"uploaded={result['resume_uploaded']} submitted={result['submitted']} "
                        f"clicked_apply={result['clicked_apply']} dry_run={dry_run} url={result['final_url']}"
                    )
                    if dry_run:
                        status = "drafted"
                        job_status = "drafted"
                    elif result["submitted"]:
                        status = "submitted"
                        job_status = "applied"
                    elif result["fields_filled"] or result["resume_uploaded"]:
                        status = "form_filled_not_submitted"
                        job_status = "needs_review"
                    else:
                        status = "no_apply_form"
                        job_status = "needs_review"
                    conn.execute(
                        """UPDATE applications SET status=?, notes=?, screenshot_path=?, finished_at=datetime('now')
                           WHERE id=?""",
                        (status, notes, result.get("screenshot_path") or "", app_id),
                    )
                    conn.execute(
                        "UPDATE jobs SET status=?, updated_at=datetime('now') WHERE id=?",
                        (job_status, job["id"]),
                    )
                    conn.execute(
                        "UPDATE settings SET applied_tonight = applied_tonight + 1 WHERE id = 1"
                    )
                    conn.commit()
                    applied += 1
                    log(conn, f"Result {job['title']}: {status}")
                except Exception as exc:
                    conn.execute(
                        """UPDATE applications SET status='failed', notes=?, finished_at=datetime('now') WHERE id=?""",
                        (str(exc), app_id),
                    )
                    conn.execute(
                        "UPDATE jobs SET status='failed', updated_at=datetime('now') WHERE id=?",
                        (job["id"],),
                    )
                    conn.commit()
                    log(conn, f"Apply failed {job['title']}: {exc}", "error")
        finally:
            if pw and browser:
                close_browser(pw, browser)

        _state["last_cycle"] = datetime.now(timezone.utc).isoformat()
        _state["phase"] = "idle"
        log(conn, f"Cycle done. discovered={discovered} applied={applied}")
        return {"ok": True, "discovered": discovered, "applied": applied}
    except Exception as exc:
        _state["last_error"] = str(exc)
        _state["phase"] = "error"
        log(conn, f"Cycle crashed: {exc}", "error")
        return {"ok": False, "error": str(exc)}
    finally:
        _state["running"] = False
        if _state["phase"] not in ("error",):
            _state["phase"] = "idle"


class NightShift:
    def __init__(self, conn):
        self.conn = conn
        self._thread = None

    def start_loop(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while True:
            try:
                settings = get_settings(self.conn)
                if settings.get("overnight_enabled") and not _state["running"]:
                    cycle_once(self.conn, headless=True)
                interval = max(5, int(settings.get("interval_minutes") or 20))
            except Exception:
                interval = 20
            time.sleep(interval * 60)
