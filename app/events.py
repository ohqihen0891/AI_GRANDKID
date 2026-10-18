"""Activity log of the AI agent's parts. Powers the live "AI agents at work" panel in the demo view and the "What the AI did" list in the Care log."""
from . import db

AGENTS = ["Orchestrator", "Companion", "Health", "Insight", "Safety",
          "Family Bridge", "Legacy"]


def log(parent_id: int, agent: str, action: str, detail: str = ""):
    db.x("INSERT INTO events(parent_id, agent, action, detail, created_at) VALUES(?,?,?,?,?)",
         (parent_id, agent, action, detail[:500], db.now_iso()))
    print(f"[{agent}] {action} {('- ' + detail[:160]) if detail else ''}")
