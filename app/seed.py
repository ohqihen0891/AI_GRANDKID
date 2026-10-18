"""Fresh demo family.  Run:  python -m app.seed   (RESETS the database)

Everything starts EMPTY: Mum's WhatsApp, the child's chat, the care log and the book pages.
Only what the family filled in when they signed up is there: a few profile facts and the
family-book template (themed chapters with suggested questions).
"""
import os

from . import book, clock, db
from .config import S

PROFILE = [
    "Lives alone in Sham Shui Po, Hong Kong; her husband passed away in 2019",
    "Daughter Ka Yan (嘉欣) lives in London with grandson Ethan (8 years old)",
    "Likes tai chi in the park in the morning and cooking soup for the family",
    "Takes blood pressure medicine",
]


def seed():
    if os.path.exists(S.DB_PATH):
        os.remove(S.DB_PATH)
    clock.reset()
    if S.DEMO_INSTANT:
        # demo always starts the same way: Ka Yan's evening in London, early morning (asleep) for Mum in Hong Kong
        from datetime import timezone
        start = clock.parent_now().replace(hour=5, minute=0, second=0, microsecond=0)
        clock.set_to(start.astimezone(timezone.utc))
    db.init()
    wa = S.DEMO_PARENT_WA or "sim:mum"
    pid = db.x("INSERT INTO parents(name, nickname, wa_id, channel, child_name, child_name_zh, child_city, child_tz, created_at) "
               "VALUES(?,?,?,?,?,?,?,?,?)", (S.PARENT_NAME, S.PARENT_NICKNAME, wa, "whatsapp" if S.DEMO_PARENT_WA else "sim",
                                            S.CHILD_NAME, S.CHILD_NAME_ZH, S.CHILD_CITY, S.CHILD_TZ, db.now_iso()))
    for f in PROFILE:
        db.x("INSERT INTO facts(parent_id, content, created_at) VALUES(?,?,?)", (pid, f, db.now_iso()))
    book.seed_chapters(pid, S.CHILD_NAME_ZH)
    db.kv_set(f"brief:{pid}", clock.child_now(S.CHILD_TZ).strftime("%Y-%m-%d"))  # no brief until the next morning
    print(f"Fresh demo family ready (parent_id={pid}). Chat, care log and Mum's WhatsApp are empty.")
    return pid


if __name__ == "__main__":
    seed()
