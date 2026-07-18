"""Claude Code-like streaming UI with panels, diffs, and permissions."""
import sys, time
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.live import Live
from rich.text import Text
from rich.rule import Rule
from rich.prompt import Confirm

console = Console()
PERMISSION_ALWAYS = set()  # Tools the user has said "always allow" for


def user_banner(msg: str):
    """Render user message."""
    console.print()
    console.print(f"[bold cyan]▸ {msg}[/]")


def thinking_start():
    """Show thinking indicator."""
    pass  # Streaming handles this


def stream_agent(provider, messages: list, tool_defs: list, tool_handlers: dict, max_turns: int) -> dict:
    """Run agent with streaming UI. Returns result dict."""
    actions = []
    tokens = {"prompt": 0, "completion": 0, "total": 0}
    reply = ""
    all_reasoning = ""

    for turn in range(max_turns):
        # Collect streaming events
        content = ""
        reasoning = ""
        tool_calls = []

        # Show live streaming
        with Live(Text("⏳ ...", style="dim"), refresh_per_second=10, transient=True) as live:
            for event, data in provider.chat_stream(messages, tools=tool_defs if tool_defs else None):
                if event == "content":
                    content += data
                    # Show last 2 lines of streaming content
                    lines = content.split("\n")
                    preview = "\n".join(lines[-2:])[-200:]
                    live.update(Text.from_markup(f"[dim]{preview}[/]"))

                elif event == "reasoning":
                    reasoning += data
                    if len(reasoning) < 300:
                        live.update(Text.from_markup(f"[dim]💭 {reasoning.replace(chr(10), ' ')}[/]", overflow="ellipsis"))

                elif event == "tool_calls":
                    tool_calls = data
                    names = [tc["function"]["name"] for tc in data]
                    live.update(Text.from_markup(f"[cyan]🔧 Calling: {', '.join(names)}[/]"))

                elif event == "done":
                    d = data
                    if d.get("content"): content = d["content"]
                    if d.get("reasoning"): reasoning = d["reasoning"]
                    if d.get("tool_calls"): tool_calls = d["tool_calls"]
                    u = d.get("usage", {})
                    tokens["prompt"] += u.get("prompt_tokens", 0)
                    tokens["completion"] += u.get("completion_tokens", 0)
                    tokens["total"] += u.get("total_tokens", 0)

                elif event == "error":
                    return {"reply": f"Error: {data}", "actions": actions, "tokens": tokens, "model": "stream", "reasoning": reasoning}

        if reasoning:
            all_reasoning += reasoning

        # No tool calls → done
        if not tool_calls:
            reply = content
            break

        # Show tool calls as panels
        for tc in tool_calls:
            name = tc["function"]["name"]
            args_str = tc["function"]["arguments"]
            try:
                args = __import__('json').loads(args_str)
            except Exception:
                args = {}
            actions.append(name)

            # Permission check for destructive ops
            if _needs_permission(name, args):
                if name not in PERMISSION_ALWAYS:
                    console.print(Panel(f"[yellow]Tool: {name}[/]\n{yellow}{str(args)[:200]}[/]",
                                        border_style="yellow", title="⚠️ Permission required"))
                    if not Confirm.ask("Allow?", default=True):
                        tool_result = "User denied permission."
                    else:
                        if Confirm.ask("Always allow this tool?", default=False):
                            PERMISSION_ALWAYS.add(name)
                        tool_result = _execute_tool(name, args, tool_handlers)
                else:
                    tool_result = _execute_tool(name, args, tool_handlers)
            else:
                tool_result = _execute_tool(name, args, tool_handlers)

            # Show tool result compactly
            result_preview = str(tool_result)[:200].replace("\n", " ")
            console.print(f"[dim]  ⮡ {name}: {result_preview}[/]")

            messages.append({"role": "assistant", "content": content or "",
                             "tool_calls": [{"id": tc.get("id", f"call_{turn}"), "type": "function",
                                             "function": {"name": name, "arguments": args_str}}]})
            messages.append({"role": "tool", "tool_call_id": tc.get("id", f"call_{turn}"),
                             "content": str(tool_result)})

        if turn == max_turns - 3:
            messages.append({"role": "user", "content": "Donne ta reponse finale. Plus d'outils."})

    # Fallback
    if not reply:
        resp = provider.chat(messages, tools=None)
        reply = resp.get("content", "")

    return {"reply": reply, "reasoning": all_reasoning, "actions": actions, "tokens": tokens, "model": "stream"}


def _needs_permission(name: str, args: dict) -> bool:
    """Check if tool needs user permission."""
    destructive = ["rm ", "git reset", "git push", "docker rm", "kubectl delete", "DROP ", "DELETE "]
    if name == "bash":
        cmd = str(args.get("command", "")).lower()
        return any(d in cmd for d in destructive)
    if name in ("write_file", "edit_file"):
        return True  # File modifications always ask
    return False


def _execute_tool(name: str, args: dict, handlers: dict) -> str:
    """Execute a tool and return result."""
    handler = handlers.get(name)
    if not handler: return f"Unknown tool: {name}"
    try: return str(handler(**args))
    except Exception as e: return f"Tool error: {e}"


def reply_block(result: dict):
    """Render agent reply."""
    console.print()
    if result.get("reasoning"):
        r = result["reasoning"].replace("\n", " ")[:300]
        console.print(Panel(r, border_style="dim", title="💭"))
    console.print(Markdown(result.get("reply", "")))
    actions = result.get("actions", [])
    t = result.get("tokens", {}).get("total", 0)
    if actions:
        console.print(f"[dim]🔧 {' '.join(actions)}  🪙 {t:,}[/]")
    console.print()


def footer_line(model: str, context: str, skill: str = "", tokens: int = 0):
    """Compact footer."""
    parts = [f"[bold]Ely[/] [dim]· {model} · ctx: {context}[/]"]
    if skill: parts.append(f"[dim]· {skill}[/]")
    if tokens: parts.append(f"[dim]· 🪙 {tokens:,}[/]")
    console.print(Rule(" ".join(parts), style="dim"))
