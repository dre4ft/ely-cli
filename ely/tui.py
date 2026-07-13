"""
Cockpit TUI — interface terminal sobre et efficace.
Powered by Textual.

Layout:
  ┌──────────────────────────────────────────────────┐
  │ ELY · gpt-4o-mini · ctx: code · 🔒sandbox · 🪙12k │  <- status bar
  ├────────────────────────────────┬─────────────────┤
  │                                │  ⚡ TOOLS        │
  │  Chat messages...              │  - bash          │
  │                                │  - read_file     │
  │  › user: ...                   │                 │
  │                                │  📔 DIARY        │
  │  Ely: ...                      │  #3 bug fix...   │
  │                                │  #2 project...   │
  ├────────────────────────────────┴─────────────────┤
  │ › prompt...                              [Send]   │
  └──────────────────────────────────────────────────┘
"""

import os
import json
from threading import Thread

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Header, Footer, Input, Static, Button, RichLog,
)
from textual.binding import Binding
from textual.screen import ModalScreen

from ely.agent import chat
from ely.config import get, get_provider_config
from ely.tools import (
    _is_sandbox_enabled, _workspace_dir,
    _load_diary as load_diary,
    get_tool_names,
)


class StatusBar(Static):
    """Top status bar with model, context, sandbox, tokens."""

    def update_status(self, model: str, tokens: dict, context: str, slot: str):
        t = tokens.get("total", 0)
        sandbox_icon = "🔒" if _is_sandbox_enabled() else "💻"
        sandbox_label = "sandbox" if _is_sandbox_enabled() else "direct"
        ws = os.path.basename(_workspace_dir())
        self.update(
            f"[bold white]ELY[/] "
            f"[dim]·[/] [cyan]{model}[/] "
            f"[dim]·[/] ctx: [green]{context}[/] "
            f"[dim]·[/] {sandbox_icon} [yellow]{sandbox_label}[/] "
            f"[dim]·[/] 📁 [blue]{ws}[/] "
            f"[dim]·[/] 🪙 [magenta]{t:,}[/]"
        )


class ToolPanel(Static):
    """Shows recently executed tools with arguments."""

    def add_tool(self, name: str):
        current = self.renderable.plain if self.renderable and hasattr(self.renderable, 'plain') else ""
        lines = [l for l in current.split("\n") if l.strip()]
        lines.append(f"  [cyan]⚡[/] {name}")
        lines = lines[-12:]
        self.update("\n".join(lines) if lines else "")


class DiaryPanel(Static):
    """Shows recent diary entries in side panel."""

    def refresh_entries(self):
        entries = load_diary()
        if not entries:
            self.update("[dim](vide)[/]")
            return
        lines = []
        for e in reversed(entries[-5:]):
            tags = f" [dim]{', '.join(e.get('tags', []))}[/]" if e.get("tags") else ""
            lines.append(f"[yellow]#{e['id']}[/]{tags}")
            lines.append(f"  [dim]{e['content'][:80]}[/]")
        self.update("\n".join(lines))


class ChatLog(RichLog):
    """Chat message area with markdown."""

    def add_user_msg(self, text: str):
        self.write(f"\n[bold green]› {text}[/]")

    def add_agent_msg(self, text: str):
        self.write(text)

    def add_error(self, text: str):
        self.write(f"\n[red]✗ {text}[/]")

    def add_info(self, text: str):
        self.write(f"\n[dim]ℹ {text}[/]")


class HelpScreen(ModalScreen):
    """Help modal overlay."""

    BINDINGS = [Binding("escape", "dismiss", "Fermer")]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("[bold]Raccourcis[/]", id="help-title"),
            Static(
                "  [cyan]Enter[/]         Envoyer\n"
                "  [cyan]Ctrl+P[/]        Mode Pro / Flash\n"
                "  [cyan]Ctrl+C[/]        Changer contexte\n"
                "  [cyan]Ctrl+D[/]        Voir diary\n"
                "  [cyan]Ctrl+L[/]        Effacer l'écran\n"
                "  [cyan]Ctrl+Q[/]        Quitter\n"
                "  [cyan]Esc[/]          Fermer cette aide\n"
                "\n"
                "  [cyan]/explain <t>[/]   Expliquer du code\n"
                "  [cyan]/fix <c>[/]       Corriger un bug\n"
                "  [cyan]/context <c>[/]   Contexte (default/code/sysadmin/research)\n"
                "  [cyan]/pro[/]          Mode Pro\n"
                "  [cyan]/flash[/]        Mode rapide\n"
                "  [cyan]/diary[/]        Voir le diary\n"
                "  [cyan]/clear[/]        Nouvelle conversation\n",
                id="help-body",
            ),
            Button("Fermer", variant="primary", id="help-close"),
            id="help-container",
        )

    @on(Button.Pressed, "#help-close")
    def close_help(self):
        self.dismiss()


