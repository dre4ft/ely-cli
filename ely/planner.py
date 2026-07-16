"""
Task planner — decomposes complex requests into sub-tasks for parallel execution.
Each sub-agent gets only the task-specific context, minimizing token usage.
"""

import json
import re

PLANNER_PROMPT = """Decompose request into independent sub-tasks. Output ONLY JSON array.

Rules:
- Each task SELF-CONTAINED (sub-agent only sees its task)
- Include ALL context in description (paths, patterns, expected output)
- Tasks INDEPENDENT -> parallel
- Max 6. Simple = 1.
- Fields: id, desc, context, hint

Format: [{"id":1,"desc":"...","context":"code","hint":"bash|grep|..."}]

Request: {request}
JSON:"""


def parse_plan(response: str) -> list[dict]:
    """Extract JSON plan from LLM response."""
    try:
        # Try direct JSON
        return json.loads(response)
    except json.JSONDecodeError:
        pass
    # Try to extract from markdown
    m = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', response, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try bare array
    m = re.search(r'\[.*\]', response, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return []


def build_subagent_prompt(task: dict) -> str:
    """Build minimal context for a sub-agent."""
    desc = task.get("desc", task.get("description", ""))
    ctx = task.get("context", "default")
    hint = task.get("hint", "")
    tid = task.get('id', '?')
    prompt = f"Task #{tid}: {desc}\nContext: {ctx}"
    if hint: prompt += f"\nTools: {hint}"
    prompt += "\n3-4 turns max. No markdown. Result first. End with 🔄 Evolution if relevant."
    return prompt


def estimate_complexity(request: str) -> int:
    """Quick heuristic: does this request need decomposition?
    Returns suggested number of sub-tasks (1 = no decomposition needed).
    """
    indicators = [
        (r"(?i)(each|every|all|multiple|several|chaque|tous|toutes|plusieurs)\s+(file|endpoint|url|module|fichier)s?", 3),
        (r"(?i)(parallel|concurrent|simultaneous|parallèle|simultané)", 3),
        (r"(?i)(compare|contrast|diff|versus|vs\.?|comparer)", 2),
        (r"(?i)(analyze|audit|scan|check|test|analyser|auditer|scanner)\s+.*(?:and|,|et)\s+(?:analyze|audit|scan|check|test|analyser)", 3),
        (r"(?i)(report|summary|overview|document|rapport|résumé)\s+(?:on|of|about|sur|de)", 2),
        (r"(?i)(tous les|toutes les|chaque|all the|every)", 3),
    ]
    for pattern, count in indicators:
        if re.search(pattern, request):
            return count
    # Default: no decomposition for simple requests
    if len(request.split()) < 15:
        return 1
    return 2
