import json
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.agent.runner import NightShift, cycle_once, request_stop, state
from backend import ingest as ingest_mod
from backend.db import (
    UPLOADS,
    SHOTS,
    connect,
    fetch_all,
    fetch_one,
    get_profile,
    get_settings,
    init_db,
    log,
)

app = FastAPI(title="Nightshift Job Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

conn = connect()
init_db(conn)
nightshift = NightShift(conn)

FRONTEND = Path("/workspace/frontend")
if FRONTEND.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND / "assets")), name="assets")


class CompanyIn(BaseModel):
    name: str
    career_url: str
    enabled: bool = True
    notes: str = ""


class CompanyPatch(BaseModel):
    name: str | None = None
    career_url: str | None = None
    enabled: bool | None = None
    notes: str | None = None


class SettingsIn(BaseModel):
    overnight_enabled: bool | None = None
    auto_submit: bool | None = None
    dry_run: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=5, le=180)
    min_score: float | None = Field(default=None, ge=0, le=100)
    max_per_night: int | None = Field(default=None, ge=1, le=50)


class ProfileIn(BaseModel):
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin: str | None = None
    github: str | None = None
    website: str | None = None
    location: str | None = None
    skills: str | None = None
    summary: str | None = None
    answers: dict | None = None


class PastedJobsIn(BaseModel):
    text: str
    apply_now: bool = False


@app.on_event("startup")
def startup():
    ingest_mod.ensure_token(conn)
    nightshift.start_loop()
    log(conn, "Nightshift API started on this VM")


@app.get("/api/health")
def health():
    return {"ok": True, "agent": state()}


@app.get("/api/profile")
def profile():
    return get_profile(conn)


@app.post("/api/profile")
def save_profile(body: ProfileIn):
    current = get_profile(conn)
    data = body.model_dump(exclude_none=True)
    answers = data.pop("answers", None)
    if answers is not None:
        current["answers_json"] = json.dumps(answers)
    for k, v in data.items():
        current[k] = v
    conn.execute(
        """UPDATE profile SET full_name=?, email=?, phone=?, linkedin=?, github=?, website=?,
           location=?, skills=?, summary=?, answers_json=? WHERE id=1""",
        (
            current.get("full_name") or "",
            current.get("email") or "",
            current.get("phone") or "",
            current.get("linkedin") or "",
            current.get("github") or "",
            current.get("website") or "",
            current.get("location") or "",
            current.get("skills") or "",
            current.get("summary") or "",
            current.get("answers_json") or "{}",
        ),
    )
    conn.commit()
    log(conn, "Profile updated")
    return get_profile(conn)


@app.post("/api/profile/resume")
async def upload_resume(file: UploadFile = File(...)):
    suffix = Path(file.filename or "resume.pdf").suffix.lower()
    if suffix not in {".pdf", ".doc", ".docx", ".txt"}:
        raise HTTPException(400, "Resume must be pdf, doc, docx, or txt")
    dest = UPLOADS / f"resume{suffix}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    conn.execute("UPDATE profile SET resume_path=? WHERE id=1", (str(dest),))
    conn.commit()
    log(conn, f"Resume uploaded: {dest.name}")
    return get_profile(conn)


@app.get("/api/settings")
def settings():
    s = get_settings(conn)
    s.pop("ingest_token", None)
    s["agent"] = state()
    return s


@app.post("/api/settings")
def save_settings(body: SettingsIn):
    current = get_settings(conn)
    data = body.model_dump(exclude_none=True)
    for k, v in data.items():
        if isinstance(v, bool):
            current[k] = 1 if v else 0
        else:
            current[k] = v
    conn.execute(
        """UPDATE settings SET overnight_enabled=?, auto_submit=?, dry_run=?,
           interval_minutes=?, min_score=?, max_per_night=? WHERE id=1""",
        (
            int(current["overnight_enabled"]),
            int(current["auto_submit"]),
            int(current["dry_run"]),
            int(current["interval_minutes"]),
            float(current["min_score"]),
            int(current["max_per_night"]),
        ),
    )
    conn.commit()
    log(conn, "Settings updated")
    out = get_settings(conn)
    out["agent"] = state()
    return out


@app.get("/api/companies")
def companies():
    return fetch_all(conn, "SELECT * FROM companies ORDER BY id DESC")


@app.post("/api/companies")
def add_company(body: CompanyIn):
    conn.execute(
        "INSERT INTO companies (name, career_url, enabled, notes) VALUES (?, ?, ?, ?)",
        (body.name.strip(), body.career_url.strip(), 1 if body.enabled else 0, body.notes),
    )
    conn.commit()
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    log(conn, f"Company added: {body.name}")
    return fetch_one(conn, "SELECT * FROM companies WHERE id=?", (cid,))


@app.patch("/api/companies/{cid}")
def patch_company(cid: int, body: CompanyPatch):
    row = fetch_one(conn, "SELECT * FROM companies WHERE id=?", (cid,))
    if not row:
        raise HTTPException(404, "Company not found")
    data = body.model_dump(exclude_none=True)
    for k, v in data.items():
        if k == "enabled":
            row[k] = 1 if v else 0
        else:
            row[k] = v
    conn.execute(
        "UPDATE companies SET name=?, career_url=?, enabled=?, notes=? WHERE id=?",
        (row["name"], row["career_url"], row["enabled"], row["notes"], cid),
    )
    conn.commit()
    return fetch_one(conn, "SELECT * FROM companies WHERE id=?", (cid,))


