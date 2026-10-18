"""Care tasks: what the child asked for, carried out by the AI in Mum's Hong Kong day.

Task kinds
  medication  daily/weekly reminder with "taken" buttons, follow-up, missed -> tell child
  checkin     AI starts a caring chat; Mum's reply becomes the outcome
  message     news / words / voice note from the child, delivered at a good HK time
  appointment reminders before a doctor visit, offers an escort
  reminder    any other reminder with an "OK" button
  book        family-book interview question (Legacy agent)

Schedules (all times in Mum's time zone)
  {"type": "daily",  "time": "17:00"}
  {"type": "weekly", "days": [6], "time": "15:00"}       (0 = Monday)
  {"type": "times",  "at": ["2026-10-24 08:30", ...]}    (one-off slots)
"""
import os
from datetime import datetime, timedelta

from . import channel, chatlog, clock, db, events
from .config import S

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
ICONS = {"medication": "💊", "checkin": "☀️", "message": "💌", "appointment": "🏥", "reminder": "⏰", "book": "📖"}
WAKE_START, WAKE_END = S.WAKE_START, S.WAKE_END
RUN_WINDOW_MIN = 60        # a daily slot can fire up to 60 min late (e.g. after a restart)
MED_FOLLOWUP_MIN = 30      # remind again if no "taken" after 30 min
MED_MISSED_MIN = 30        # then mark missed after another 30 min
REPLY_WAIT_HOURS = {"checkin": 3, "message": 3, "appointment": 3, "reminder": 3, "book": 24}


# ======================================================================= helpers
def describe_schedule(schedule: dict, child_tz: str = None) -> str:
    t = schedule.get("type")
    if t in ("daily", "weekly"):
        hh, mm = map(int, schedule["time"].split(":"))
        ref = clock.parent_now().replace(hour=hh, minute=mm, second=0, microsecond=0)
        both = clock.fmt_both(ref, child_tz)
        if t == "daily":
            return f"Every day · {both}"
        days = ", ".join(DAYS[d] for d in schedule.get("days", []))
        return f"Every {days} · {both}"
    if t == "times":
        parts = []
        for slot in schedule.get("at", []):
            dt = clock.slot_to_utc(slot)
            parts.append(f"{dt.astimezone(clock.PTZ).strftime('%a %d %b')} · {clock.fmt_both(dt, child_tz)}")
        return " / ".join(parts)
    return "—"


def in_waking_hours(dt_hk: datetime) -> bool:
    hm = dt_hk.strftime("%H:%M")
    return WAKE_START <= hm <= WAKE_END


def next_good_slot(min_delay_min: int = 2) -> tuple:
    """Next time that is kind to Mum: within her waking hours, outside quiet hours.
    Returns (slot 'YYYY-MM-DD HH:MM', reason)."""
    now = clock.parent_now() + timedelta(minutes=min_delay_min)
    if in_waking_hours(now):
        return now.strftime("%Y-%m-%d %H:%M"), "Mum is awake now, so it goes out right away"
    h, m = map(int, S.MORNING_DELIVERY.split(":"))
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target.strftime("%Y-%m-%d %H:%M"), "Mum is asleep, so I'll deliver it after her breakfast"


def get_task(tid: int):
    t = db.one("SELECT * FROM care_tasks WHERE id=?", (tid,))
    if t:
        t["schedule"] = db.jload(t["schedule"], {})
        t["details"] = db.jload(t["details"], {})
    return t


def tasks_for(pid: int, include_inactive=True) -> list:
    rows = db.q("SELECT * FROM care_tasks WHERE parent_id=? " +
                ("" if include_inactive else "AND status='active' ") + "ORDER BY id", (pid,))
    for t in rows:
        t["schedule"] = db.jload(t["schedule"], {})
        t["details"] = db.jload(t["details"], {})
    return rows


def create_task(parent, kind, title, title_zh, schedule, details=None, status="pending_confirm", voice_path=None):
    tid = db.x("INSERT INTO care_tasks(parent_id, kind, title, title_zh, schedule, details, voice_path, status, "
               "created_at) VALUES(?,?,?,?,?,?,?,?,?)",
               (parent["id"], kind, title, title_zh, db.jdump(schedule), db.jdump(details or {}),
                os.path.basename(voice_path) if voice_path else None, status, db.now_iso()))
    return tid


