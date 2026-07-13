"""
Conversation memory with periodic LLM compaction.
Stores a short (<1000 char) situation summary, auto-compacted every N Q&A cycles.
Only user questions and final assistant answers are used (no tool calls).
"""

import json
import os
import time


def _memory_dir() -> str:
    from .config import get
    d = get("memory", "dir", "~/.ely/memory")
    return os.path.expanduser(d)


def _memory_file(user_id: str) -> str:
    d = _memory_dir()
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{user_id}.json")


def _load(user_id: str) -> dict:
    path = _memory_file(user_id)
    if os.path.isfile(path):
        with open(path) as f:
            return json.load(f)
    return {"situation": "", "round_count": 0, "created_at": time.time()}


def _save(user_id: str, data: dict):
    with open(_memory_file(user_id), "w") as f:
        json.dump(data, f, indent=2)


def get_situation(user_id: str) -> str:
    return _load(user_id).get("situation", "")


def update_situation(user_id: str, situation: str):
    data = _load(user_id)
    data["situation"] = situation[:1000]
    _save(user_id, data)


def get_round_count(user_id: str) -> int:
    return _load(user_id).get("round_count", 0)


def increment_round(user_id: str):
    data = _load(user_id)
    data["round_count"] = data.get("round_count", 0) + 1
    _save(user_id, data)


def build_memory_prompt(user_id: str) -> str:
    """Build a compact memory section for the system prompt."""
    situation = get_situation(user_id)
    if not situation:
        return ""

    return (
        "\n**Mémoire (contexte de la session en cours) :**\n"
        f"{situation}"
    )


def maybe_compact(user_id: str, recent_messages: list, provider) -> bool:
    """Run LLM compaction every N rounds. Returns True if compaction ran."""
    from .config import get_int
    interval = get_int("memory", "compaction_rounds", 10)
    count = get_round_count(user_id)

    if count > 0 and count % interval == 0 and recent_messages:
        _compact(user_id, recent_messages, provider)
        return True
    return False


def _compact(user_id: str, recent_messages: list, provider):
    """Compress Q&A into a situation summary via the LLM."""
    from .prompts import COMPACT_PROMPT

    # Keep only user questions and final assistant answers (no tool calls, no tool results)
    qa_messages = [
        m for m in recent_messages[-40:]
        if m["role"] in ("user", "assistant")
        and not m.get("tool_calls")
    ]

    if not qa_messages:
        return

    history_text = "\n".join(
        f"{m['role']}: {str(m.get('content', ''))[:300]}"
        for m in qa_messages
    )

    try:
        resp = provider.chat(
            messages=[{"role": "user", "content": COMPACT_PROMPT.format(history=history_text)}],
            tools=None,
        )
        content = resp.get("content", "").strip()
        if content:
            update_situation(user_id, content[:1000])
    except Exception:
        pass  # Compaction is best-effort