@app.post("/api/jobs/paste")
def paste_jobs(body: PastedJobsIn):
    urls = ingest_mod.extract_urls(body.text or "")
    added, skipped = ingest_mod.queue_urls(conn, urls, source="Pasted")
    started = False
    if body.apply_now and added:
        started = ingest_mod.start_apply(conn, job_ids=[row["id"] for row in added])
    return {"ok": True, "added": added, "skipped": skipped, "started": started}


@app.get("/api/ingest")
def ingest_info():
    token = ingest_mod.ensure_token(conn)
    return {
        "ok": True,
        "token_suffix": token[-6:],
        "path": f"/in/{token}",
        "get_example": f"/in/{token}?url=https://boards.greenhouse.io/acme/jobs/123",
        "post_example": {"url": "https://jobs.lever.co/acme/abc", "title": "SDET", "company": "Acme"},
    }


@app.post("/api/ingest/rotate")
def ingest_rotate():
    token = ingest_mod.rotate_token(conn)
    return {"ok": True, "token_suffix": token[-6:], "path": f"/in/{token}"}


def _ingest_request(token: str, urls, extra=None):
    if not ingest_mod.token_ok(conn, token):
        raise HTTPException(404, "Not found")
    if not ingest_mod.rate_ok(token):
        raise HTTPException(429, "Too many ingest requests")
    if not urls:
        return {"ok": False, "error": "no_job_url", "added": [], "skipped": [], "started": False}
    added, skipped = ingest_mod.queue_urls(conn, urls, source="ChatGPT", extra=extra)
    started = False
    if added:
        started = ingest_mod.start_apply(conn, job_ids=[row["id"] for row in added])
    return {"ok": True, "queued": len(added), "added": added, "skipped": skipped, "started": started}


@app.api_route("/in/{token}", methods=["GET", "POST"])
async def ingest_hook(token: str, request: Request, url: str | None = None, u: str | None = None, text: str | None = None):
    extra = {}
    urls = []
    if url:
        urls.extend(ingest_mod.extract_urls(url))
    if u:
        urls.extend(ingest_mod.extract_urls(u))
    if text:
        urls.extend(ingest_mod.extract_urls(text))
    if request.method == "POST":
        ctype = (request.headers.get("content-type") or "").lower()
        try:
            if "application/json" in ctype:
                body = await request.json()
                more, extra = ingest_mod.payload_urls(body)
                urls.extend(more)
            else:
                raw = (await request.body()).decode("utf-8", "ignore")
                urls.extend(ingest_mod.extract_urls(raw))
        except Exception:
            pass
    result = _ingest_request(token, urls, extra)
    if request.method == "GET" and request.headers.get("accept", "").find("text/html") >= 0:
        return JSONResponse(result)
    return result


@app.post("/api/jobs/{jid}/status")
def set_job_status(jid: int, status: str = Form(...)):
    allowed = {
        "new",
        "queued",
        "skipped",
        "drafted",
        "needs_review",
        "applied",
        "failed",
        "applying",
    }
    if status not in allowed:
        raise HTTPException(400, "Invalid status")
    row = fetch_one(conn, "SELECT id FROM jobs WHERE id=?", (jid,))
    if not row:
        raise HTTPException(404, "Job not found")
    conn.execute(
        "UPDATE jobs SET status=?, updated_at=datetime('now') WHERE id=?",
        (status, jid),
    )
    conn.commit()
    return fetch_one(conn, "SELECT * FROM jobs WHERE id=?", (jid,))


@app.get("/api/jobs")
def jobs(status: str | None = None, limit: int = 80):
    limit = min(max(limit, 1), 200)
    if status:
        return fetch_all(
            conn,
            "SELECT * FROM jobs WHERE status=? ORDER BY match_score DESC, id DESC LIMIT ?",
            (status, limit),
        )
    return fetch_all(
        conn, "SELECT * FROM jobs ORDER BY match_score DESC, id DESC LIMIT ?", (limit,)
    )


@app.get("/api/applications")
def applications(limit: int = 50):
    limit = min(max(limit, 1), 200)
    return fetch_all(
        conn,
        """SELECT a.*, j.title, j.company_name, j.url, j.match_score
           FROM applications a JOIN jobs j ON j.id = a.job_id
           ORDER BY a.id DESC LIMIT ?""",
        (limit,),
    )


@app.get("/api/logs")
def logs(limit: int = 80):
    limit = min(max(limit, 1), 300)
    return fetch_all(conn, "SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,))


@app.post("/api/run")
def run_now():
    if state()["running"]:
        raise HTTPException(409, "Agent already running")

    def _go():
        cycle_once(conn, headless=True)

    from threading import Thread

    Thread(target=_go, daemon=True).start()
    return {"ok": True, "started": True, "agent": state()}


@app.post("/api/stop")
def stop_now():
    request_stop()
    return {"ok": True, "agent": state()}


@app.get("/api/screenshot/{job_id}")
def screenshot(job_id: int):
    path = SHOTS / f"job-{job_id}.png"
    if not path.exists():
        raise HTTPException(404, "No screenshot")
    return FileResponse(str(path), media_type="image/png")


@app.get("/")
def index():
    index_path = FRONTEND / "index.html"
    if not index_path.exists():
        return {"ok": True, "message": "UI missing"}
    return FileResponse(str(index_path))
