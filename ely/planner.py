"""
Task planner — decomposes complex requests into sub-tasks for parallel execution.
Each sub-agent gets only the task-specific context, minimizing token usage.
"""

import json
import re

PLANNER_PROMPT = """You are a task planner. Decompose the user's request into independent sub-tasks.

Rules:
- Each sub-task must be SELF-CONTAINED (the sub-agent only sees its task, nothing else)
- Include ALL relevant context in each task description (file paths, patterns, expected output)
- Sub-tasks should be INDEPENDENT — they can run in parallel
- Max 6 sub-tasks. If the request is simple, return just 1.
- Each sub-task needs: id, description, context (default/code/sysadmin/research), tool_hint (optional)

Output ONLY valid JSON array:
[{"id": 1, "desc": "...", "context": "code", "hint": "bash|read_file|grep|web_search|..."}]

User request: {request}

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
    """Build minimal context for a sub-agent — only the task itself."""
    desc = task.get("desc", task.get("description", ""))
    ctx = task.get("context", "default")
    hint = task.get("hint", "")

    prompt = f"""**Task #{task.get('id', '?')}**: {desc}

Context: {ctx}
{f'Recommended tools: {hint}' if hint else ''}

CRITICAL: You have only 3-4 turns. Be laser-focused. No exploration, just execution.
Start with the result directly. At the end, suggest 1-2 evolution axes if relevant:
🔄 Evolution: [concrete next step]
"""
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