class CockpitApp(App):
    """Main cockpit TUI application."""

    CSS = """
    #status-bar {
        height: 1;
        padding: 0 1;
        background: #1a1a2e;
        color: #e0e0e0;
    }
    #main-area {
        height: 1fr;
    }
    #chat-log {
        width: 3fr;
        border: solid #333;
        padding: 0 1;
    }
    #side-panel {
        width: 28;
        border: solid #333;
        padding: 0 1;
        background: #0d1117;
    }
    #side-title, #diary-title {
        height: 1;
        padding: 0 1;
        text-style: bold;
        color: #58a6ff;
        background: #161b22;
    }
    #tool-panel {
        height: 2fr;
        padding: 0 1;
    }
    #diary-panel {
        height: 1fr;
        padding: 0 1;
    }
    #info-panel {
        height: auto;
        padding: 0 1;
    }
    #input-area {
        height: 3;
        padding: 0 1;
        border: solid #333;
        background: #0d1117;
    }
    #prompt {
        width: 1fr;
    }
    #send-btn {
        width: 10;
    }
    #help-container {
        width: 52;
        height: auto;
        border: thick #58a6ff;
        background: #0d1117;
        padding: 1;
        align: center middle;
    }
    #help-title {
        text-style: bold;
        color: #58a6ff;
        padding: 0 1;
    }
    #help-body {
        padding: 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+p", "toggle_pro", "Pro/Flash"),
        Binding("ctrl+c", "cycle_context", "Contexte"),
        Binding("ctrl+d", "show_diary", "Diary"),
        Binding("ctrl+l", "clear_screen", "Clear"),
        Binding("ctrl+q", "quit", "Quitter"),
        Binding("f1", "show_help", "Aide"),
    ]

    def __init__(self):
        super().__init__()
        self._history = []
        self._current_context = "default"
        self._slot = "provider"
        self._tokens = {"prompt": 0, "completion": 0, "total": 0}
        self._model = get_provider_config("provider")["model"]
        self._history_file = os.path.expanduser("~/.ely/chat_history.json")
        self._contexts = ["default", "code", "sysadmin", "research"]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield StatusBar(id="status-bar")
        yield Horizontal(
            ChatLog(highlight=True, markup=True, wrap=True, id="chat-log"),
            Vertical(
                Static("⚡ TOOLS", id="side-title"),
                ToolPanel("", id="tool-panel"),
                Static("📔 DIARY", id="diary-title"),
                DiaryPanel("", id="diary-panel"),
                Static("", id="info-panel"),
                id="side-panel",
            ),
            id="main-area",
        )
        yield Horizontal(
            Input(placeholder="› Pose ta question...", id="prompt"),
            Button("Send", variant="primary", id="send-btn"),
            id="input-area",
        )
        yield Footer()

    def on_mount(self):
        """Initialize the cockpit."""
        self._load_history()
        cfg = get_provider_config(self._slot)
        self._model = cfg["model"]

        # Restore recent history
        chat_log = self.query_one("#chat-log", ChatLog)
        for h in self._history[-10:]:
            role = h.get("role", "")
            content = h.get("content", "")
            if role == "user":
                chat_log.add_user_msg(content)
            elif role == "assistant":
                chat_log.add_agent_msg(content)

        self._update_status()
        self._update_side_info()
        self._refresh_diary()
        self.query_one("#prompt", Input).focus()

    def _update_status(self):
        bar = self.query_one("#status-bar", StatusBar)
        bar.update_status(self._model, self._tokens, self._current_context, self._slot)

    def _update_side_info(self):
        info = self.query_one("#info-panel", Static)
        ws = _workspace_dir()
        sandbox = "🔒 sandbox" if _is_sandbox_enabled() else "💻 direct"
        tools = ", ".join(get_tool_names())
        info.update(
            f"[dim]📁 {os.path.basename(ws)}\n"
            f"🐚 {sandbox}\n"
            f"🪙 {self._tokens['total']:,} tok\n"
            f"💬 {len(self._history)} msg\n"
            f"\n[dim]Outils:[/]\n[dim]{tools}[/]"
        )

    def _refresh_diary(self):
        diary = self.query_one("#diary-panel", DiaryPanel)
        diary.refresh_entries()

    def _load_history(self):
        if os.path.isfile(self._history_file):
            try:
                with open(self._history_file) as f:
                    self._history = json.load(f)
            except Exception:
                self._history = []

    def _save_history(self):
        os.makedirs(os.path.dirname(self._history_file), exist_ok=True)
        with open(self._history_file, "w") as f:
            json.dump(self._history[-100:], f, indent=2)

    def action_toggle_pro(self):
        self._slot = "pro_provider" if self._slot == "provider" else "provider"
        cfg = get_provider_config(self._slot)
        self._model = cfg["model"]
        self._update_status()
        self._update_side_info()
        self.query_one("#chat-log", ChatLog).add_info(f"Slot: {self._slot} ({self._model})")

    def action_cycle_context(self):
        idx = self._contexts.index(self._current_context) if self._current_context in self._contexts else 0
        self._current_context = self._contexts[(idx + 1) % len(self._contexts)]
        self._update_status()
        self._update_side_info()
        self.query_one("#chat-log", ChatLog).add_info(f"Contexte: {self._current_context}")

    def action_clear_screen(self):
        self.query_one("#chat-log", ChatLog).clear()
        self._history = []
        self._save_history()
        self._refresh_diary()

    def action_show_help(self):
        self.push_screen(HelpScreen())

    def action_show_diary(self):
        entries = load_diary()
        chat_log = self.query_one("#chat-log", ChatLog)
        if not entries:
            chat_log.add_info("Diary vide.")
            return
        chat_log.add_info(f"📔 Diary ({len(entries)} entrées):")
        for e in entries[-15:]:
            tags = f" [{' '.join(e.get('tags', []))}]" if e.get("tags") else ""
            chat_log.add_info(f"  #{e['id']} [{e['timestamp']}]{tags}\n    {e['content'][:200]}")

    @on(Button.Pressed, "#send-btn")
    async def on_send_button(self):
        await self._process_input()

    @on(Input.Submitted, "#prompt")
    async def on_input_submitted(self):
        await self._process_input()

    async def _process_input(self):
        prompt_widget = self.query_one("#prompt", Input)
        user_input = prompt_widget.value.strip()
        if not user_input:
            return
        prompt_widget.value = ""

        chat_log = self.query_one("#chat-log", ChatLog)
        tool_panel = self.query_one("#tool-panel", ToolPanel)
        status_widget = self.query_one("#status-bar", StatusBar)

        # Handle slash commands
        if user_input.startswith("/"):
            self._handle_command(user_input, chat_log)
            return

        chat_log.add_user_msg(user_input)
        self._history.append({"role": "user", "content": user_input})

        status_widget.update("[bold yellow]⏳ Réflexion...[/]")

        result_container = {}

        def run_agent():
            try:
                result_container["result"] = chat(
                    message=user_input,
                    history=self._history[:-1],
                    context=self._current_context,
                    slot=self._slot,
                )
            except Exception as e:
                result_container["error"] = str(e)

        thread = Thread(target=run_agent)
        thread.start()

        dots = 0
        while thread.is_alive():
            dots = (dots + 1) % 4
            status_widget.update(
                f"[dim]Ely[/] [yellow]·[/] [cyan]{self._model}[/] [yellow]·[/] "
                f"ctx: [green]{self._current_context}[/] "
                f"[yellow]{'◌' * dots}{' ' * (3 - dots)}[/]"
            )
            await self._sleep(0.15)

        thread.join()

        if "error" in result_container:
            chat_log.add_error(f"Erreur: {result_container['error']}")
            self._update_status()
            return

        result = result_container.get("result", {})
        reply = result.get("reply", "")
        actions = result.get("actions", [])
        t = result.get("tokens", {})

        self._tokens["prompt"] += t.get("prompt", 0)
        self._tokens["completion"] += t.get("completion", 0)
        self._tokens["total"] += t.get("total", 0)

        chat_log.add_agent_msg(reply)
        self._history.append({"role": "assistant", "content": reply})

        for action in actions:
            tool_panel.add_tool(action)

        self._save_history()
        self._update_status()
        self._update_side_info()
        self._refresh_diary()

    def _handle_command(self, cmd: str, chat_log: ChatLog):
        parts = cmd.split(maxsplit=1)
        command = parts[0].lower()

        if command == "/help":
            self.action_show_help()
        elif command == "/clear":
            self.action_clear_screen()
            chat_log.add_info("Conversation effacée.")
        elif command == "/pro":
            self._slot = "pro_provider"
            cfg = get_provider_config(self._slot)
            self._model = cfg["model"]
            chat_log.add_info(f"Mode Pro: {self._model}")
            self._update_status()
            self._update_side_info()
        elif command == "/flash":
            self._slot = "provider"
            cfg = get_provider_config(self._slot)
            self._model = cfg["model"]
            chat_log.add_info(f"Mode Flash: {self._model}")
            self._update_status()
            self._update_side_info()
        elif command == "/tokens":
            chat_log.add_info(
                f"Tokens: {self._tokens['total']:,} total "
                f"({self._tokens['prompt']:,} prompt, {self._tokens['completion']:,} completion)"
            )
        elif command == "/diary":
            self.action_show_diary()
        elif command == "/context" and len(parts) > 1:
            new_ctx = parts[1]
            if new_ctx in self._contexts:
                self._current_context = new_ctx
                chat_log.add_info(f"Contexte: {self._current_context}")
                self._update_status()
                self._update_side_info()
            else:
                chat_log.add_info(f"Contextes: {', '.join(self._contexts)}")
        elif command == "/model" and len(parts) > 1:
            self._model = parts[1]
            os.environ["ELY_PROVIDER_MODEL"] = self._model
            chat_log.add_info(f"Modèle: {self._model}")
            self._update_status()
            self._update_side_info()
        elif command in ("/exit", "/quit"):
            self.exit()
        else:
            chat_log.add_user_msg(cmd)
            self._history.append({"role": "user", "content": cmd})
            self.query_one("#prompt", Input).value = cmd

    async def _sleep(self, seconds: float):
        import asyncio
        await asyncio.sleep(seconds)


def run_tui():
    """Entry point for the cockpit TUI."""
    app = CockpitApp()
    app.run()