def set_status(tid: int, status: str):
    db.x("UPDATE care_tasks SET status=? WHERE id=?", (status, tid))


def update_task(tid: int, schedule=None, details=None, title=None, title_zh=None):
    t = get_task(tid)
    if not t:
        return
    sched = {**t["schedule"], **(schedule or {})} if schedule else t["schedule"]
    det = {**t["details"], **(details or {})}
    db.x("UPDATE care_tasks SET schedule=?, details=?, title=?, title_zh=? WHERE id=?",
         (db.jdump(sched), db.jdump(det), title or t["title"], title_zh or t["title_zh"], tid))


# ======================================================================= scheduling
def due_slots(task) -> list:
    now = clock.parent_now()
    sched = task["schedule"]
    slots = []
    if sched.get("type") in ("daily", "weekly"):
        if sched["type"] == "weekly" and now.weekday() not in sched.get("days", []):
            return []
        hh, mm = map(int, sched["time"].split(":"))
        start = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if start <= now < start + timedelta(minutes=RUN_WINDOW_MIN):
            slots.append(start.strftime("%Y-%m-%d %H:%M"))
    elif sched.get("type") == "times":
        for slot in sched.get("at", []):
            dt = clock.slot_to_utc(slot)
            if dt <= clock.now_utc() < dt + timedelta(hours=6):
                slots.append(slot)
    return [s for s in slots if not db.one("SELECT id FROM care_runs WHERE task_id=? AND slot=?", (task["id"], s))]


def tick():
    """Called every 20s by the scheduler (and by demo controls)."""
    for task in db.q("SELECT id FROM care_tasks WHERE status='active'"):
        t = get_task(task["id"])
        for slot in due_slots(t):
            execute(t, slot)
        if t["schedule"].get("type") == "times":
            done = all(db.one("SELECT id FROM care_runs WHERE task_id=? AND slot=?", (t["id"], s))
                       for s in t["schedule"].get("at", []))
            if done:
                set_status(t["id"], "done")
    follow_ups()


