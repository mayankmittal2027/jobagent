import json
import sqlite3
from pathlib import Path

DB_PATH = Path("/workspace/data/nightshift.db")
UPLOADS = Path("/workspace/data/uploads")
SHOTS = Path("/workspace/data/screenshots")


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    SHOTS.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            full_name TEXT DEFAULT '',
            email TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            linkedin TEXT DEFAULT '',
            github TEXT DEFAULT '',
            website TEXT DEFAULT '',
            location TEXT DEFAULT '',
            skills TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            resume_path TEXT DEFAULT '',
            answers_json TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            career_url TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            notes TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER,
            company_name TEXT DEFAULT '',
            title TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            location TEXT DEFAULT '',
            description TEXT DEFAULT '',
            match_score REAL DEFAULT 0,
            status TEXT DEFAULT 'new',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (company_id) REFERENCES companies(id)
        );

        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            status TEXT DEFAULT 'started',
            notes TEXT DEFAULT '',
            screenshot_path TEXT DEFAULT '',
            started_at TEXT DEFAULT (datetime('now')),
            finished_at TEXT,
            FOREIGN KEY (job_id) REFERENCES jobs(id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            overnight_enabled INTEGER DEFAULT 0,
            auto_submit INTEGER DEFAULT 0,
            dry_run INTEGER DEFAULT 1,
            interval_minutes INTEGER DEFAULT 20,
            min_score REAL DEFAULT 35,
            max_per_night INTEGER DEFAULT 8,
            applied_tonight INTEGER DEFAULT 0,
            night_date TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (datetime('now')),
            level TEXT DEFAULT 'info',
            message TEXT NOT NULL
        );
        """
    )
    conn.execute("INSERT OR IGNORE INTO profile (id) VALUES (1)")
    conn.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(settings)").fetchall()}
    if "ingest_token" not in cols:
        conn.execute("ALTER TABLE settings ADD COLUMN ingest_token TEXT DEFAULT ''")
    conn.commit()


def row_to_dict(row):
    if row is None:
        return None
    return dict(row)


def fetch_one(conn, sql, params=()):
    return row_to_dict(conn.execute(sql, params).fetchone())


def fetch_all(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def log(conn, message, level="info"):
    conn.execute(
        "INSERT INTO logs (level, message) VALUES (?, ?)", (level, message)
    )
    conn.commit()


def get_profile(conn):
    p = fetch_one(conn, "SELECT * FROM profile WHERE id = 1")
    try:
        p["answers"] = json.loads(p.get("answers_json") or "{}")
    except json.JSONDecodeError:
        p["answers"] = {}
    return p


def get_settings(conn):
    return fetch_one(conn, "SELECT * FROM settings WHERE id = 1")
