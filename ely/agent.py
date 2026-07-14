"""
Core agent loop — provider-agnostic function-calling loop with tool dispatch.
Extracted and adapted from ely/agent.py.
"""

import json
from .providers import create_provider
from .config import get_provider_config, get_int, get, get_bool
from .tools import get_tools, get_workspace_info, get_diary_context, _get_dynamic_tool_names
from .guard import sanitize
from .skills import build_skills_prompt
from .prompts import BASE_PROMPT, SLASH_PROMPTS
from .contexts import get_context_prompt
from .memory import increment_round, build_memory_prompt, maybe_compact
from .mcp import get_mcp_manager


def _resolve_provider(slot: str = "provider"):
    """Create an AI provider from config."""
    cfg = get_provider_config(slot)
    return create_provider(cfg), cfg["model"]


def _build_system_prompt(context: str = "default") -> str:
    """Assemble the system prompt from base + skill + context + memory + workspace + diary."""
    name = get("agent", "name", "Ely")

    prompt = BASE_PROMPT.format(name=name)

    # Skills (directory-based, loaded from ~/.ely/skills/ and ./skills/)
    skills_prompt = build_skills_prompt()
    if skills_prompt:
        prompt += skills_prompt

    # Workspace info
    prompt += f"\n\n**Environnement :** {get_workspace_info()}"
    prompt += "\nTous les chemins de fichiers sont relatifs au workspace. Tu ne peux pas lire/écrire en dehors."

    # Bash mode (informational — agent cannot change it)
    from .tools import _is_sandbox_enabled
    if _is_sandbox_enabled():
        prompt += "\n**Bash :** sandbox Docker (réseau isolé, workspace monté dans /workspace)."
    else:
        prompt += "\n**Bash :** exécution directe sur la machine hôte. Sois prudent avec les commandes destructives."

    # Context (loaded from ~/.ely/contexts/ or ./.ely/contexts/)
    ctx_prompt = get_context_prompt(context)
    prompt += f"\n\n**Contexte :** {ctx_prompt}"

    # Diary (last 5 entries)
    diary = get_diary_context(5)
    if diary:
        prompt += diary
    prompt += "\n**Diary :** Tu peux utiliser diary_add (si l'utilisateur demande de retenir quelque chose) et diary_search (pour retrouver une information passée)."

    # MCP resources
    try:
        mcp_ctx = get_mcp_manager().get_resources_context()
        if mcp_ctx:
            prompt += mcp_ctx
    except Exception:
        pass

    return prompt


def _check_slash_command(message: str) -> str | None:
    """If message is a slash command, return the enhanced prompt."""
    msg = message.strip()
    for cmd, prefix in SLASH_PROMPTS.items():
        if msg.startswith(f"/{cmd}"):
            rest = msg[len(cmd) + 1:].strip()
            return f"{prefix}\n\n{rest}" if rest else prefix
    return None


