"""Rule-based stand-in for the LLM (LLM_PROVIDER=mock, or if the AI call fails).
Lets the whole app run with no API key. For the real demo use a real model."""
import re
from datetime import timedelta

SCAM_WORDS = ["轉帳", "轉賬", "過數", "銀行戶口", "驗證碼", "otp", "保證金", "公安", "入境處", "海關", "中獎", "投資",
              "回報", "客服", "速遞", "包裹", "http", "猜猜我是誰", "猜下我係邊個", "凍結", "洗黑錢", "加密貨幣",
              "transfer", "verify your account", "scam message"]
EMERGENCY_WORDS = ["跌親起唔到", "起唔到身", "胸口好痛", "胸口痛", "唞唔到氣", "暈低", "流好多血", "救命"]


# ============================================================== Mum's side
def analyze(text: str) -> dict:
    t = text.lower()
    signals = []

    def add(cat, val, detail):
        signals.append({"category": cat, "value": val, "detail": detail, "evidence": text[:120]})

    if "痛" in t or "pain" in t:
        part = "knee" if "膝" in t else "back" if ("腰" in t or "背" in t) else "head" if "頭痛" in t else "body"
        add("pain", f"{part} pain", f"Mentioned {part} pain")
    if any(k in t for k in ["冇落街", "唔想出街", "冇出街", "留喺屋企"]):
        add("mobility", "stayed home", "Did not go out")
    if any(k in t for k in ["食咗藥", "食左藥", "食咗啲藥", "已食藥"]):
        add("medication", "taken", "Took medicine")
    if any(k in t for k in ["冇買餸", "冇落街買餸", "冇食", "唔想食", "冇胃口"]):
        add("meal", "skipped", "Skipped a meal or did not buy food")
    elif any(k in t for k in ["食咗飯", "食飯", "早餐", "午餐", "晚飯", "飲茶", "煮咗"]):
        add("meal", "ate", "Had a meal")
    if any(k in t for k in ["悶", "唔開心", "孤單", "寂寞", "冇人陪"]):
        add("mood", "lonely" if any(k in t for k in ["孤單", "寂寞", "冇人陪"]) else "low", "Feeling low")
    elif any(k in t for k in ["開心", "好好", "幾好", "精神"]):
        add("mood", "good", "In good spirits")
    if any(k in t for k in ["瞓唔著", "訓唔著", "失眠", "瞓得唔好"]):
        add("sleep", "poor", "Slept poorly")
    if any(k in t for k in ["太極", "公園", "飲茶", "朋友"]):
        add("social", "met friends", "Social activity")

    facts = []
    if "陳太" in text and ("醫院" in text or "入院" in text):
        facts.append("Friend Mrs Chan (陳太) is in hospital")

    hits = [w for w in SCAM_WORDS if w in t]
    risk = "high" if len(hits) >= 2 else "medium" if hits else "none"
    emerg = any(w in text for w in EMERGENCY_WORDS)
    is_story = (any(k in text for k in ["以前", "後生", "細個", "嗰陣", "食譜", "做法", "煲", "公公", "嫁"])
                and len(text) > 25 and risk == "none")
    chapter = ("recipes" if any(k in text for k in ["湯", "煲", "煮", "食譜"]) else
               "love" if any(k in text for k in ["公公", "嫁", "結婚"]) else
               "childhood" if "細個" in text else "first_home")
    summary = ("Mum: " + ", ".join(s["detail"].lower().replace("mentioned ", "") for s in signals)) if signals else f"Mum said: {text[:60]}"
    return {"summary_en": summary, "signals": signals, "facts": facts,
            "scam": {"risk": risk, "reason": ("Contains: " + ", ".join(hits)) if hits else "",
                     "type": "money transfer / impersonation" if hits else ""},
            "emergency": {"is_emergency": emerg, "reason": "Urgent words detected" if emerg else ""},
            "story": {"is_story": is_story, "chapter": chapter}}