# ======================================================================= carrying out a task
def execute(task, slot: str = None):
    """Carry out one task now. Returns the run id."""
    parent = db.get_parent(task["parent_id"])
    if not parent:
        return None
    slot = slot or clock.parent_now().strftime("%Y-%m-%d %H:%M")
    if db.one("SELECT id FROM care_runs WHERE task_id=? AND slot=?", (task["id"], slot)):
        slot = clock.parent_now().strftime("%Y-%m-%d %H:%M:%S")  # manual re-run (demo)
    try:
        rid = db.x("INSERT INTO care_runs(task_id, parent_id, slot, status, sent_at) VALUES(?,?,?,?,?)",
                   (task["id"], parent["id"], slot, "sent", db.now_iso()))
    except Exception:
        return None  # another thread already carried out this slot
    kind, d, nick, child = task["kind"], task["details"], parent["nickname"], parent["child_name_zh"]
    pid = parent["id"]

    if kind == "medication":
        med = d.get("medicine_zh") or d.get("medicine") or "藥"
        if task.get("voice_path") and os.path.exists(os.path.join(S.MEDIA_DIR, task["voice_path"])):
            channel.send_audio(parent, os.path.join(S.MEDIA_DIR, task["voice_path"]), sender="child",
                               transcript=f"({child}嘅錄音提醒)")
            events.log(pid, "Health", f"Played {parent['child_name']}'s own voice reminder")
        note = f"（{d['note_zh']}）" if d.get("note_zh") else ""
        channel.send_buttons(parent, f"💊 {nick}，夠鐘食{med}喇！{child}叫我提你㗎～{note}食完㩒下面個掣話我知呀。",
                             [{"id": f"med_taken:{rid}", "title": "已食藥 ✅"},
                              {"id": f"med_snooze:{rid}", "title": "遲啲提我 ⏰"}])
        events.log(pid, "Health", "Medication reminder sent", f"{med} · {slot[-5:]} HK")

    elif kind == "checkin":
        from . import agents
        msg = agents.proactive_message(parent, "checkin", focus=d.get("focus", ""))
        events.log(pid, "Companion", "Daily check-in started", msg[:80])

    elif kind == "message":
        if task.get("voice_path") and os.path.exists(os.path.join(S.MEDIA_DIR, task["voice_path"])):
            channel.send_text(parent, f"💌 {nick}，{child}由{S.CHILD_CITY_ZH}錄咗段說話俾你：")
            channel.send_audio(parent, os.path.join(S.MEDIA_DIR, task["voice_path"]), sender="child",
                               transcript=d.get("message_zh") or None)
        else:
            channel.send_text(parent, d.get("message_zh") or f"💌 {child}話：{d.get('message_en', '')}")
        events.log(pid, "Family Bridge", f"Delivered {parent['child_name']}'s message at a good HK time",
                   d.get("message_en", "")[:80])

    elif kind == "appointment":
        what = d.get("what_zh") or "睇醫生"
        when = d.get("when_zh") or ""
        channel.send_buttons(parent, f"🏥 {nick}，提提你{when}要去{what}呀！{child}叫我記住提你。要唔要有人陪你去？",
                             [{"id": f"ack:{rid}", "title": "知道喇 👍"},
                              {"id": f"escort:{rid}", "title": "想有人陪我去"}])
        events.log(pid, "Health", "Appointment reminder sent", f"{what} {when}")

    elif kind == "reminder":
        channel.send_buttons(parent, f"⏰ {nick}，{d.get('text_zh') or task['title_zh'] or task['title']}",
                             [{"id": f"ack:{rid}", "title": "知道喇 👍"}])
        events.log(pid, "Companion", "Reminder sent", task["title"])

    elif kind == "book":
        from . import book
        q = book.ask_next(parent, rid, d.get("question_id"))
        if not q:
            db.x("UPDATE care_runs SET status='skipped', outcome=? WHERE id=?",
                 ("All family-book questions answered for now", rid))
    return rid


# ======================================================================= follow-ups and timeouts
def follow_ups():
    now = clock.now_utc()
    for r in db.q("SELECT r.*, t.kind FROM care_runs r JOIN care_tasks t ON t.id=r.task_id "
                  "WHERE r.status IN ('sent','followed_up')"):
        parent = db.get_parent(r["parent_id"])
        task = get_task(r["task_id"])
        if not parent or not task:
            continue
        sent = clock.parse_iso(r["sent_at"])
        if r["kind"] == "medication":
            med = task["details"].get("medicine_zh") or "藥"
            if r["status"] == "sent" and now >= sent + timedelta(minutes=MED_FOLLOWUP_MIN):
                db.x("UPDATE care_runs SET status='followed_up', followup_at=? WHERE id=?", (db.now_iso(), r["id"]))
                channel.send_buttons(parent, f"{parent['nickname']}，食咗{med}未呀？食咗記得㩒個掣話我知呀😊",
                                     [{"id": f"med_taken:{r['id']}", "title": "已食藥 ✅"}])
                events.log(parent["id"], "Health", "No reply yet, gentle second reminder sent", med)
            elif r["status"] == "followed_up" and now >= clock.parse_iso(r["followup_at"]) + timedelta(minutes=MED_MISSED_MIN):
                db.x("UPDATE care_runs SET status='missed', outcome=? WHERE id=?",
                     ("No reply after 2 reminders", r["id"]))
                events.log(parent["id"], "Health", "Medication marked as missed", med)
                recent = db.q("SELECT status FROM care_runs WHERE task_id=? ORDER BY id DESC LIMIT 2", (task["id"],))
                if len(recent) == 2 and all(x["status"] == "missed" for x in recent):
                    from . import agents
                    agents.create_alert(parent, "meds", 2, f"Mum missed {task['title']} twice in a row",
                                        "Two reminders in a row got no reply. It may be worth a call.",
                                        [], [{"id": "call", "label": "Call Mum"}])
                else:
                    chatlog.post(parent["id"], "ai",
                                 f"⚠️ Mum hasn't confirmed **{task['title']}** "
                                 f"({clock.fmt_both(r['sent_at'], parent['child_tz'])}). I reminded her twice. "
                                 f"I'll keep an eye on it and tell you if it happens again.")
        else:
            wait = REPLY_WAIT_HOURS.get(r["kind"], 3)
            if now >= sent + timedelta(hours=wait):
                status = "delivered" if r["kind"] == "message" else "no_reply"
                db.x("UPDATE care_runs SET status=?, outcome=? WHERE id=?",
                     (status, "Delivered, no reply" if status == "delivered" else f"No reply within {wait}h", r["id"]))
                if r["kind"] == "book" and r["ref"]:
                    db.x("UPDATE book_questions SET status='pending' WHERE id=?", (r["ref"],))
                elif r["kind"] == "checkin":
                    from . import agents
                    agents.create_alert(parent, "no_reply", 2, "Mum hasn't replied to today's check-in",
                                        f"I started a chat at {clock.fmt_both(r['sent_at'], parent['child_tz'])} and "
                                        f"haven't heard back for {wait} hours.", [],
                                        [{"id": "call", "label": "Call Mum"},
                                         {"id": "local_contact", "label": "Ask a relative to visit"}])


