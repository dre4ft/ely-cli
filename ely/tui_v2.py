"""Polished Claude Code-like rendering with streaming, panels, and clean layout."""

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.live import Live
from rich.rule import Rule
from rich.text import Text
from rich.prompt import Confirm
from rich.layout import Layout

console = Console()

# Permission whitelist
PERM_ALWAYS = set()


class Conversation:
    """Tracks all messages in the current conversation for rendering."""

    def __init__(self):
        self.messages = []  # List of (role, content, meta)

    def add_user(self, msg: str):
        self.messages.append(("user", msg, {}))

    def add_agent(self, msg: str, reasoning: str = "", actions: list = None, tokens: int = 0):
        self.messages.append(("agent", msg, {"reasoning": reasoning, "actions": actions or [], "tokens": tokens}))

    def add_tool(self, name: str, params: str, result: str):
        self.messages.append(("tool", f"{name}: {params} → {result[:100]}", {"name": name, "params": params, "result": result}))

    def render(self, max_history: int = 20) -> str:
        """Render conversation as Rich-markup string."""
        lines = []
        for role, content, meta in self.messages[-max_history:]:
            if role == "user":
                lines.append(f"[bold cyan]▸ {content}[/]")
            elif role == "agent":
                lines.append(content)
                if meta.get("actions"):
                    lines.append(f"[dim]🔧 {' '.join(meta['actions'])}  🪙 {meta.get('tokens', 0):,}[/]")
            elif role == "tool":
                lines.append(f"[dim]  ⚡ {meta.get('name', '')}: {meta.get('result', '')[:120]}[/]")
        return "\n".join(lines)


def show_header(model: str, context: str, workspace: str, skill: str = "", tokens: int = 0):
    """Render fixed header."""
    parts = [f"[bold]Ely[/] [dim]· {model} · ctx: {context} · 📁 {workspace}[/]"]
    if skill: parts.append(f"[dim]· {skill}[/]")
    parts.append(f"[dim]· 🪙 {tokens:,}[/]")
    console.print(" ".join(parts))


def show_help():
    """Render help panel."""
    console.print(Panel(
        "[bold]Commands[/]\n"
        "  [cyan]message[/]     → send to agent\n"
        "  [cyan]?question[/]   → quick LLM, no tools\n"
        "  [cyan]#cmd[/]        → bash directly\n"
        "  [cyan]/help[/]       → this help\n"
        "  [cyan]/context[/]    → manage contexts\n"
        "  [cyan]/skill[/]      → manage skills\n"
        "  [cyan]/diary[/]      → manage diary\n"
        "  [cyan]/subagent[/]   → manage sub-agents\n"
        "  [cyan]/tokens[/]     → show token usage\n"
        "  [cyan]/clear[/]      → clear conversation\n"
        "  [cyan]/pro[/] [cyan]/flash[/] → switch provider\n"
        "  [cyan]Tab[/]         → autocomplete",
        title="Ely CLI", border_style="cyan", padding=(1, 2)))


def show_tool_call(name: str, params: str) -> bool:
    """Show tool call and ask permission if needed. Returns True if allowed."""
    destructive = any(d in str(params).lower() for d in ["rm ", "git reset", "git push --force", "DROP "])

    if destructive and name not in PERM_ALWAYS:
        console.print(Panel(f"[yellow]{name}[/]\n[yellow]{params[:200]}[/]", border_style="yellow", title="⚠️ Permission"))
        if not Confirm.ask("Allow?", default=True):
            console.print("[red]✗ Denied[/]")
            return False
        if Confirm.ask("Always allow?", default=False):
            PERM_ALWAYS.add(name)
    return True


