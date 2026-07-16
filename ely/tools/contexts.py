"""Context tools — manage agent contexts."""
from . import _action


@_action("context_list", "List all available contexts.", {})
def tool_context_list() -> str:
    from ..contexts import list_contexts
    ctxs = list_contexts()
    if not ctxs: return "No contexts found."
    lines = ["Available contexts:"]
    for c in ctxs: lines.append(f"  - {c['name']}: {c.get('description', '')}")
    return "\n".join(lines)


@_action("context_create", "Create a new custom context.",
         {"name": {"type": "string", "description": "Context name (slug)."},
          "description": {"type": "string", "description": "One-line description."},
          "prompt": {"type": "string", "description": "Instructions for the agent."}})
def tool_context_create(name: str, description: str, prompt: str) -> str:
    from ..contexts import create_context
    path = create_context(name, description, prompt)
    return f"Context '{name}' created at {path}. Activate with /context activate {name}."


@_action("context_get", "Get the full content of a context.",
         {"name": {"type": "string", "description": "Context name."}})
def tool_context_get(name: str) -> str:
    from ..contexts import get_context
    ctx = get_context(name)
    if not ctx: return f"Context '{name}' not found."
    return f"**{name}** — {ctx.get('description', '')}\n\n{ctx.get('prompt', '')}"