# ======================================================================= Mum's replies close the loop
def open_run(pid: int, kinds: tuple):
    marks = ",".join("?" * len(kinds))
    r = db.one(f"SELECT r.*, t.kind, t.title FROM care_runs r JOIN care_tasks t ON t.id=r.task_id "
               f"WHERE r.parent_id=? AND r.status IN ('sent','followed_up') AND t.kind IN ({marks}) "
               f"ORDER BY r.id DESC LIMIT 1", (pid, *kinds))
    if not r:
        return None
    wait = REPLY_WAIT_HOURS.get(r["kind"], 3) if r["kind"] != "medication" else 3
    if clock.now_utc() > clock.parse_iso(r["sent_at"]) + timedelta(hours=wait):
        return None
    return r


def link_reply(parent, text: str, analysis: dict, media_path: str = None) -> dict:
    """Attach Mum's message to whatever the AI last asked her. Returns context for the Companion."""
    pid = parent["id"]
    ctx = {}
    summary = (analysis.get("summary_en") or text)[:200]
    sigs = analysis.get("signals") or []

    # medicine confirmed in words ("食咗藥喇")
    if any(s.get("category") == "medication" and s.get("value") == "taken" for s in sigs):
        r = open_run(pid, ("medication",))
        if r:
            mark_taken(parent, r["id"], via="text reply")
            ctx["medication_confirmed"] = True

    r = open_run(pid, ("book", "checkin", "message", "appointment", "reminder"))
    if not r:
        return ctx
    if r["kind"] == "book":
        from . import book
        entry = book.save_answer(parent, r, text, media_path)
        db.x("UPDATE care_runs SET status='answered', responded_at=?, outcome=? WHERE id=?",
             (db.now_iso(), f"Mum shared a story: {entry['title'] if entry else summary}", r["id"]))
        ctx["book_answer"] = True
        return ctx
    db.x("UPDATE care_runs SET status='answered', responded_at=?, outcome=?, reply=? WHERE id=?",
         (db.now_iso(), summary, text[:500], r["id"]))
    events.log(pid, "Family Bridge", f"Mum replied to \"{r['title']}\"", summary)
    if r["kind"] == "message":
        chatlog.post(pid, "ai", f"💬 Mum replied to your message: “{text[:200]}”\n\n_{summary}_")
    ctx["answered"] = r["title"]
    return ctx