def stream_agent_reply(provider, messages: list, tool_defs: list, tool_handlers: dict,
                       max_turns: int, conv: Conversation) -> dict:
    """Run agent with streaming, updating conversation in real-time.
    Returns {"reply": str, "actions": list, "tokens": dict, "model": str, "reasoning": str}"""

    actions = []
    tokens = {"prompt": 0, "completion": 0, "total": 0}
    reply = ""
    all_reasoning = ""

    for turn in range(max_turns):
        content = ""
        reasoning = ""
        tool_calls = []

        with Live(Text("⏳ ...", style="dim"), refresh_per_second=10, transient=True) as live:
            for event, data in provider.chat_stream(messages, tools=tool_defs if tool_defs else None):
                if event == "content":
                    content += data
                    preview = content.split("\n")[-1][-200:]
                    live.update(Text.from_markup(f"[dim]{preview}[/]"))

                elif event == "reasoning":
                    reasoning += data
                    if len(reasoning) < 300:
                        live.update(Text.from_markup(f"[dim]💭 {reasoning.replace(chr(10), ' ')}[/]", overflow="ellipsis"))

                elif event == "tool_calls":
                    tool_calls = data
                    live.update(Text.from_markup(f"[cyan]🔧 {', '.join(tc['function']['name'] for tc in data)}[/]"))

                elif event == "done":
                    d = data
                    content = d.get("content", content)
                    reasoning = d.get("reasoning", reasoning)
                    tool_calls = d.get("tool_calls", tool_calls)
                    u = d.get("usage", {})
                    tokens["prompt"] += u.get("prompt_tokens", 0)
                    tokens["completion"] += u.get("completion_tokens", 0)
                    tokens["total"] += u.get("total_tokens", 0)

                elif event == "error":
                    return {"reply": f"Error: {data}", "actions": actions, "tokens": tokens, "model": "error", "reasoning": reasoning}

        if reasoning: all_reasoning += reasoning

        if not tool_calls:
            reply = content
            break

        for tc in tool_calls:
            name = tc["function"]["name"]
            args_str = tc["function"]["arguments"]
            try: args = __import__('json').loads(args_str)
            except Exception: args = {}
            actions.append(name)

            # Show tool panel + permission
            if not show_tool_call(name, str(args)[:200]):
                tool_result = "Denied by user."
            else:
                handler = tool_handlers.get(name)
                if handler:
                    try: tool_result = str(handler(**args))
                    except Exception as e: tool_result = f"Error: {e}"
                else:
                    tool_result = f"Unknown: {name}"

            conv.add_tool(name, str(args)[:100], tool_result)

            messages.append({"role": "assistant", "content": content or "",
                             "tool_calls": [{"id": tc.get("id", f"call_{turn}"), "type": "function",
                                             "function": {"name": name, "arguments": args_str}}]})
            messages.append({"role": "tool", "tool_call_id": tc.get("id", f"call_{turn}"), "content": tool_result})

        if turn == max_turns - 3:
            messages.append({"role": "user", "content": "Reponse finale. Plus d'outils."})

    if not reply:
        resp = provider.chat(messages, tools=None)
        reply = resp.get("content", "")

    return {"reply": reply, "reasoning": all_reasoning, "actions": actions, "tokens": tokens, "model": "stream"}


def render_reply(conv: Conversation, result: dict):
    """Render final reply with reasoning panel."""
    reasoning = result.get("reasoning", "")
    reply = result.get("reply", "")

    if reasoning:
        short = reasoning.replace("\n", " ")[:300]
        console.print(Panel(short, border_style="dim", title="💭", padding=(0, 1)))

    console.print(Markdown(reply))
    actions = result.get("actions", [])
    t = result.get("tokens", {}).get("total", 0)
    if actions:
        console.print(f"[dim]🔧 {' '.join(actions)}  🪙 {t:,}[/]")
    console.print()


def show_footer(model: str, context: str, skill: str = "", tokens: int = 0):
    """Minimal footer."""
    parts = [f"[bold]Ely[/] [dim]· {model} · ctx: {context}[/]"]
    if skill: parts.append(f"[dim]· {skill}[/]")
    if tokens: parts.append(f"[dim]· 🪙 {tokens:,}[/]")
    console.print(Rule(" ".join(parts), style="dim"))
    console.print()
