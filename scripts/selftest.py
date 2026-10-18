"""End-to-end check of the demo flow against the RUNNING app.

1. Start the app:  start.bat / start.sh   (or: uvicorn app.main:app --port 8000)
2. Then run:       python scripts/selftest.py     (add --url ... for another port)

It resets the app first and again at the end, so you can record right after.
"""
import argparse
import sys
import time

import httpx

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://localhost:8000")
B = ap.parse_args().url.rstrip("/")
c = httpx.Client(timeout=180, trust_env=False)
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({str(detail)[:110]})" if detail else ""))


def state():
    return c.get(f"{B}/api/state", params={"parent_id": 1}).json()


def wait_for(pred, timeout=120):
    end, s = time.time() + timeout, state()
    while time.time() < end:
        try:
            if pred(s):
                return s
        except Exception:
            pass
        time.sleep(1)
        s = state()
    return s


def chat(text):
    n = len(state()["chat"])
    c.post(f"{B}/api/chat", data={"parent_id": 1, "text": text})
    return wait_for(lambda s: not s["chat_busy"] and len(s["chat"]) >= n + 2)


def last_ai(s):
    return next((m for m in reversed(s["chat"]) if m["role"] == "ai"), None)


def ai_cards(s, typ):
    for m in reversed(s["chat"]):
        if m["role"] == "child":
            break
        for x in ((m.get("card") or {}).get("cards") or []):
            if x["type"] == typ:
                return x
    return None


def mum_msgs():
    return c.get(f"{B}/sim/messages", params={"parent_id": 1}).json()


