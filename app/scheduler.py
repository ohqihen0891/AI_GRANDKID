"""Runs the care plan on its own: every 20 seconds it carries out due care tasks, sends follow-ups,
and once a day writes the morning brief at the child's local morning."""
import threading

from apscheduler.schedulers.background import BackgroundScheduler

from . import agents, care, clock, db
from .config import S

scheduler = BackgroundScheduler()
_lock = threading.Lock()


def tick():
    if not _lock.acquire(blocking=False):
        return
    try:
        care.tick()
        for p in ([] if S.DEMO_INSTANT else db.q("SELECT * FROM parents")):   # demo: brief comes from the button
            now = clock.child_now(p["child_tz"])
            key = f"brief:{p['id']}"
            if now.strftime("%H:%M") >= S.BRIEF_TIME and db.kv_get(key) != now.strftime("%Y-%m-%d"):
                db.kv_set(key, now.strftime("%Y-%m-%d"))
                agents.generate_brief(p)
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        _lock.release()


def start():
    if not scheduler.running:
        scheduler.add_job(tick, "interval", seconds=20, id="tick", replace_existing=True, max_instances=1)
        scheduler.start()