def reply(text, analysis, nickname, child_zh, ctx=None) -> str:
    ctx = ctx or {}
    if analysis["emergency"]["is_emergency"]:
        return f"{nickname}！而家即刻打999或者㩒平安鐘呀！我已經即刻通知咗{child_zh}。"
    if analysis["scam"]["risk"] in ("medium", "high"):
        return (f"{nickname}小心呀⚠️ 呢個好似係騙案！千祈唔好轉錢、唔好俾銀行資料同驗證碼、唔好撳條連結。"
                f"我已經話咗俾{child_zh}知，有懷疑可以打防騙易熱線18222。")
    if ctx.get("book_answer"):
        return f"多謝你{nickname}，講得好好聽呀😊 我幫你寫低咗，放入屋企嘅回憶簿，啲孫一定好鍾意。"
    if ctx.get("medication_confirmed"):
        return f"好叻呀{nickname}👍 我記低咗你食咗藥，{child_zh}都會好放心！"
    cats = {s["category"]: s["value"] for s in analysis["signals"]}
    if "pain" in cats:
        part = {"knee": "膝頭", "back": "腰", "head": "頭"}.get(cats["pain"].replace(" pain", ""), "")
        return f"{nickname}，{part}痛就唔好勉強喇，坐低休息下先。痛咗幾耐呀？我話俾{child_zh}知先～"
    if cats.get("mood") in ("low", "lonely"):
        return f"{nickname}，我喺度陪你傾偈呀😊 今日想講下以前嘅事，定係我講個笑話你聽？"
    if cats.get("meal") == "ate":
        return f"好呀{nickname}，有食飯就好！今日食咗啲咩好嘢呀？"
    if analysis["facts"]:
        return f"{nickname}，陳太入咗醫院，你一定好掛住佢。佢而家點呀？"
    return f"收到呀{nickname}😊 今日過得點呀？有冇食飯、有冇出去行下？"


def greeting(nickname: str) -> str:
    return f"早晨呀{nickname}☀️ 今日膝頭仲痛唔痛呀？瞓得好唔好？"


def describe_image(caption: str) -> dict:
    return {"kind": "scam_message", "text": caption or "(screenshot)",
            "summary": "a screenshot of a suspicious message asking to transfer money (mock mode)",
            "scam_risk": "high", "scam_reason": "Mock mode treats screenshots as suspicious messages"}


def book_page(question: str, answer: str, chapter_key: str = None) -> dict:
    titles = {"recipes": ("A family recipe", "家傳湯水", "呢個湯係邊個教你煲㗎？"),
              "love": ("How we met", "我同公公", "你哋第一次約會去咗邊呀？"),
              "childhood": ("Growing up", "我細個嗰陣", "你細個屋企有幾多個兄弟姊妹？"),
              "first_home": ("Our first home", "我哋第一個家", "嗰陣租金幾多錢㗎？"),
              "when_you_were_little": ("When you were little", "你細個嘅時候", "仲有冇第二件好笑嘅事？"),
              "wisdom": ("What life taught me", "人生道理", "點解你咁諗呀？")}
    t, tz, fu = titles.get(chapter_key or "first_home", titles["first_home"])
    first = re.split(r"[，。！？,.!?]", answer.strip())[0][:40]
    return {"chapter": chapter_key or "first_home", "title": t, "title_zh": tz, "story_zh": answer.strip(),
            "story_en": "(Add an AI key to get an English translation of Mum's story.)", "quote": first, "follow_up_zh": fu}


# ============================================================== child's side
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
MEDS = [("blood pressure", "Blood pressure medicine", "血壓藥"), ("血壓", "Blood pressure medicine", "血壓藥"),
        ("diabetes", "Diabetes medicine", "糖尿藥"), ("metformin", "Metformin (diabetes)", "糖尿藥"),
        ("糖尿", "Diabetes medicine", "糖尿藥"), ("vitamin", "Vitamins", "維他命"), ("cholesterol", "Cholesterol medicine", "膽固醇藥"),
        ("heart", "Heart medicine", "心臟藥")]


def find_time(t: str):
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)", t, re.I) or \
        re.search(r"(?:at|@)\s*(\d{1,2})(?::(\d{2}))?", t, re.I) or re.search(r"(\d{1,2})[:：](\d{2})", t) or \
        re.search(r"(\d{1,2})\s*點", t)
    if not m:
        return None
    h = int(m.group(1))
    mi = int(m.group(2)) if m.lastindex and m.lastindex >= 2 and m.group(2) else 0
    ap = (m.group(3) if m.lastindex and m.lastindex >= 3 and m.group(3) else "").lower().replace(".", "")
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if not ap and ("下晝" in t or "晚" in t):
        h = h + 12 if h < 12 else h
    return f"{h:02d}:{mi:02d}"