def mark_taken(parent, run_id: int, via="button"):
    r = db.one("SELECT r.*, t.title, t.details FROM care_runs r JOIN care_tasks t ON t.id=r.task_id WHERE r.id=?",
               (run_id,))
    if not r or r["status"] == "taken":
        return False
    late = clock.now_utc() - clock.parse_iso(r["sent_at"])
    mins = int(late.total_seconds() // 60)
    outcome = f"Taken at {clock.parent_now().strftime('%H:%M')} HK" + (f" ({mins} min after reminder)" if mins >= 5 else "")
    db.x("UPDATE care_runs SET status='taken', responded_at=?, outcome=? WHERE id=?", (db.now_iso(), outcome, run_id))
    db.x("INSERT INTO signals(parent_id, category, value, detail, evidence, created_at) VALUES(?,?,?,?,?,?)",
         (parent["id"], "medication", "taken", f"Took {r['title']}", "已食藥 ✅", db.now_iso()))
    events.log(parent["id"], "Health", f"Mum confirmed medicine ({via})", outcome)
    return True


def handle_button(parent, button_id: str):
    kind, _, ref = (button_id or "").partition(":")
    run = db.one("SELECT r.*, t.kind AS tkind, t.title FROM care_runs r JOIN care_tasks t ON t.id=r.task_id "
                 "WHERE r.id=?", (int(ref),)) if ref.isdigit() else None
    nick, child = parent["nickname"], parent["child_name_zh"]
    if kind == "med_taken" and run:
        if mark_taken(parent, run["id"]):
            channel.send_text(parent, f"好叻呀{nick}👍 我記低咗喇，{child}都會好放心！")
        else:
            channel.send_text(parent, f"收到呀{nick}，已經記低咗㗎喇😊")
    elif kind == "med_snooze" and run:
        # make the follow-up fire after SNOOZE_MINUTES instead of 30
        shifted = clock.now_utc() - timedelta(minutes=MED_FOLLOWUP_MIN - S.SNOOZE_MINUTES)
        db.x("UPDATE care_runs SET status='sent', sent_at=? WHERE id=?", (shifted.isoformat(timespec="seconds"), run["id"]))
        events.log(parent["id"], "Health", f"Mum asked to be reminded in {S.SNOOZE_MINUTES} min")
        channel.send_text(parent, f"冇問題，我{S.SNOOZE_MINUTES}分鐘之後再提你呀{nick}～")
    elif kind == "ack" and run:
        db.x("UPDATE care_runs SET status='answered', responded_at=?, outcome=? WHERE id=?",
             (db.now_iso(), "Mum acknowledged 👍", run["id"]))
        events.log(parent["id"], "Companion", "Mum acknowledged the reminder", run["title"])
        channel.send_text(parent, f"好呀{nick}😊 我會再提你㗎。")
    elif kind == "escort" and run:
        db.x("UPDATE care_runs SET status='answered', responded_at=?, outcome=? WHERE id=?",
             (db.now_iso(), "Mum would like someone to go with her", run["id"]))
        events.log(parent["id"], "Family Bridge", "Mum asked for an escort", run["title"])
        channel.send_text(parent, f"收到！我會話俾{child}知，佢會幫你安排有人陪你去呀{nick}，唔使擔心。")
        chatlog.post(parent["id"], "ai", f"🏥 Mum would like someone to go with her to **{run['title']}**. Could a relative "
                                         f"in Hong Kong go with her? Hospital escort (陪診) booking is coming soon in the Services tab.",
                     {"type": "service", "service": "Hospital escort (陪診)"})
    else:
        channel.send_text(parent, f"收到呀{nick}😊")


# ======================================================================= for the UI
def runs_for(task_id: int, limit: int = 8) -> list:
    return db.q("SELECT * FROM care_runs WHERE task_id=? ORDER BY id DESC LIMIT ?", (task_id, limit))


def task_view(t, child_tz):
    runs = runs_for(t["id"])
    week = db.q("SELECT status FROM care_runs WHERE task_id=? AND sent_at>=?", (t["id"], db.ago_iso(days=7)))
    ok = len([r for r in week if r["status"] in ("taken", "answered", "delivered")])
    return {**t, "icon": ICONS.get(t["kind"], "•"), "when": describe_schedule(t["schedule"], child_tz),
            "runs": [{**r, "when": clock.fmt_both(r["sent_at"], child_tz),
                      "date": clock.to_parent(r["sent_at"]).strftime("%d %b")} for r in runs],
            "week_ok": ok, "week_total": len(week)}
