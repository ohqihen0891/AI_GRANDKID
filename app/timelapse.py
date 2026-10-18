"""Demo time-lapse.

In real use the scheduler carries out the care plan as the hours pass. For a 3-minute demo we
can't wait, so the app's clock JUMPS to the moment each thing should happen:

- when the child confirms a plan (or a message / family-book question is planned), the clock
  jumps to its first time and it happens right away on Mum's phone, stamped with that time;
- "Next event" jumps to whatever comes next in Mum's day (tomorrow's check-in, a follow-up...);
- "Morning brief" jumps to the child's next morning and writes the brief.
"""
from datetime import datetime, timedelta, timezone

from . import agents, care, clock, db
from .config import S


def _slot_dt(slot: str) -> datetime:
    return clock.slot_to_utc(slot[:16])


def next_slot(task, after: datetime, any_time: bool = False):
    """Next time (UTC) this task should run strictly after `after`, with its slot key."""
    sched = task["schedule"]
    t = sched.get("type")
    if t in ("daily", "weekly"):
        hh, mm = map(int, sched["time"].split(":"))
        base = after.astimezone(clock.PTZ)
        for d in range(0, 15):
            day = (base + timedelta(days=d)).replace(hour=hh, minute=mm, second=0, microsecond=0)
            if day <= base:
                continue
            if t == "weekly" and day.weekday() not in sched.get("days", []):
                continue
            slot = day.strftime("%Y-%m-%d %H:%M")
            if not db.one("SELECT id FROM care_runs WHERE task_id=? AND slot=?", (task["id"], slot)):
                return day.astimezone(timezone.utc), slot
    elif t == "times":
        for slot in sorted(sched.get("at", [])):
            if db.one("SELECT id FROM care_runs WHERE task_id=? AND slot=?", (task["id"], slot)):
                continue
            dt = _slot_dt(slot)
            if any_time or dt > after:
                return dt, slot
    return None, None


def run_now(task):
    """Jump the clock to this task's next time and carry it out (instant demo mode)."""
    if not S.DEMO_INSTANT or task["status"] != "active":
        return None
    now = clock.now_utc()
    dt, slot = next_slot(task, now, any_time=True)
    if task["schedule"].get("type") in ("daily", "weekly"):
        dt, slot = next_slot(task, now - timedelta(minutes=1))   # today's time if it hasn't passed, else tomorrow
    if not dt:
        return None
    clock.set_to(dt + timedelta(seconds=5))   # jump to that moment (even a date you picked in the past)
    return care.execute(task, slot)


def upcoming(limit: int = 1) -> list:
    """What happens next in Mum's day, for the presenter panel."""
    now = clock.now_utc()
    items = []
    for row in db.q("SELECT id FROM care_tasks WHERE status='active'"):
        t = care.get_task(row["id"])
        dt, slot = next_slot(t, now)
        if dt and dt > now:
            items.append({"at": dt, "label": f"{care.ICONS.get(t['kind'], '•')} {t['title']}", "task_id": t["id"], "slot": slot})
    for r in db.q("SELECT r.*, t.title, t.kind FROM care_runs r JOIN care_tasks t ON t.id=r.task_id "
                  "WHERE r.status IN ('sent','followed_up') AND t.kind='medication'"):
        base = r["followup_at"] if r["status"] == "followed_up" else r["sent_at"]
        dt = clock.parse_iso(base) + timedelta(minutes=care.MED_FOLLOWUP_MIN, seconds=5)
        label = "⏳ Second reminder" if r["status"] == "sent" else "❌ Mark as missed"
        items.append({"at": dt, "label": f"{label}: {r['title']}", "followup": True})
    items.sort(key=lambda i: i["at"])
    tz = S.CHILD_TZ
    return [{**i, "at": i["at"].isoformat(timespec="seconds"),
             "when": i["at"].astimezone(clock.PTZ).strftime("%a %d %b · ") + clock.fmt_both(i["at"], tz)} for i in items[:limit]]


def next_event():
    nxt = upcoming(1)
    if not nxt:
        return None
    e = nxt[0]
    clock.set_to(clock.parse_iso(e["at"]))
    if e.get("followup"):
        care.follow_ups()
    else:
        t = care.get_task(e["task_id"])
        care.execute(t, e["slot"])
        care.tick()
    return e


def morning_brief(parent_id: int = 1):
    """Jump to the child's next morning and write the brief."""
    p = db.get_parent(parent_id)
    now = clock.child_now(p["child_tz"])
    hh, mm = map(int, S.BRIEF_TIME.split(":"))
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    clock.set_to(target.astimezone(timezone.utc))
    db.kv_set(f"brief:{p['id']}", target.strftime("%Y-%m-%d"))
    care.follow_ups()
    return agents.generate_brief(p)