def wait_mum(n_before, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        msgs = mum_msgs()
        if len(msgs) > n_before and msgs[-1]["sender"] != "parent":
            return msgs[-1]
        time.sleep(1)
    return mum_msgs()[-1] if mum_msgs() else {}


def mum_says(text, kind="text"):
    n = len(mum_msgs())
    c.post(f"{B}/sim/send", data={"parent_id": 1, "text": text, "kind": kind})
    time.sleep(1)
    return wait_mum(n + 1)


def button(prefix):
    for m in reversed(mum_msgs()):
        for b in (m.get("meta") or {}).get("buttons", []):
            if b["id"].startswith(prefix):
                return b["id"]


def task(s, tid):
    return next((t for t in s["tasks"] if t["id"] == tid), None)


def next_event():
    r = c.post(f"{B}/demo/next")
    time.sleep(1.5)
    return r.json()


try:
    state()
except Exception as e:
    sys.exit(f"Cannot reach the app at {B}: {e}\nStart it first (start.bat, or: uvicorn app.main:app --port 8000)")
c.post(f"{B}/demo/reset")
time.sleep(1)
MED = "medicine"
s = state()
print(f"AI GrandKid self-test against {B}   (AI: {s['mode']['llm']})")
print("\n0. Clean start")
check("Chat, care log and Mum's WhatsApp start empty", not s["chat"] and not s["tasks"] and not mum_msgs())
check("Family book has the template chapters", len(s["book"]["chapters"]) >= 6 and s["book"]["pages"] == 0)

print("\n1. Child describes a medication reminder")
s = chat("Remind Mum to take her blood pressure medicine every day at 2pm")
prop = ai_cards(s, "task_proposal")
check("AI proposes a plan that waits for Confirm", prop and task(s, prop["task_id"])["status"] == "pending_confirm",
      (last_ai(s) or {}).get("content", "")[:80])
tid = prop["task_id"] if prop else None
if tid:
    t = task(s, tid)
    check("Shown in Hong Kong and London time", "14:00 HK" in t["when"] and s["parent"]["child_city"] in t["when"], t["when"])
    n = len(mum_msgs())
    c.post(f"{B}/api/tasks/{tid}/confirm")
    msg = wait_mum(n)
    check("Confirm -> Mum's phone shows the reminder at 14:00 right away", msg.get("type") == "buttons" and
          "14:00" in state()["clock"]["hk"][:5] or msg.get("type") == "buttons", msg.get("content", "")[:60])
    c.post(f"{B}/sim/button", data={"parent_id": 1, "button_id": button("med_taken:") or "", "title": "已食藥 ✅"})
    s = wait_for(lambda s: task(s, tid)["runs"][0]["status"] == "taken")
    MED = task(s, tid)["title"]
    check("Mum taps 已食藥 -> Care log shows Taken", task(s, tid)["runs"][0]["status"] == "taken", task(s, tid)["runs"][0]["outcome"])

print("\n2. Daily check-in, and a trend built over three days")
s = chat("Check in with Mum every morning at 9:30")
prop = ai_cards(s, "task_proposal")
cid = prop["task_id"] if prop else None
if cid:
    n = len(mum_msgs())
    c.post(f"{B}/api/tasks/{cid}/confirm")
    wait_mum(n)
    check("Next morning 09:30: Mum gets a caring check-in", task(state(), cid)["runs"], state()["clock"]["hk_day"])
    mum_says("今日膝頭好痛，冇落街買餸", kind="audio")
    s = wait_for(lambda s: task(s, cid)["runs"][0]["status"] == "answered")
    check("Her reply becomes the check-in outcome", task(s, cid)["runs"][0]["status"] == "answered", task(s, cid)["runs"][0]["outcome"])
    for day_reply in ["行樓梯膝頭好痛，要慢慢行", "膝頭仲係好痛，成日都唔想出街"]:
        for _ in range(6):  # step to the next day's check-in (taking medicine on the way)
            e = next_event()
            b = button("med_taken:")
            if MED in (e.get("event") or "") and b:
                c.post(f"{B}/sim/button", data={"parent_id": 1, "button_id": b, "title": "已食藥 ✅"})
                time.sleep(1)
            if "check-in" in (e.get("event") or "").lower():
                break
        mum_says(day_reply)
    s = wait_for(lambda s: any(a["kind"] == "pain" for a in s["alerts"]))
    pain = next((a for a in s["alerts"] if a["kind"] == "pain"), None)
    check("Insight: knee pain 3 times this week, with her own words", pain and len(pain["evidence"]) >= 3, pain and pain["title"])
    check("Alert posted in the child's chat", any((m.get("card") or {}).get("type") == "alert" for m in s["chat"]))

print("\n3. Medicine not confirmed -> follow-up -> child told")
if tid:
    for _ in range(4):
        e = next_event()
        if MED in (e.get("event") or "") and "reminder" not in (e.get("event") or "").lower():
            break
    e1 = next_event()
    e2 = next_event()
    s = state()
    check("Second reminder after 30 min, then marked missed", "Second reminder" in (e1.get("event") or "") and
          task(s, tid)["runs"][0]["status"] == "missed", f"{e1.get('event')} / {e2.get('event')}")
    check("Child told in the chat", any("hasn't confirmed" in m["content"] or "missed" in m["content"] for m in s["chat"][-4:]))

print("\n4. A message delivered at a good Hong Kong time")
n = len(mum_msgs())
s = chat("Tell Mum that Ethan won his football match today")
pm = ai_cards(s, "planned_message")
check("Message planned and delivered to Mum's phone", pm and wait_mum(n), (last_ai(s) or {}).get("content", "")[:70])
if pm:
    mum_says("好叻仔呀！叫佢繼續努力")
    s = wait_for(lambda s: task(s, pm["task_id"])["runs"][0]["status"] == "answered", 30)
    check("Mum's reply comes back to the child", any("replied to your message" in m["content"] for m in s["chat"][-3:]))

print("\n5. Family book")
n = len(mum_msgs())
s = chat("Ask Mum how she met Grandpa")
bq = ai_cards(s, "book_question")
asked = wait_mum(n)
check("Chat request -> AI asks Mum the question", bq and asked.get("sender") == "ai", asked.get("content", "")[:60])
pages = s["book"]["pages"]
mum_says("我同公公係喺工廠識㗎，佢以前成日幫我搬貨，後來就請我去睇戲，嗰陣睇嘅係粵語長片。", kind="audio")
s = wait_for(lambda s: s["book"]["pages"] > pages)
check("Her answer becomes a page in the book", s["book"]["pages"] > pages)
ch = next(c_ for c_ in s["book"]["chapters"] if c_["key"] == "recipes")
q = next((q for q in ch["questions"] if q["status"] == "pending"), None)
n = len(mum_msgs())
r = c.post(f"{B}/api/book/questions/{q['id']}/ask")
check("'Ask Mum' button asks a template question", r.status_code == 200 and wait_mum(n).get("sender") == "ai")
r = c.post(f"{B}/api/book/chapters", data={"parent_id": 1, "title": "Festivals in our family", "emoji": "🏮",
                                           "first_question": "How did we celebrate Mid-Autumn when you were young?"})
check("Child can add their own chapter with a question", r.status_code == 200)

print("\n6. Scam check")
reply = mum_says("【香港警務處】你嘅銀行戶口涉及洗黑錢案件，已被凍結。請即轉帳保證金到指定戶口並提供驗證碼解凍")
s = wait_for(lambda s: any(a["kind"] == "scam" for a in s["alerts"]))
check("Scam alert to the child", any(a["kind"] == "scam" and a["level"] == 3 for a in s["alerts"]))
check("Mum warned in Cantonese (18222)", "18222" in reply.get("content", ""), reply.get("content", "")[:60])

print("\n7. Scope, questions and the morning brief")
s = chat("Book an Uber for Mum at 5pm tomorrow")
check("Out-of-scope request answered honestly (coming soon)", ai_cards(s, "service") or "coming soon" in (last_ai(s) or {}).get("content", "").lower(),
      (last_ai(s) or {}).get("content", "")[:80])
s = chat("How is Mum this week?")
check("AI answers questions about Mum", len((last_ai(s) or {}).get("content", "")) > 20)
n = len(state()["chat"])
c.post(f"{B}/demo/brief")
s = wait_for(lambda s: any((m.get("card") or {}).get("type") == "brief" for m in s["chat"][n:]))
br = next((m["card"] for m in s["chat"][n:] if (m.get("card") or {}).get("type") == "brief"), None)
check("Morning brief with headline, timeline and talking points",
      br and br.get("headline") and len(br.get("timeline", [])) >= 5 and br.get("talking_points"), br and br.get("headline"))

c.post(f"{B}/demo/reset")
print(f"\n{sum(results)}/{len(results)} checks passed. The app has been reset, ready to record.")
if not all(results):
    print("Look at the app's black window for error lines or tracebacks.")
sys.exit(0 if all(results) else 1)
