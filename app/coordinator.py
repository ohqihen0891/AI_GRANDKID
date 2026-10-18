"""The child's chat: describe the care in your own words, AI GrandKid does the rest.

One LLM call reads the child's message plus everything we know, and returns a reply and a
list of actions (create / change care tasks, deliver a message at a good Hong Kong time,
add a family-book question, or say honestly that a service is "coming soon").
Recurring tasks wait for the child's Confirm.
"""
import re

from . import agents, book, care, chatlog, clock, db, events, llm, mock_ai, prompts, timelapse
from .config import S

KINDS = {"medication", "checkin", "appointment", "reminder", "book"}


def snapshot(parent) -> str:
    """Plain-text picture of how Mum is, so the AI can answer 'How is Mum?' honestly."""
    pid, tz = parent["id"], parent["child_tz"]
    since = db.ago_iso(days=3)
    lines = []
    last = db.one("SELECT content, created_at FROM messages WHERE parent_id=? AND sender='parent' ORDER BY id DESC LIMIT 1", (pid,))
    if last:
        lines.append(f"Mum's last message ({clock.fmt_both(last['created_at'], tz)}): {last['content'][:160]}")
    for r in db.q("SELECT r.*, t.title FROM care_runs r JOIN care_tasks t ON t.id=r.task_id WHERE r.parent_id=? "
                  "AND r.sent_at>=? ORDER BY r.id DESC LIMIT 12", (pid, since)):
        lines.append(f"- {clock.to_parent(r['sent_at']).strftime('%a %H:%M')} HK {r['title']}: {r['status']} {r['outcome'] or ''}")
    sig = db.q("SELECT category, value, evidence, created_at FROM signals WHERE parent_id=? AND created_at>=? ORDER BY id DESC LIMIT 15",
               (pid, db.ago_iso(days=7)))
    if sig:
        lines.append("Signals this week: " + "; ".join(f"{clock.to_parent(s['created_at']).strftime('%a')} {s['category']}={s['value']}" for s in sig))
    for a in db.q("SELECT title FROM alerts WHERE parent_id=? AND status='open' ORDER BY id DESC LIMIT 5", (pid,)):
        lines.append(f"Open alert: {a['title']}")
    facts = db.q("SELECT content FROM facts WHERE parent_id=? ORDER BY id DESC LIMIT 8", (pid,))
    if facts:
        lines.append("Facts: " + "; ".join(f["content"] for f in facts))
    pages = db.q("SELECT title FROM book_entries WHERE parent_id=? ORDER BY id DESC LIMIT 5", (pid,))
    if pages:
        lines.append("Family book pages: " + "; ".join(p["title"] for p in pages))
    return "\n".join(lines) or "Nothing yet."


def tasks_text(parent) -> str:
    rows = [t for t in care.tasks_for(parent["id"]) if t["status"] in ("active", "paused", "pending_confirm")]
    return "\n".join(f"{t['id']}: [{t['status']}] {t['kind']} '{t['title']}' {db.jdump(t['schedule'])} {db.jdump(t['details'])}"
                     for t in rows) or "(none)"


def handle_child_message(parent, text: str):
    pid = parent["id"]
    events.log(pid, "Orchestrator", f"{parent['child_name']} wrote in the chat", text[:80])
    if llm.is_mock():
        out = mock_ai.coordinate(text, parent, snapshot(parent), care.tasks_for(pid))
    else:
        hk, ch = clock.parent_now(), clock.child_now(parent["child_tz"])
        system = prompts.COORDINATOR_SYSTEM.format(
            child=parent["child_name"], city=parent["child_city"], child_tz=parent["child_tz"], name=parent["name"],
            nickname=parent["nickname"], hk_now=hk.strftime("%Y-%m-%d %H:%M"), hk_weekday=hk.strftime("%A"),
            child_now=ch.strftime("%Y-%m-%d %H:%M"), wake_start=S.WAKE_START, wake_end=S.WAKE_END,
            tasks=tasks_text(parent), snapshot=snapshot(parent), lang=S.CHILD_LANG)
        msgs = [{"role": "system", "content": system}]
        for m in chatlog.recent(pid, 10)[:-1]:
            if m["content"]:
                msgs.append({"role": "user" if m["role"] == "child" else "assistant", "content": m["content"][:600]})
        msgs.append({"role": "user", "content": text})
        try:
            out = llm.chat_json(msgs)
        except Exception as e:
            print("[coordinator] LLM failed, using rules:", e)
            out = mock_ai.coordinate(text, parent, snapshot(parent), care.tasks_for(pid))

    cards = []
    for action in out.get("actions") or []:
        try:
            card = apply_action(parent, action)
            if card:
                cards.append(card)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print("[coordinator] could not apply action", action, e)
    reply = (out.get("reply") or "").strip() or "Done."
    chatlog.post(pid, "ai", reply, {"cards": [{k: v for k, v in c.items() if k != "deliver"} for c in cards]} if cards else None)
    events.log(pid, "Orchestrator", "Replied to the family", reply[:80])
    for c in cards:  # messages and book questions go out at their planned time (instantly in demo mode)
        if c.get("deliver") and c.get("task_id"):
            deliver(c["task_id"])


