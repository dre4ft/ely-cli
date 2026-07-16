"""Diary tools — persistent user-driven memory."""
import json
import os
import time
from . import _action, _load_diary, _save_entry, _next_diary_id, _migrate_old_diary, _diary_dir


@_action("diary_add", "Add an entry to the persistent diary.",
         {"content": {"type": "string", "description": "The diary entry text."},
          "tags": {"type": "string", "description": "Comma-separated tags."}},
         optional=["tags"])
def tool_diary_add(content: str, tags: str = "") -> str:
    _migrate_old_diary()
    entry = {"id": _next_diary_id(), "content": content,
             "tags": [t.strip() for t in tags.split(",") if t.strip()],
             "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}
    _save_entry(entry)
    return f"Diary entry #{entry['id']} saved."


@_action("diary_list", "List recent diary entries.",
         {"limit": {"type": "integer", "description": "Max entries (default 20)."}},
         optional=["limit"])
def tool_diary_list(limit: int = 20) -> str:
    _migrate_old_diary()
    entries = _load_diary()
    if not entries: return "Diary is empty."
    recent = entries[-limit:]
    lines = [f"Diary ({len(entries)} entries, showing last {len(recent)}):"]
    for e in reversed(recent):
        tags = f" [{', '.join(e.get('tags', []))}]" if e.get("tags") else ""
        lines.append(f"  #{e['id']} [{e['timestamp']}]{tags}")
        lines.append(f"    {e['content'][:200]}")
    return "\n".join(lines)


@_action("diary_search", "Search diary entries by text or tags.",
         {"query": {"type": "string", "description": "Search query."}})
def tool_diary_search(query: str) -> str:
    _migrate_old_diary()
    entries = _load_diary()
    q = query.lower()
    matches = [e for e in entries if q in e.get("content", "").lower() or any(q in t.lower() for t in e.get("tags", []))]
    if not matches: return f"No diary entries matching '{query}'."
    lines = [f"Found {len(matches)} matching entries:"]
    for e in reversed(matches[-15:]):
        tags = f" [{', '.join(e.get('tags', []))}]" if e.get("tags") else ""
        lines.append(f"  #{e['id']} [{e['timestamp']}]{tags}")
        lines.append(f"    {e['content'][:300]}")
    return "\n".join(lines)


@_action("diary_get", "Read a specific diary entry by ID.",
         {"entry_id": {"type": "integer", "description": "The diary entry ID."}})
def tool_diary_get(entry_id: int) -> str:
    _migrate_old_diary()
    path = os.path.join(_diary_dir(), f"{entry_id}.json")
    if os.path.isfile(path):
        try:
            with open(path) as f: e = json.load(f)
            tags = f" [{', '.join(e.get('tags', []))}]" if e.get("tags") else ""
            return f"#{e['id']} [{e['timestamp']}]{tags}\n\n{e['content']}"
        except Exception: pass
    return f"Diary entry #{entry_id} not found."