def find_date(t: str, now_hk):
    tl = t.lower()
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", tl)
    if m:
        return now_hk.replace(year=int(m.group(3)), month=int(m.group(2)), day=int(m.group(1))).date()
    for i, d in enumerate(WEEKDAYS):
        if d in tl or d[:3] + " " in tl:
            delta = (i - now_hk.weekday()) % 7 or 7
            return (now_hk + timedelta(days=delta)).date()
    m = re.search(r"(\d{1,2})\s*(" + "|".join(MONTHS) + r")", tl) or re.search(r"(" + "|".join(MONTHS) + r")\w*\s*(\d{1,2})", tl)
    if m:
        a, b = m.group(1), m.group(2)
        day, mon = (int(a), b) if a.isdigit() else (int(b), a)
        year = now_hk.year + (1 if MONTHS.index(mon[:3]) + 1 < now_hk.month else 0)
        return now_hk.replace(year=year, month=MONTHS.index(mon[:3]) + 1, day=day).date()
    if "tomorrow" in tl or "聽日" in t:
        return (now_hk + timedelta(days=1)).date()
    return None


def coordinate(text: str, parent, snapshot: str, tasks: list) -> dict:
    from . import clock
    t, tl = text, text.lower()
    child, nick = parent["child_name_zh"], parent["nickname"]
    now = clock.parent_now()
    time = find_time(t)

    if re.search(r"how('?s| is| was)\s+(my\s+)?(mum|mom|mother|she|her|dad|father)|點呀|好唔好|最近點|how are things", tl):
        lines = snapshot.split("\n")
        last = next((l for l in lines if l.startswith("Mum's last message")), "")
        alerts = [l.replace("Open alert: ", "") for l in lines if l.startswith("Open alert")]
        sig = next((l for l in lines if l.startswith("Signals this week")), "")
        pain = sig.count("pain")
        txt = (f"{last}\n\nThis week: knee pain mentioned {pain} time(s). "
               + (f"Things to watch: {'; '.join(alerts)}." if alerts else "Nothing worrying right now.")
               + "\n(Connect a real AI model for a fuller answer.)")
        return {"reply": txt, "actions": []}

    if re.search(r"remind|提", tl) and re.search(r"medicine|meds|pill|tablet|vitamin|藥", tl):
        if not time:
            return {"reply": "Sure. What time should I remind Mum (Hong Kong time)?", "actions": []}
        name, zh = "Medicine", "藥"
        for key, en, z in MEDS:
            if key in tl:
                name, zh = en, z
                break
        note = "飯後食" if re.search(r"after (meal|food|dinner|lunch)|飯後", tl) else ""
        once = find_date(t, now)
        if once and not re.search(r"every|daily|each day|每日|每天", tl):
            return {"reply": f"Got it. On {once.strftime('%a %d %b')} at {time} Hong Kong time I'll remind Mum to take her {name.lower()} "
                             f"and tell you when she's taken it. Tap Confirm.",
                    "actions": [{"type": "create_task", "task": {"kind": "medication", "title": name, "title_zh": zh,
                                                                 "schedule": {"type": "times", "at": [f"{once} {time}"]},
                                                                 "details": {"medicine": name, "medicine_zh": zh, "note_zh": note}}}]}
        return {"reply": f"Got it. I'll remind Mum every day at {time} Hong Kong time to take her {name.lower()}, ask her to tap "
                         f"\"taken\", remind her once more if she doesn't reply, and tell you if she misses it. Tap Confirm to start."
                         + ("" if name != "Medicine" else " Which medicine is it? I can add the name later."),
                "actions": [{"type": "create_task", "task": {"kind": "medication", "title": name, "title_zh": zh,
                                                             "schedule": {"type": "daily", "time": time},
                                                             "details": {"medicine": name, "medicine_zh": zh, "note_zh": note}}}]}

    if re.search(r"check (in|on)|every morning|每朝|問候", tl):
        tm = time or "09:30"
        return {"reply": f"I'll check in with Mum every day at {tm} Hong Kong time, ask how she's feeling, and tell you if "
                         f"anything seems off or she doesn't reply. Tap Confirm to start.",
                "actions": [{"type": "create_task", "task": {"kind": "checkin", "title": "Daily check-in", "title_zh": "每日問候",
                                                             "schedule": {"type": "daily", "time": tm},
                                                             "details": {"focus": "how she feels, her knee, meals"}}}]}

    if re.search(r"doctor|appointment|clinic|hospital|睇醫生|覆診", tl) and not re.search(r"escort|book a", tl):
        d = find_date(t, now)
        if not d:
            return {"reply": "When is the appointment? (e.g. Thursday 10am or 24 Oct 10am)", "actions": []}
        tm = time or "10:00"
        before = d - timedelta(days=1)
        return {"reply": f"I'll remind Mum about the appointment the evening before (18:00) and that morning (08:30), "
                         f"and ask if she'd like someone to go with her. Tap Confirm to start.",
                "actions": [{"type": "create_task", "task": {"kind": "appointment", "title": f"Doctor visit {d.strftime('%a %d %b')}",
                                                             "title_zh": "睇醫生",
                                                             "schedule": {"type": "times", "at": [f"{before} 18:00", f"{d} 08:30"]},
                                                             "details": {"what_zh": "睇醫生", "when_zh": f"{d.month}月{d.day}號{tm}",
                                                                         "appointment_at": f"{d} {tm}"}}}]}

    if re.search(r"uber|taxi|ride|car |grocer|escort|physio|clean|gift|order|deliver food|book a|送餸|陪診|叫車|的士", tl):
        when = f" at {time}" if time else ""
        return {"reply": f"I can't book that yet. It's coming soon in the Services tab. What I can do now: remind Mum{when} "
                         f"about it, tell her the details in Cantonese, and check in afterwards that everything went well. "
                         f"Would you like that?",
                "actions": [{"type": "service", "service": text[:60]}]}

    if re.search(r"want to know|ask (her|mum|mom|mother) (about|how|why|what|when|where|who)|family book|her story|her life|點解|以前", tl):
        ch = ("love" if re.search(r"grandpa|dad|father|met|公公", tl) else
              "recipes" if re.search(r"recipe|cook|soup|dish|湯|餸", tl) else
              "childhood" if re.search(r"grew up|childhood|young|細個", tl) else
              "when_you_were_little" if re.search(r"when i was|about me", tl) else
              "wisdom" if re.search(r"advice|wisdom|lesson", tl) else "first_home")
        qz = {"love": "你同公公係點識㗎？", "recipes": "你最拿手煮咩餸呀？可唔可以教下我點煮？",
              "childhood": "你細個喺邊度大㗎？屋企係點樣㗎？", "when_you_were_little": f"{child}細個有冇咩好笑嘅事呀？",
              "wisdom": "你想同孫仔講咩人生道理呀？", "first_home": "你當初點解會搬嚟呢區㗎？"}[ch]
        return {"reply": "Lovely question. I'll ask Mum gently in one of our chats and add her answer to the family book.",
                "actions": [{"type": "book_question", "chapter": ch, "question_en": text, "question_zh": qz}]}

    m = re.search(r"(?:tell|let)\s+(?:her|mum|mom|mother)(?:\s+know)?(?:\s+that)?\s+(.+)", t, re.I) or \
        re.search(r"(?:話俾|同)(?:媽媽|阿媽|佢)(?:知|講)?[，,：: ]*(.+)", t)
    if m or re.search(r"i love her|miss her", tl):
        msg = (m.group(1) if m else text).strip().rstrip(".")
        return {"reply": "I'll pass it on to Mum at a good time for her, and tell you what she says.",
                "actions": [{"type": "send_message", "message_en": msg, "message_zh": f"💌 {nick}，{child}叫我話你知：{msg}"}]}

    return {"reply": "Tell me how you'd like to care for Mum, for example: \"Remind Mum to take her blood pressure medicine "
                     "every day at 5pm\", \"Check in with Mum every morning\", \"Tell Mum Ethan won his football match\", "
                     "or ask \"How is Mum this week?\"", "actions": []}


def brief(data: dict) -> dict:
    pain = [s for s in data["signals"] if s["category"] == "pain"]
    tps = []
    if any("Mrs Chan" in f for f in data["facts"]):
        tps.append("Ask how her friend Mrs Chan is doing in hospital.")
    if pain:
        tps.append("Ask how her knee feels and whether it's hard to get around.")
    if any("book page" in t for t in data["timeline"]):
        tps.append("She added a story to the family book. Ask her to tell you more about it.")
    if not tps:
        tps.append("Ask what she cooked today and who she talked to.")
    acts = ["Call Mum tonight and ask about her knee; consider a doctor visit"] if pain else []
    return {"headline": "Mum had a fairly good day, but her knee is bothering her" if pain else "Mum had a calm day",
            "summary": f"{len(data['timeline'])} things happened in Mum's day. "
                       + ("Open alerts: " + "; ".join(data["open_alerts"]) + "." if data["open_alerts"] else "No open alerts."),
            "talking_points": tps[:3], "next_actions": acts}
