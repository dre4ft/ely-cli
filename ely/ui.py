"""Claude Code-like CLI UI renderer."""

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

console = Console()


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
