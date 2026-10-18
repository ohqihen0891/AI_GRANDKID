"""The child's chat with AI GrandKid. The AI also posts updates here on its own
(morning brief, alerts, missed medicine, new family-book pages)."""
from . import db


def post(parent_id: int, role: str, content: str, card: dict = None) -> int:
    return db.x("INSERT INTO chat(parent_id, role, content, card, created_at) VALUES(?,?,?,?,?)",
                (parent_id, role, content, db.jdump(card) if card else None, db.now_iso()))


def recent(parent_id: int, limit: int = 12) -> list:
    rows = db.q("SELECT role, content FROM chat WHERE parent_id=? ORDER BY id DESC LIMIT ?", (parent_id, limit))
    return list(reversed(rows))