def chat(
    message: str,
    history: list = None,
    user_id: str = "default",
    context: str = "default",
    stream_cb=None,
    status_cb=None,
    slot: str = "provider",
) -> dict:
    """
    Main chat entry point. Runs the LLM function-calling loop.

    Args:
        stream_cb: called with final reply text when done
        status_cb: called with (event_type, data) for live status updates
                   event_types: "thinking", "tool_call", "tool_result", "reply"
    Returns:
        {"reply": str, "actions": [str], "tokens": {...}, "model": str}
    """
    history = history or []
    actions = []
    tokens = {"prompt": 0, "completion": 0, "total": 0}

    # Sanitize input
    clean_msg, flagged = sanitize(message)
    if flagged:
        actions.append("prompt_guard_triggered")

    # Check slash commands — enhance the prompt
    slash_prompt = _check_slash_command(message)
    if slash_prompt:
        clean_msg = slash_prompt

    # Build system prompt (includes workspace, diary, sandbox status, memory)
    system_prompt = _build_system_prompt(context)

    # Add auto-memory (compacted user profile)
    mem = build_memory_prompt(user_id)
    if mem:
        system_prompt += mem

    # Resolve provider
    provider, model_name = _resolve_provider(slot)

    # Initialize MCP connections (lazy, only on first call)
    try:
        get_mcp_manager().connect_all()
    except Exception:
        pass

    # Get tools — dynamically filter based on message, broaden on later turns
    dynamic_names = _get_dynamic_tool_names(clean_msg, history)
    if dynamic_names:
        tool_defs, tool_handlers = get_tools(names=dynamic_names)
    else:
        tool_defs, tool_handlers = get_tools()

    # Build messages with conversation history
    messages = [{"role": "system", "content": system_prompt}]
    for h in history[-10:]:
        role = h.get("role", "user")
        content = h.get("content", "")
        if role in ("user", "assistant"):
            messages.append({"role": role, "content": str(content)[:2500]})
    messages.append({"role": "user", "content": clean_msg})

    max_turns = get_int("agent", "max_turns", 8)

    # Inject completed background task results before each agent call
    try:
        from .tools import _get_background_results
        bg_results = _get_background_results()
        if bg_results:
            messages.append({"role": "user", "content": f"[Background tasks completed]\n{bg_results}"})
    except Exception:
        pass

    # ── Function-calling loop ──
    reply = ""
    all_reasoning = []
    for turn in range(max_turns):
        # Broaden tools after first turn — agent might need any tool
        if turn == 1 and dynamic_names:
            tool_defs, tool_handlers = get_tools()
            dynamic_names = None
        if status_cb:
            status_cb("thinking", f"Réflexion... (tour {turn + 1}/{max_turns})")
        try:
            resp = provider.chat(messages, tools=tool_defs if tool_defs else None)
        except Exception as e:
            # Provider error — try once more without tools
            if turn == 0:
                try:
                    resp = provider.chat(messages, tools=None)
                except Exception:
                    return {"reply": f"Erreur de connexion au LLM: {e}", "actions": actions, "tokens": tokens, "model": model_name}
            else:
                return {"reply": f"Erreur LLM: {e}", "actions": actions, "tokens": tokens, "model": model_name}

        usage = resp.get("usage", {})
        tokens["prompt"] += usage.get("prompt_tokens", 0)
        tokens["completion"] += usage.get("completion_tokens", 0)
        tokens["total"] += usage.get("total_tokens", 0)

        content = resp.get("content", "") or ""
        reasoning = resp.get("reasoning", "")
        if reasoning:
            all_reasoning.append(reasoning)
            if status_cb:
                status_cb("reasoning", reasoning[:500])
        tool_calls = resp.get("tool_calls")

        # No tool calls → agent is done
        if not tool_calls:
            reply = content
            if stream_cb:
                stream_cb(content)
            if status_cb:
                status_cb("reply", content[:200])
            break

        # Record assistant message (must include tool_calls for API validity)
        messages.append({
            "role": "assistant",
            "content": content or "",
            "tool_calls": tool_calls,
        })

        # Process tool calls
        for tc in tool_calls:
            tc_id = tc.get("id", "")
            tc_name = tc["function"]["name"]
            tc_args_str = tc["function"]["arguments"]

            try:
                tc_args = json.loads(tc_args_str)
            except json.JSONDecodeError:
                tc_args = {}

            actions.append(tc_name)

            if status_cb:
                # Show a compact tool call description
                arg_preview = json.dumps(tc_args, ensure_ascii=False)
                if len(arg_preview) > 80:
                    arg_preview = arg_preview[:77] + "..."
                status_cb("tool_call", f"{tc_name} {arg_preview}")

            handler = tool_handlers.get(tc_name)
            if handler:
                try:
                    result = handler(**tc_args)
                except Exception as e:
                    result = f"Tool error: {e}"
            else:
                result = f"Unknown tool: {tc_name}"

            if status_cb:
                result_preview = str(result)[:100].replace("\n", " ")
                status_cb("tool_result", f"{tc_name} → {result_preview}")

            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": str(result),
            })

        # Gentle nudge near the end
        if turn == max_turns - 3:
            messages.append({
                "role": "user",
                "content": "Si tu as des résultats, donne ta réponse maintenant. Limite les nouveaux appels d'outils.",
            })

    # ── Fallback: force text response ──
    if not reply:
        try:
            resp = provider.chat(messages, tools=None)
            reply = resp.get("content", "")
            if stream_cb:
                stream_cb(reply)
            usage = resp.get("usage", {})
            tokens["prompt"] += usage.get("prompt_tokens", 0)
            tokens["completion"] += usage.get("completion_tokens", 0)
            tokens["total"] += usage.get("total_tokens", 0)
        except Exception:
            reply = "Je n'ai pas pu générer de réponse. Peux-tu reformuler ?"

    # ── Post-turn: memory compaction ──
    increment_round(user_id)
    all_msgs = messages + [{"role": "assistant", "content": reply}]
    try:
        maybe_compact(user_id, all_msgs, provider)
    except Exception:
        pass

    return {
        "reply": reply,
        "reasoning": "\n".join(all_reasoning) if all_reasoning else "",
        "actions": actions,
        "tokens": tokens,
        "model": model_name,
    }
