import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from backend.db import SHOTS, fetch_all, fetch_one, get_profile, get_settings, log
from backend.matcher import score_job
from backend.agent.portals import company_from_url, is_job_posting_url, portal_name

_lock = threading.Lock()
_state = {
    "running": False,
    "phase": "idle",
    "last_error": "",
    "last_cycle": None,
    "stop": False,
    "current_job": "",
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


def _title_from_page(job, result):
    title = (result.get("page_title") or "").strip()
    current = job.get("title") or ""
    if title and (
        current.startswith("Ingested role")
        or current.startswith("Pasted role")
        or current.startswith("Queued role")
        or not current
    ):
        cleaned = title
        for junk in (" - Greenhouse", " | Lever", " | Ashby", " - Job Application"):
            cleaned = cleaned.replace(junk, "")
        return cleaned[:160]
    return current[:160] if current else title[:160]


def _job_status_from_result(result, auto_submit):
    block = result.get("block") or ""
    if block in ("linkedin_login_required", "login_required", "captcha_or_bot_check"):
        return "blocked", "needs_review"
    if result.get("submitted") or (auto_submit and result.get("submitted")):
        return "submitted", "applied"
    if result.get("fields_filled") or result.get("resume_uploaded"):
        if auto_submit:
            return "form_filled_not_submitted", "needs_review"
        return "drafted", "drafted"
    if result.get("clicked_apply"):
        return "opened_apply_no_form", "needs_review"
    return "no_apply_form", "needs_review"


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
        _state["current_job"] = ""

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
        if not companies and not queued and not job_ids:
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
                kept = 0
                for job in jobs:
                    if _state["stop"]:
                        break
                    if not is_job_posting_url(job["url"]):
                        continue
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
                        status = "queued" if score >= float(settings.get("min_score") or 35) else "skipped"
                        conn.execute(
                            """INSERT INTO jobs (company_id, company_name, title, url, location, description, match_score, status)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                company["id"],
                                name,
                                job["title"],
                                job["url"],
                                job.get("location") or "",
                                job.get("description") or "",
                                score,
                                status,
                            ),
                        )
                        discovered += 1
                        kept += 1
                    conn.commit()
                log(conn, f"{name}: kept {kept} job links from {len(jobs)} harvested")

            settings = get_settings(conn)
            min_score = float(settings.get("min_score") or 35)
            max_night = int(settings.get("max_per_night") or 8)
            applied_tonight = int(settings.get("applied_tonight") or 0)
            dry_run = bool(settings.get("dry_run"))
            auto_submit = bool(settings.get("auto_submit")) and not dry_run
            remaining = max(0, max_night - applied_tonight)
            ingest_apply = bool(skip_discover or job_ids)
            if remaining <= 0:
                remaining = max(len(job_ids or []), 5)
            if ingest_apply:
                dry_run = False
                auto_submit = True
                remaining = max(remaining, len(job_ids or []) or 5)
                if job_ids:
                    placeholders = ",".join("?" * len(job_ids))
                    candidates = fetch_all(
                        conn,
                        f"SELECT * FROM jobs WHERE id IN ({placeholders}) AND status IN ('queued', 'new', 'needs_review', 'drafted') ORDER BY id DESC",
                        tuple(job_ids),
                    )
                else:
                    candidates = fetch_all(
                        conn,
                        """SELECT * FROM jobs
                           WHERE status IN ('queued', 'new')
                             AND company_name IN ('ChatGPT', 'Pasted')
                           ORDER BY id DESC
                           LIMIT ?""",
                        (remaining,),
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
                if remaining <= 0 and not ingest_apply:
                    log(conn, "Nightly apply cap reached")
                    break
                _state["current_job"] = job.get("url") or ""
                log(
                    conn,
                    f"Applying to {job['title']} @ {job['company_name']} ({job['match_score']}) {job['url']}",
                )
                conn.execute(
                    "INSERT INTO applications (job_id, status, notes) VALUES (?, 'started', ?)",
                    (job["id"], f"browser apply portal={portal_name(job['url'])}"),
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
                    page_title = _title_from_page(job, result)
                    company_name = job.get("company_name") or ""
                    if company_name in ("ChatGPT", "Pasted", "") and result.get("company_guess"):
                        company_name = result["company_guess"]
                    conn.execute(
                        "UPDATE jobs SET title=?, company_name=? WHERE id=?",
                        (page_title[:160], company_name[:120], job["id"]),
                    )
                    status, job_status = _job_status_from_result(result, auto_submit)
                    if dry_run and job_status == "applied":
                        status, job_status = "drafted", "drafted"
                    notes = (
                        f"portal={result.get('portal')} title={page_title} filled={result['fields_filled']} "
                        f"uploaded={result['resume_uploaded']} submitted={result['submitted']} "
                        f"clicked_apply={result['clicked_apply']} block={result.get('block') or ''} "
                        f"dry_run={dry_run} url={result['final_url']} {result.get('notes') or ''}"
                    )
                    conn.execute(
                        """UPDATE applications SET status=?, notes=?, screenshot_path=?, finished_at=datetime('now')
                           WHERE id=?""",
                        (status, notes[:2000], result.get("screenshot_path") or "", app_id),
                    )
                    conn.execute(
                        "UPDATE jobs SET status=?, updated_at=datetime('now') WHERE id=?",
                        (job_status, job["id"]),
                    )
                    if job_status in ("applied", "drafted"):
                        conn.execute(
                            "UPDATE settings SET applied_tonight = applied_tonight + 1 WHERE id = 1"
                        )
                    conn.commit()
                    applied += 1
                    remaining -= 1
                    log(conn, f"Result {page_title}: {status} / {job_status}")
                except Exception as exc:
                    conn.execute(
                        """UPDATE applications SET status='failed', notes=?, finished_at=datetime('now') WHERE id=?""",
                        (str(exc)[:1000], app_id),
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
        _state["current_job"] = ""
        log(conn, f"Cycle done. discovered={discovered} applied={applied}")
        return {"ok": True, "discovered": discovered, "applied": applied}
    except Exception as exc:
        _state["last_error"] = str(exc)
        _state["phase"] = "error"
        log(conn, f"Cycle crashed: {exc}", "error")
        return {"ok": False, "error": str(exc)}
    finally:
        _state["running"] = False
        _state["current_job"] = ""
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
