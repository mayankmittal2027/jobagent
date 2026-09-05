import hashlib
import hmac
import re
import secrets
import time
from threading import Thread
from urllib.parse import unquote, urlparse

from backend.agent.portals import company_from_url, is_job_posting_url
from backend.agent.runner import cycle_once, state
from backend.db import fetch_one, get_profile, get_settings, log
from backend.matcher import score_job

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
_hits = []


def new_token():
    return secrets.token_urlsafe(24)


def ensure_token(conn):
    settings = get_settings(conn)
    token = (settings.get("ingest_token") or "").strip()
    if not token:
        token = new_token()
        conn.execute("UPDATE settings SET ingest_token=? WHERE id=1", (token,))
        conn.commit()
    return token


def rotate_token(conn):
    token = new_token()
    conn.execute("UPDATE settings SET ingest_token=? WHERE id=1", (token,))
    conn.commit()
    log(conn, "Ingest token rotated")
    return token


def token_ok(conn, given: str) -> bool:
    expected = (get_settings(conn).get("ingest_token") or "").strip()
    if not expected or not given:
        return False
    return hmac.compare_digest(given, expected)


def rate_ok(token: str, limit=60, window=600) -> bool:
    now = time.time()
    key = hashlib.sha256(token.encode()).hexdigest()[:16]
    while _hits and _hits[0][0] < now - window:
        _hits.pop(0)
    count = sum(1 for ts, k in _hits if k == key)
    if count >= limit:
        return False
    _hits.append((now, key))
    return True


def _clean_url(raw: str) -> str:
    url = unquote((raw or "").strip())
    url = url.rstrip(").,]\"'")
    url = url.split("#")[0]
    if url.startswith("http://"):
        parsed = urlparse(url)
        if parsed.netloc:
            url = "https://" + url[len("http://") :]
    return url


def extract_urls(text: str):
    urls = []
    seen = set()
    blob = unquote(text or "")
    for raw in URL_RE.findall(blob):
        url = _clean_url(raw)
        if not url.startswith("http"):
            continue
        key = url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        urls.append(url)
    if not urls:
        maybe = _clean_url(blob)
        if maybe.startswith("http"):
            urls.append(maybe)
    return urls


def host_name(url: str) -> str:
    host = urlparse(url).netloc.replace("www.", "")
    return host.split(":")[0] or "Pasted"


DONE = {"applied", "applying", "submitted", "queued"}


def queue_urls(conn, urls, source="ingest", extra=None):
    extra = extra or {}
    profile = get_profile(conn)
    added = []
    skipped = []
    for url in urls:
        url = _clean_url(url)
        if not url.startswith("http"):
            skipped.append(url)
            continue
        existing = fetch_one(conn, "SELECT * FROM jobs WHERE url = ?", (url,))
        if not existing:
            existing = fetch_one(
                conn, "SELECT * FROM jobs WHERE rtrim(url, '/') = ?", (url.rstrip("/"),)
            )
        host = host_name(url)
        guessed_company = extra.get("company") or company_from_url(url)
        if guessed_company in ("ChatGPT", "Pasted", source, ""):
            guessed_company = company_from_url(url) or source
        title = extra.get("title") or (existing.get("title") if existing else "")
        if not title or title.startswith("Ingested role") or title.startswith("Pasted role"):
            title = extra.get("title") or f"Queued role @ {host}"
        location = extra.get("location") or ((existing or {}).get("location") or "")
        description = extra.get("description") or ((existing or {}).get("description") or url)
        score = score_job(profile, title, description, location)
        if not extra.get("title") and is_job_posting_url(url):
            score = max(score, 70.0)
        if existing:
            if existing.get("status") in DONE:
                skipped.append(url)
                continue
            conn.execute(
                """UPDATE jobs SET company_name=?, title=?, location=?, description=?, match_score=?,
                   status='queued', updated_at=datetime('now') WHERE id=?""",
                (
                    guessed_company[:120],
                    (title or existing["title"])[:160],
                    location[:120],
                    description[:4000],
                    max(score, 70.0),
                    existing["id"],
                ),
            )
            conn.commit()
            added.append({"id": existing["id"], "url": url, "requeued": True})
            continue
        conn.execute(
            """INSERT INTO jobs (company_id, company_name, title, url, location, description, match_score, status)
               VALUES (NULL, ?, ?, ?, ?, ?, ?, 'queued')""",
            (
                guessed_company[:120],
                title[:160],
                url,
                location[:120],
                description[:4000],
                max(score, 70.0),
            ),
        )
        conn.commit()
        added.append({"id": conn.execute("SELECT last_insert_rowid()").fetchone()[0], "url": url})
    if added:
        log(conn, f"{source}: queued {len(added)} jobs ({len(skipped)} already applied)")
    return added, skipped


def start_apply(conn, job_ids=None):
    ids = list(job_ids or [])

    def _go():
        for _ in range(90):
            if not state()["running"]:
                cycle_once(conn, headless=True, skip_discover=True, job_ids=ids)
                return
            time.sleep(2)
        log(conn, "Ingest apply waited too long; agent still running", "warn")

    Thread(target=_go, daemon=True).start()
    return True


def payload_urls(payload) -> tuple[list[str], dict]:
    extra = {}
    urls = []
    if payload is None:
        return urls, extra
    if isinstance(payload, str):
        return extract_urls(payload), extra
    if not isinstance(payload, dict):
        return urls, extra
    extra = {
        "title": str(payload.get("title") or payload.get("job_title") or ""),
        "company": str(payload.get("company") or payload.get("company_name") or ""),
        "location": str(payload.get("location") or ""),
        "description": str(payload.get("description") or payload.get("notes") or ""),
    }
    for key in ("url", "job_url", "link", "job"):
        if isinstance(payload.get(key), str):
            urls.extend(extract_urls(payload[key]))
    for key in ("urls", "links", "jobs"):
        val = payload.get(key)
        if isinstance(val, str):
            urls.extend(extract_urls(val))
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    urls.extend(extract_urls(item))
                elif isinstance(item, dict):
                    nested, nested_extra = payload_urls(item)
                    urls.extend(nested)
                    if nested_extra.get("title") and not extra.get("title"):
                        extra.update({k: v for k, v in nested_extra.items() if v})
    if payload.get("text"):
        urls.extend(extract_urls(str(payload["text"])))
    seen = []
    out = []
    for u in urls:
        if u not in seen:
            seen.append(u)
            out.append(u)
    return out, extra
