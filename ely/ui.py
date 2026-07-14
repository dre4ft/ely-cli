"""Claude Code-like CLI UI renderer."""

from rich.console import Console
from rich.markdown import Markdown
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

console = Console()


def echo_user(msg: str):
    """Echo user message with Claude-style prefix."""
    console.print(f"[bold cyan]⏺[/] [bold]{msg}[/]")


def thinking_live(status_cb, chat_fn, **chat_kwargs) -> dict:
    """Run the agent with a live thinking display. Returns chat result dict."""
    import time as _t

    _start = _t.time()
    _status_action = ""
    _status_reasoning = ""

    def _cb(event, data):
        nonlocal _status_action, _status_reasoning
        if event == "thinking":
            _status_action = data
        elif event == "reasoning":
            _status_reasoning = data[-250:]
        elif event == "tool_call":
            _status_action = f"🔧 {data[:120]}"
        elif event == "tool_result":
            _status_action = f"   ⮡ {data[:120]}"
        elif event == "reply":
            _status_action = ""

    # Wrap user callback with our interceptor
    user_cb = chat_kwargs.pop("status_cb", None)

    def combined_cb(event, data):
        _cb(event, data)
        if user_cb:
            user_cb(event, data)

    chat_kwargs["status_cb"] = combined_cb

    with Live(Text("🤔 Réflexion...", style="dim"), refresh_per_second=8, transient=True) as live:
        import threading as _th
        _result = {}
        _done = _th.Event()

        def _run():
            try:
                r = chat_fn(**chat_kwargs)
                _result.update(r)
            except Exception as e:
                _result.update({"reply": f"Error: {e}", "actions": [], "tokens": {}, "model": "error"})
            finally:
                _done.set()

        _thr = _th.Thread(target=_run, daemon=True)
        _thr.start()

        while _thr.is_alive():
            elapsed = _t.time() - _start
            parts = [Text.from_markup(f"[dim]⏳ {elapsed:.0f}s · {_status_action or 'Réflexion...'}[/]", overflow="ellipsis")]
            if _status_reasoning:
                short = _status_reasoning.replace("\n", " ")[-200:]
                parts.append(Text.from_markup(f"[dim]💭 {short}[/]", overflow="ellipsis"))
            live.update(Text("\n").join(parts))
            _done.wait(0.1)

        _thr.join(timeout=2)

    return _result


def reply_header(model: str = "", context: str = "", skill: str = "", tokens: int = 0) -> None:
    """Print a compact header line before the reply."""
    parts = [f"[dim]Ely · {model}[/]" if model else "[dim]Ely[/]"]
    if context:
        parts.append(f"[dim]ctx: {context}[/]")
    if skill:
        parts.append(f"[dim]🧠 {skill}[/]")
    if tokens:
        parts.append(f"[dim]🪙 {tokens:,}[/]")
    console.print(" · ".join(parts))


def tool_call_summary(actions: list[str]) -> None:
    """Print a compact tool call summary."""
    if actions:
        console.print(f"[dim]🔧 {'  '.join(actions)}[/]")


def reasoning_panel(text: str) -> None:
    """Show model reasoning in a compact collapsible panel."""
    lines = text.strip().split("\n")[:5]
    body = "\n".join(lines)
    if len(text.strip().split("\n")) > 5:
        body += f"\n[dim]... ({len(text)} chars)[/]"
    console.print(Panel(body, border_style="dim", padding=(0, 1), title="💭 Réflexion"))


def reply_body(text: str) -> None:
    """Render the agent's markdown reply."""
    console.print()
    console.print(Markdown(text))
    console.print()


def footer(model: str, context: str, skill: str, tokens: int, actions: list[str]) -> None:
    """Print a clean footer with session info."""
    parts = [f"[bold]Ely[/]", f"[cyan]{model}[/]", f"ctx: [green]{context}[/]"]
    if skill:
        parts.append(skill)
    parts.append(f"[dim]🪙 {tokens:,}[/]")
    if actions:
        parts.append(f"[dim]🔧 {' '.join(actions)}[/]")
    console.print(Rule(" · ".join(parts), style="dim"))
    console.print()
