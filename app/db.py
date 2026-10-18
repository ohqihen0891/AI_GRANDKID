"""Tiny SQLite layer. One file database at data/aigrandkid.db."""
import json
import os
import sqlite3
import threading
from datetime import timedelta

from . import clock
from .config import S

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS parents(
  id INTEGER PRIMARY KEY, name TEXT, nickname TEXT, wa_id TEXT UNIQUE, channel TEXT,
  child_name TEXT, child_name_zh TEXT, child_city TEXT, child_tz TEXT, created_at TEXT);

-- Mum's side: WhatsApp / simulator conversation
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY, parent_id INT, direction TEXT, sender TEXT, type TEXT,
  content TEXT, media_path TEXT, meta TEXT, wa_msg_id TEXT, created_at TEXT);

-- Child's side: chat with the AI care coordinator
CREATE TABLE IF NOT EXISTS chat(
  id INTEGER PRIMARY KEY, parent_id INT, role TEXT, content TEXT, card TEXT, created_at TEXT);

-- Care tasks the child asked for, and every time the AI carried one out
CREATE TABLE IF NOT EXISTS care_tasks(
  id INTEGER PRIMARY KEY, parent_id INT, kind TEXT, title TEXT, title_zh TEXT,
  schedule TEXT, details TEXT, voice_path TEXT, status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS care_runs(
  id INTEGER PRIMARY KEY, task_id INT, parent_id INT, slot TEXT, status TEXT,
  sent_at TEXT, followup_at TEXT, responded_at TEXT, outcome TEXT, reply TEXT, ref INT,
  UNIQUE(task_id, slot));

-- What the AI knows and notices
CREATE TABLE IF NOT EXISTS signals(
  id INTEGER PRIMARY KEY, parent_id INT, category TEXT, value TEXT, detail TEXT,
  evidence TEXT, message_id INT, created_at TEXT);
CREATE TABLE IF NOT EXISTS facts(
  id INTEGER PRIMARY KEY, parent_id INT, content TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY, parent_id INT, kind TEXT, level INT, title TEXT, detail TEXT,
  evidence TEXT, actions TEXT, status TEXT DEFAULT 'open', created_at TEXT);
CREATE TABLE IF NOT EXISTS digests(
  id INTEGER PRIMARY KEY, parent_id INT, content TEXT, created_at TEXT);

-- Family book
CREATE TABLE IF NOT EXISTS book_chapters(
  id INTEGER PRIMARY KEY, parent_id INT, key TEXT, emoji TEXT, title TEXT, title_zh TEXT,
  description TEXT, active INT DEFAULT 1, sort INT);
CREATE TABLE IF NOT EXISTS book_questions(
  id INTEGER PRIMARY KEY, parent_id INT, chapter_id INT, question_zh TEXT, question_en TEXT,
  source TEXT, status TEXT DEFAULT 'pending', asked_at TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS book_entries(
  id INTEGER PRIMARY KEY, parent_id INT, chapter_id INT, question_id INT, title TEXT, title_zh TEXT,
  story_zh TEXT, story_en TEXT, quote TEXT, original TEXT, voice_path TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, parent_id INT, agent TEXT, action TEXT, detail TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS processed(wa_msg_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
"""


def now_iso() -> str:
    return clock.now_utc().isoformat(timespec="seconds")


def ago_iso(days: float = 0, hours: float = 0, minutes: float = 0) -> str:
    return (clock.now_utc() - timedelta(days=days, hours=hours, minutes=minutes)).isoformat(timespec="seconds")


def _connect():
    os.makedirs(os.path.dirname(S.DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(S.DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock:
        c = _connect()
        try:
            c.executescript(SCHEMA)
            c.commit()
        finally:
            c.close()
    os.makedirs(S.MEDIA_DIR, exist_ok=True)


def q(sql: str, params=()) -> list:
    c = _connect()
    try:
        return [dict(r) for r in c.execute(sql, params).fetchall()]
    finally:
        c.close()


def one(sql: str, params=()):
    rows = q(sql, params)
    return rows[0] if rows else None


def x(sql: str, params=()) -> int:
    with _lock:
        c = _connect()
        try:
            cur = c.execute(sql, params)
            c.commit()
            return cur.lastrowid
        finally:
            c.close()


def jdump(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def jload(v, default=None):
    if not v:
        return default
    try:
        return json.loads(v)
    except Exception:
        return default


def get_parent(pid: int):
    return one("SELECT * FROM parents WHERE id=?", (pid,))


def kv_get(k, default=None):
    r = one("SELECT v FROM kv WHERE k=?", (k,))
    return r["v"] if r else default


def kv_set(k, v):
    x("INSERT INTO kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