def deliver(task_id: int):
    t = care.get_task(task_id)
    if not t:
        return
    if S.DEMO_INSTANT:
        timelapse.run_now(t)
    else:
        care.tick()


def handle_child_voice(parent, voice_path: str, text: str = ""):
    """Child recorded a voice note for Mum: deliver it at a good Hong Kong time."""
    slot, reason = care.next_good_slot()
    tid = care.create_task(parent, "message", "Your voice message", "語音訊息", {"type": "times", "at": [slot]},
                           {"message_en": text or "Voice message", "message_zh": text, "reason": reason},
                           status="active", voice_path=voice_path)
    when = clock.fmt_both(clock.slot_to_utc(slot), parent["child_tz"])
    chatlog.post(parent["id"], "ai", f"🎙 Got your voice message. I'll play it to Mum at **{when}**. {reason}.",
                 {"cards": [{"type": "planned_message", "task_id": tid}]})
    events.log(parent["id"], "Family Bridge", "Voice message planned for a good HK time", when)
    deliver(tid)


# ------------------------------------------------------------------ actions
def clean_time(t: str) -> str:
    m = re.match(r"^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*$", str(t or ""), re.I)
    if not m:
        raise ValueError(f"bad time {t}")
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return f"{h:02d}:{mi:02d}"


def clean_schedule(s: dict) -> dict:
    t = (s or {}).get("type")
    if t == "daily":
        return {"type": "daily", "time": clean_time(s["time"])}
    if t == "weekly":
        return {"type": "weekly", "days": sorted({int(d) % 7 for d in s.get("days", [])}) or [6], "time": clean_time(s["time"])}
    if t == "times":
        at = []
        for slot in s.get("at", []):
            d, _, tm = str(slot).strip().partition(" ")
            at.append(f"{d} {clean_time(tm or '09:00')}")
        return {"type": "times", "at": sorted(at)}
    raise ValueError(f"bad schedule {s}")


def apply_action(parent, a: dict):
    typ = a.get("type")
    pid = parent["id"]
    if typ == "create_task":
        t = a.get("task") or {}
        kind = t.get("kind") if t.get("kind") in KINDS else "reminder"
        sched = clean_schedule(t.get("schedule"))
        tid = care.create_task(parent, kind, t.get("title") or kind.capitalize(), t.get("title_zh") or "",
                               sched, t.get("details") or {}, status="pending_confirm")
        events.log(pid, "Orchestrator", "Proposed a care plan", t.get("title") or kind)
        return {"type": "task_proposal", "task_id": tid}
    if typ == "update_task":
        task = care.get_task(int(a["task_id"]))
        if not task or task["parent_id"] != pid:
            return None
        care.update_task(task["id"], schedule=clean_schedule({**task["schedule"], **a["schedule"]}) if a.get("schedule") else None,
                         details=a.get("details"))
        events.log(pid, "Orchestrator", "Updated a care task", task["title"])
        return {"type": "task_updated", "task_id": task["id"]}
    if typ in ("pause_task", "resume_task", "delete_task"):
        task = care.get_task(int(a["task_id"]))
        if not task or task["parent_id"] != pid:
            return None
        care.set_status(task["id"], {"pause_task": "paused", "resume_task": "active", "delete_task": "deleted"}[typ])
        events.log(pid, "Orchestrator", typ.replace("_", " ").capitalize(), task["title"])
        return {"type": "task_updated", "task_id": task["id"]}
    if typ == "send_message":
        slot, reason = care.next_good_slot()
        tid = care.create_task(parent, "message", (a.get("message_en") or "Message")[:60], "",
                               {"type": "times", "at": [slot]},
                               {"message_en": a.get("message_en", ""), "message_zh": a.get("message_zh", ""), "reason": reason},
                               status="active")
        events.log(pid, "Family Bridge", "Planned a message for a good Hong Kong time",
                   f"{clock.fmt_both(clock.slot_to_utc(slot), parent['child_tz'])} · {reason}")
        return {"type": "planned_message", "task_id": tid, "deliver": True}
    if typ == "book_question":
        ch, qid = book.add_question(parent, a.get("chapter") or "first_home", a.get("question_zh") or a.get("question_en"),
                                    a.get("question_en", ""))
        tid = book.plan_ask(parent, qid)
        return {"type": "book_question", "chapter_id": ch["id"], "question_id": qid, "task_id": tid, "deliver": True}
    if typ == "service":
        events.log(pid, "Orchestrator", "Service request (coming soon)", a.get("service", ""))
        return {"type": "service", "service": a.get("service", "")}
    return None


def confirm(task_id: int):
    t = care.get_task(task_id)
    if not t or t["status"] != "pending_confirm":
        return t
    parent = db.get_parent(t["parent_id"])
    care.set_status(task_id, "active")
    events.log(parent["id"], "Orchestrator", "Care plan started", f"{t['title']} · {care.describe_schedule(t['schedule'])}")
    chatlog.post(parent["id"], "ai", f"✅ Started: **{t['title']}**, {care.describe_schedule(t['schedule'], parent['child_tz'])}. "
                                     f"You'll see every result in the Care log.")
    deliver(task_id)
    return t
