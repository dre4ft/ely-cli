#!/usr/bin/env python3
"""
Ely — Standalone CLI AI Agent.
Extracted from Elyria's Ely Copilot.

Usage:
  ely                    Cockpit TUI mode (default)
  ely "question"         Single-shot mode
  ely --tui              Force TUI mode
  ely --no-tui           Simple REPL mode
  ely --context code     Set context
  ely --pro              Use pro provider
"""

import sys
import os
import json
import atexit
import readline  # noqa
import argparse

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape as rich_escape
from rich.live import Live
from rich.rule import Rule
from rich.text import Text
from rich.table import Table

from ely.agent import chat, _build_system_prompt
from ely.config import get, get_provider_config, get_bool, get_int
from ely.memory import build_memory_prompt
from ely.tools import get_tools

console = Console()

from ely.config import get_ely_dir
HISTORY_FILE = os.path.join(get_ely_dir(), "chat_history.json")

# ── Slash command registry (name → description, subcommands) ──

COMMANDS = {
    "/explain":    ("Expliquer un concept ou du code", None),
    "/fix":        ("Corriger un bug dans le code", None),
    "/refactor":   ("Refactoriser du code", None),
    "/test":       ("Écrire des tests pour du code", None),
    "/context":    ("Gérer le contexte (list, activate, create)", ["list", "activate", "create", "delete"]),
    "/pro":        ("Basculer sur le provider pro", None),
    "/flash":      ("Basculer sur le provider flash/rapide", None),
    "/tokens":     ("Afficher le total de tokens consommés", None),
    "/clear":      ("Effacer l'historique de conversation", None),
    "/diary":      ("Gérer le diary persistant", ["list", "add", "search", "get"]),
    "/skill":      ("Gérer les compétences", ["list", "activate", "deactivate", "delete"]),
    "/mcp":        ("Gérer les serveurs MCP connectés", ["list", "reload"]),
    "/subagent":   ("Gérer les sous-agents en arrière-plan", ["list", "kill"]),
    "/reload":     ("Recharger les outils customs et skills à chaud", None),
    "/help":       ("Afficher cette aide", None),
}

# Commands forwarded to the LLM (not handled locally)
LLM_COMMANDS = {"/explain", "/fix", "/refactor", "/test"}


HISTFILE = os.path.join(get_ely_dir(), "history")


def _setup_readline():
    """Configure readline with tab completion and persistent history."""
    # Load history from previous sessions
    if os.path.isfile(HISTFILE):
        try:
            readline.read_history_file(HISTFILE)
        except Exception:
            pass

    # macOS uses libedit (not GNU readline), needs different binding
    if "libedit" in (readline.__doc__ or ""):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")

    # Only space and tab as word delimiters (keep /, -, _ as part of words)
    readline.set_completer_delims(" \t\n")
    readline.set_completer(_command_completer)
    readline.set_history_length(1000)


def _save_readline_history():
    """Persist readline history to disk."""
    try:
        os.makedirs(os.path.dirname(HISTFILE), exist_ok=True)
        readline.write_history_file(HISTFILE)
    except Exception:
        pass


def _command_completer(text: str, state: int) -> str | None:
    """Tab completer that understands command hierarchy."""
    buffer = readline.get_line_buffer()

    if not buffer.startswith("/"):
        return None

    parts = buffer.split()
    cmd = parts[0].lower() if parts else ""
    n_parts = len(parts)
    trailing_space = buffer.endswith(" ")

    matches = []

    # Case 1: completing command name — "/sk<TAB>" → "/skill"
    if n_parts == 1 and not trailing_space:
        matches = [c for c in COMMANDS if c.startswith(text.lower())]
    # Case 1b: command typed, space pressed — show subcommands — "/skill <TAB>"
    elif n_parts == 1 and trailing_space:
        sub = COMMANDS.get(cmd, (None, None))[1]
        if sub:
            matches = sub

    # Case 2: completing first argument (subcommand) — "/skill l<TAB>"
    elif n_parts == 2 and not trailing_space:
        sub = COMMANDS.get(cmd, (None, None))[1]
        if sub:
            matches = [s for s in sub if s.startswith(parts[1].lower())]

    # Case 3: completing second argument — "/skill activate <TAB>"
    elif n_parts == 2 and trailing_space:
        if cmd == "/skill" and parts[1] in ("activate", "deactivate", "delete"):
            from ely.skills import list_skills
            matches = list_skills()
        elif cmd == "/context" and parts[1] in ("activate",):
            from ely.contexts import list_contexts
            matches = [c["name"] for c in list_contexts()]
        elif cmd == "/diary" and parts[1] == "search":
            pass  # free-form, no completion
        elif cmd == "/subagent" and parts[1] == "kill":
            from ely.tools import _background_tasks, _task_lock
            with _task_lock:
                matches = [str(tid) for tid in _background_tasks]

    # Case 4: completing second argument with partial — "/skill activate pen<TAB>"
    elif n_parts == 3 and not trailing_space:
        if cmd == "/skill" and parts[1] in ("activate", "deactivate", "delete"):
            from ely.skills import list_skills
            matches = [s for s in list_skills() if s.startswith(parts[2].lower())]
        elif cmd == "/context" and parts[1] in ("activate",):
            from ely.contexts import list_contexts
            matches = [c["name"] for c in list_contexts() if c["name"].startswith(parts[2].lower())]
        elif cmd == "/subagent" and parts[1] == "kill":
            from ely.tools import _background_tasks, _task_lock
            with _task_lock:
                matches = [str(tid) for tid in _background_tasks if str(tid).startswith(parts[2])]

    try:
        return matches[state]
    except IndexError:
        return None


def _show_help():
    """Display formatted help for all slash commands."""
    table = Table(title="Commandes disponibles", border_style="dim", padding=(0, 1))
    table.add_column("Commande", style="cyan", no_wrap=True)
    table.add_column("Description", style="dim")
    table.add_column("Sous-commandes", style="yellow")

    for cmd, (desc, subs) in COMMANDS.items():
        if cmd in LLM_COMMANDS:
            desc = f"[LLM] {desc}"
        subs_str = ", ".join(subs) if subs else "—"
        table.add_row(cmd, desc, subs_str)

    console.print()
    console.print(table)
    console.print()
    console.print("[bold]?<question>[/] [dim]— Question rapide au LLM (sans outils, sans agent)[/]")
    console.print("[bold]#<commande>[/] [dim]— Exécute une commande bash directement (sandbox ou terminal)[/]")
    console.print("[dim]LLM = envoyé à l'agent | Tab = autocomplétion[/]")


def _load_history():
    if os.path.isfile(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_history(history):
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history[-100:], f, indent=2)


def _handle_diary(user_input: str):
    """Handle /diary commands — user-driven persistent memory."""
    from ely.tools import _load_diary, tool_diary_add, tool_diary_search, tool_diary_get, tool_diary_list

    parts = user_input.split(maxsplit=1)
    sub = parts[1].strip() if len(parts) > 1 else ""

    if not sub or sub == "list":
        console.print(tool_diary_list(10))
    elif sub.startswith("add "):
        content = sub[4:].strip()
        if content:
            console.print(tool_diary_add(content))
        else:
            console.print("[red]Usage: /diary add <texte>[/]")
    elif sub.startswith("search "):
        query = sub[7:].strip()
        if query:
            console.print(tool_diary_search(query))
        else:
            console.print("[red]Usage: /diary search <query>[/]")
    elif sub.startswith("get "):
        try:
            entry_id = int(sub[4:].strip())
            console.print(tool_diary_get(entry_id))
        except ValueError:
            console.print("[red]Usage: /diary get <id>[/]")
    else:
        console.print("[cyan]/diary [list] | /diary add <texte> | /diary search <query> | /diary get <id>[/]")


def _handle_skill(user_input: str):
    """Handle /skill commands — activate/deactivate/list skills."""
    from ely.skills import list_skills, get_active_skills, activate_skill, deactivate_skill, load_skill

    parts = user_input.split(maxsplit=1)
    sub = parts[1].strip() if len(parts) > 1 else ""

    if not sub or sub == "list":
        all_skills = list_skills()
        active = get_active_skills()
        console.print(f"[bold]Compétences disponibles (une seule à la fois) :[/]")
        for s in all_skills:
            marker = "[green]● actif[/]" if s in active else "[dim]○ inactif[/]"
            skill = load_skill(s)
            desc = f" — {skill.description}" if skill and skill.description else ""
            console.print(f"  {marker} [cyan]{s}[/]{desc}")
        if not all_skills:
            console.print("  [dim]Aucune compétence trouvée.[/]")
    elif sub.startswith("activate "):
        name = sub[9:].strip()
        if activate_skill(name):
            console.print(f"[green]✓ Compétence '{name}' activée — mode expert.[/]")
        else:
            console.print(f"[red]Compétence '{name}' introuvable.[/]")
    elif sub.startswith("deactivate "):
        name = sub[11:].strip()
        if deactivate_skill(name):
            console.print(f"[dim]✓ Compétence '{name}' désactivée.[/]")
        else:
            console.print(f"[red]Impossible de désactiver '{name}' (introuvable ou compétence de base).[/]")
    elif sub.startswith("delete "):
        name = sub[7:].strip()
        if name == "ely":
            console.print("[red]Impossible de supprimer la compétence de base 'ely'.[/]")
            return
        import shutil
        from ely.tools import _skills_user_dir
        skill_dir = os.path.join(_skills_user_dir(), name)
        if not os.path.isdir(skill_dir):
            console.print(f"[red]Compétence '{name}' introuvable.[/]")
            return
        deactivate_skill(name)
        shutil.rmtree(skill_dir)
        console.print(f"[dim]✓ Compétence '{name}' supprimée.[/]")
    else:
        console.print("[cyan]/skill [list] | /skill activate <nom> | /skill deactivate <nom> | /skill delete <nom>[/]")


def _handle_mcp(user_input: str):
    """Handle /mcp commands — manage MCP server connections."""
    from ely.mcp import get_mcp_manager

    parts = user_input.split(maxsplit=1)
    sub = parts[1].strip() if len(parts) > 1 else ""

    mgr = get_mcp_manager()

    if not sub or sub == "list":
        if not mgr.clients:
            mgr.load_from_config()

        status = mgr.get_status()
        if not status:
            console.print("[dim]Aucun serveur MCP configuré.[/]")
            console.print("[dim]Ajoute des serveurs dans ely.yaml → mcp.servers, puis /mcp reload[/]")
            return

        console.print(f"[bold]Serveurs MCP ({len(status)}) :[/]")
        for s in status:
            icon = "[green]●[/]" if s["connected"] else "[red]○[/]"
            args_hint = " ".join(s.get("args", [])[:2]) if s.get("args") else ""
            if s["connected"]:
                detail = f"{s['tools_count']} tools, {s['resources_count']} resources"
            elif s.get("error"):
                detail = f"[red]{s['error']}[/]"
            else:
                detail = "[dim]non connecté — /mcp reload pour essayer[/]"
            console.print(f"  {icon} [cyan]{s['name']}[/] · {s.get('command', '')} {args_hint}")
            console.print(f"    {detail}")
    elif sub == "reload":
        mgr.close_all()
        mgr.load_from_config()
        try:
            mgr.connect_all()
            status = mgr.get_status()
            if not status:
                console.print("[dim]Aucun serveur MCP configuré.[/]")
                return
            for s in status:
                icon = "[green]●[/]" if s["connected"] else "[red]○[/]"
                detail = f"{s['tools_count']} tools" if s["connected"] else s.get("error", "échec")
                console.print(f"  {icon} {s['name']}: {detail}")
        except Exception as e:
            console.print(f"[red]Erreur MCP: {e}[/]")
    else:
        console.print("[cyan]/mcp [list] | /mcp reload[/]")


def _handle_subagent(user_input: str):
    """Handle /subagent commands — manage background sub-agents."""
    from ely.tools import _background_tasks, _task_lock

    parts = user_input.split(maxsplit=1)
    sub = parts[1].strip() if len(parts) > 1 else ""

    if not sub or sub == "list":
        with _task_lock:
            if not _background_tasks:
                console.print("[dim]Aucun sous-agent en cours.[/]")
                return
            console.print(f"[bold]Sous-agents ({len(_background_tasks)}) :[/]")
            import time as _t
            for tid, t in sorted(_background_tasks.items()):
                agent = t.get("agent")
                if agent and agent.done:
                    icon, status = "[green]✓[/]", "terminé"
                else:
                    elapsed = _t.time() - t.get("started", 0)
                    icon, status = "[yellow]⏳[/]", f"en cours ({elapsed:.0f}s)"
                console.print(f"  {icon} [cyan]#{tid}[/] {status}: {t['desc'][:80]}")
    elif sub.startswith("kill "):
        try:
            tid = int(sub[5:].strip())
        except ValueError:
            console.print("[red]Usage: /subagent kill <id>[/]")
            return
        with _task_lock:
            t = _background_tasks.get(tid)
        if not t:
            console.print(f"[red]Sous-agent #{tid} introuvable.[/]")
            return
        agent = t.get("agent")
        if agent and not agent.done:
            try:
                agent.close()
            except Exception:
                pass
            with _task_lock:
                _background_tasks.pop(tid, None)
            console.print(f"[dim]✓ Sous-agent #{tid} killed.[/]")
        elif agent and agent.done:
            with _task_lock:
                _background_tasks.pop(tid, None)
            console.print(f"[dim]✓ Sous-agent #{tid} déjà terminé, nettoyé.[/]")
        else:
            with _task_lock:
                _background_tasks.pop(tid, None)
            console.print(f"[dim]✓ Sous-agent #{tid} nettoyé.[/]")
    else:
        console.print("[cyan]/subagent [list] | /subagent kill <id>[/]")


def _handle_context(user_input: str, current_context: str) -> str:
    """Handle /context commands. Returns the (possibly updated) context name."""
    from ely.contexts import (
        list_contexts, get_context, create_context, delete_context, save_active_context
    )

    parts = user_input.split(maxsplit=1)
    sub = parts[1].strip() if len(parts) > 1 else ""

    if not sub or sub == "list":
        contexts = list_contexts()
        console.print(f"[bold]Contextes disponibles :[/]")
        for c in contexts:
            marker = "[green]● actif[/]" if c["name"] == current_context else "[dim]○[/]"
            console.print(f"  {marker} [cyan]{c['name']}[/] — {c.get('description', '')}")
        if not contexts:
            console.print("  [dim]Aucun contexte trouvé.[/]")
        return current_context

    if sub.startswith("activate "):
        name = sub[9:].strip()
        ctx = get_context(name)
        if ctx:
            save_active_context(name)
            console.print(f"[green]✓ Contexte activé : {name}[/] — {ctx.get('description', '')}")
            return name
        else:
            console.print(f"[red]Contexte '{name}' introuvable. Créez-le avec /context create {name} <description>[/]")
            return current_context

    if sub.startswith("create "):
        args = sub[7:].strip()
        if " " in args:
            name, rest = args.split(" ", 1)
            if " " in rest:
                desc, prompt = rest.split(" ", 1)
            else:
                desc, prompt = rest, ""
        else:
            console.print("[red]Usage: /context create <nom> <description> <prompt>[/]")
            return current_context

        path = create_context(name, desc, prompt)
        console.print(f"[green]✓ Contexte '{name}' créé : {path}[/]")
        return current_context

    if sub.startswith("delete "):
        name = sub[7:].strip()
        if name == current_context:
            console.print("[red]Impossible de supprimer le contexte actif. Changez d'abord avec /context activate.[/]")
            return current_context
        if delete_context(name):
            console.print(f"[dim]✓ Contexte '{name}' supprimé.[/]")
        else:
            console.print(f"[red]Impossible de supprimer '{name}' (introuvable ou contexte système).[/]")
        return current_context

    # Quick-switch: /context <name> (without subcommand)
    ctx = get_context(sub)
    if ctx:
        save_active_context(sub)
        console.print(f"[green]✓ Contexte : {sub}[/] — {ctx.get('description', '')}")
        return sub

    console.print("[cyan]/context [list] | /context activate <nom> | /context create <nom> <desc> | /context delete <nom>[/]")
    return current_context


def single_shot(query: str, context: str = "default", slot: str = "provider"):
    """Single question — print reply and exit."""
    result = chat(message=query, context=context, slot=slot)
    console.print(Markdown(result["reply"]))
    t = result.get("tokens", {})
    if t.get("total", 0) > 0:
        console.print(
            f"\n[dim]🪙 {t['total']:,} tokens | "
            f"🧠 {result.get('model', '?')} | "
            f"🔧 {', '.join(result.get('actions', []))}[/]"
        )


def repl(context: str = "", slot: str = "provider", classic_ui: bool = False, stream_ui: bool = False):
    """Simple REPL mode without full TUI."""
    history = _load_history()
    total_tokens = {"prompt": 0, "completion": 0, "total": 0}
    cfg = get_provider_config(slot)

    from ely.tools import _is_sandbox_enabled, _workspace_dir
    from ely.skills import get_active_skills, build_skills_status_line, load_active_skills

    # Load persisted skill activation
    load_active_skills()
    from ely.contexts import load_active_context, save_active_context

    # Load persisted context, fall back to CLI arg or default
    if not context:
        context = load_active_context()
    save_active_context(context)

    sandbox = "sandbox" if _is_sandbox_enabled() else "direct"
    ws = os.path.basename(_workspace_dir())

    active = get_active_skills()
    skill_status = build_skills_status_line()
    if skill_status:
        skill_status = f" · {skill_status}"

    _setup_readline()

    def _status_footer():
        """Print persistent status footer before the input prompt."""
        model = get_provider_config(slot)["model"]
        sk = build_skills_status_line()
        sandbox_label = "sandbox" if _is_sandbox_enabled() else "direct"
        wspace = os.path.basename(_workspace_dir())
        if classic_ui:
            parts = [f"[bold]Ely[/] · [cyan]{model}[/] · ctx: [green]{context}[/] · bash: [yellow]{sandbox_label}[/] · 📁 [blue]{wspace}[/]"]
        else:
            parts = [f"[bold]Ely[/]  [cyan]{model}[/]  ctx=[green]{context}[/]  bash=[yellow]{sandbox_label}[/]  📁 [blue]{wspace}[/]"]
        if sk:
            parts.append(sk)
        parts.append("[dim]🪙 {:,}[/]".format(total_tokens['total']))
        return " · ".join(parts)

    if stream_ui:
        from ely.tui_v2 import show_header, show_help
        show_header(cfg["model"], context, ws, skill_status)
    else:
        console.print("[dim]?question = LLM sans tools | #commande = bash | /help | Tab | exit[/]")

    while True:
        try:
            if not stream_ui:
                console.print(f"[dim]{_status_footer()}[/]")
            user_input = console.input("[bold green]›[/] ").strip()
            if user_input:
                _save_readline_history()
        except (KeyboardInterrupt, EOFError):
            console.print("\nAu revoir !")
            _save_readline_history()
            from ely.tools import cleanup_sandbox
            cleanup_sandbox()
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "q"):
            _save_history(history)
            _save_readline_history()
            from ely.tools import cleanup_sandbox
            cleanup_sandbox()
            break

        # ── Quick LLM query: ?question → no tools, text only ──
        if user_input.startswith("?"):
            query = user_input[1:].strip()
            if query:
                from ely.providers import create_provider
                provider = create_provider(get_provider_config(slot))
                console.print("[dim]🤔 Réflexion...[/]")
                try:
                    # Build messages with conversation history (no system prompt, no tools)
                    msgs = []
                    for h in history[-8:]:
                        role = h.get("role", "user")
                        content = h.get("content", "")
                        if role in ("user", "assistant"):
                            msgs.append({"role": role, "content": str(content)[:2500]})
                    msgs.append({"role": "user", "content": query})
                    resp = provider.chat(messages=msgs, tools=None)
                    console.print(Markdown(resp.get("content", "Pas de réponse.")))
                except Exception as e:
                    console.print(f"[red]Erreur: {e}[/]")
            continue

        # ── Direct bash command: #ls -la ──
        if user_input.startswith("#"):
            cmd = user_input[1:].strip()
            if cmd:
                from ely.tools import _run_direct
                output = _run_direct(cmd, sanitize=False)
                console.print(f"[dim]$ {cmd}[/]")
                console.print(output)
            continue

        # ── Slash command dispatch ──
        if user_input.startswith("/"):
            cmd_parts = user_input.split()
            cmd = cmd_parts[0].lower()

            if cmd == "/help":
                _show_help()
                continue
            if cmd == "/clear":
                history = []
                total_tokens = {"prompt": 0, "completion": 0, "total": 0}
                _save_history(history)
                console.clear()
                parts = ["[dim]✨ Conversation purgée.[/]"]
                parts.append("[dim]   Chat history + tokens: vidés[/]")
                # Check if diary/memory exist
                from ely.tools import _load_diary
                diary_count = len(_load_diary())
                from ely.memory import get_situation
                mem = get_situation("default")
                if diary_count > 0:
                    parts.append(f"[dim]   Diary: {diary_count} entrées persistées (non affectées)[/]")
                if mem:
                    parts.append("[dim]   Mémoire: conservée (non affectée)[/]")
                console.print("\n".join(parts) + "\n")
                continue
            if cmd == "/tokens":
                console.print(f"[dim]🪙 Total: {total_tokens['total']:,} tokens (prompt: {total_tokens['prompt']:,}, completion: {total_tokens['completion']:,})[/]")
                continue
            if cmd == "/reload":
                from ely.tools import reload_custom
                reload_custom()
                console.print("[green]✓ Tools customs et skills rechargés.[/]")
                continue
            if cmd.startswith("/diary"):
                _handle_diary(user_input)
                continue
            if cmd.startswith("/skill"):
                _handle_skill(user_input)
                continue
            if cmd.startswith("/mcp"):
                _handle_mcp(user_input)
                continue
            if cmd.startswith("/subagent"):
                _handle_subagent(user_input)
                continue
            if cmd.startswith("/context"):
                context = _handle_context(user_input, context)
                continue
            if cmd in ("/pro", "/flash"):
                slot = "pro_provider" if cmd == "/pro" else "provider"
                cfg = get_provider_config(slot)
                skill_line = build_skills_status_line()
                console.print(f"[green]✓ Provider : [bold]{cfg['model']}[/] · ctx: {context}{' · ' + skill_line if skill_line else ''}[/]")
                continue

            # LLM commands — pass through to the agent
            if cmd in LLM_COMMANDS:
                pass  # fall through to normal agent processing below
            else:
                # Unknown slash command — suggest similar
                similar = [c for c in COMMANDS if c.startswith(cmd[:3])][:3]
                if similar:
                    console.print(f"[red]Commande inconnue : {cmd}[/] — essayez {', '.join(similar)}")
                else:
                    console.print(f"[red]Commande inconnue : {cmd}[/] — tapez /help pour la liste")
                continue

        history.append({"role": "user", "content": user_input})

        # ── Streaming UI (Claude Code-like) ──
        if stream_ui:
            from ely.tui_v2 import (Conversation, stream_agent_reply, render_reply,
                                     show_footer, show_header)

            system_prompt = _build_system_prompt(context)
            mem = build_memory_prompt("default")
            if mem: system_prompt += mem

            from ely.providers import create_provider
            provider = create_provider(get_provider_config(slot))
            tool_defs, tool_handlers = get_tools()

            msgs = [{"role": "system", "content": system_prompt}]
            for h in history[-10:]:
                if h.get("role") in ("user", "assistant"):
                    msgs.append({"role": h["role"], "content": str(h.get("content", ""))[:2500]})
            msgs.append({"role": "user", "content": user_input})

            conv = Conversation()
            result = stream_agent_reply(provider, msgs, tool_defs, tool_handlers,
                                        get_int("agent", "max_turns", 8), conv)
            reply = result.get("reply", "")
            actions = result.get("actions", [])
            t = result.get("tokens", {})
            total_tokens["prompt"] += t.get("prompt", 0)
            total_tokens["completion"] += t.get("completion", 0)
            total_tokens["total"] += t.get("total", 0)
            history.append({"role": "assistant", "content": reply})
            _save_history(history)
            render_reply(conv, result)
            show_footer(result.get("model", cfg["model"]), context,
                        skill=build_skills_status_line(), tokens=total_tokens["total"])
            continue

        # ── Call agent with live status display ──
        import time as _t
        _start = _t.time()
        _current_action = ""
        _current_tool = ""
        _current_result = ""
        _reasoning_snippet = ""
        _turn_info = ""

        def _status_cb(event, data):
            nonlocal _current_action, _current_tool, _current_result, _reasoning_snippet, _turn_info
            if event == "thinking":
                _turn_info = data
            elif event == "reasoning":
                _reasoning_snippet = data[-250:]
            elif event == "tool_call":
                _current_tool = data[:100]
                _current_result = ""
            elif event == "tool_result":
                _current_result = data[:120]
            elif event == "reply":
                _current_action = "✅ Réponse"

        try:
            with Live(Text("🤔 Réflexion...", style="dim"), refresh_per_second=8, transient=True) as live:
                def _update_live(event, data):
                    _status_cb(event, data)
                    elapsed = _t.time() - _start
                    parts = [Text.from_markup(f"[dim]⏱ {elapsed:.0f}s · {rich_escape(_turn_info or 'Réflexion...')}[/]", overflow="ellipsis")]
                    if _reasoning_snippet:
                        short = rich_escape(_reasoning_snippet.replace("\n", " ")[-200:])
                        parts.append(Text.from_markup(f"[dim]💭 {short}[/]", overflow="ellipsis"))
                    if _current_tool:
                        parts.append(Text.from_markup(f"[cyan]🔧 {rich_escape(_current_tool)}[/]", overflow="ellipsis"))
                    if _current_result:
                        parts.append(Text.from_markup(f"[dim]   ⮡ {rich_escape(_current_result)}[/]", overflow="ellipsis"))
                    # Sub-agent statuses in magenta
                    from ely.subagent import get_sub_statuses
                    subs = get_sub_statuses()
                    for sid, s in sorted(subs.items()):
                        parts.append(Text.from_markup(f"[magenta]  ⚡ {rich_escape(s)}[/]", overflow="ellipsis"))
                    live.update(Text("\n").join(parts))

                result = chat(
                    message=user_input,
                    history=history[:-1],
                    context=context,
                    slot=slot,
                    status_cb=_update_live,
                )
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Interrompu.[/]")
            history.pop()
            continue

        reply = result.get("reply", "")
        reasoning = result.get("reasoning", "")
        actions = result.get("actions", [])
        t = result.get("tokens", {})
        total_tokens["prompt"] += t.get("prompt", 0)
        total_tokens["completion"] += t.get("completion", 0)
        total_tokens["total"] += t.get("total", 0)

        history.append({"role": "assistant", "content": reply})
        _save_history(history)

        if classic_ui:
            # ── Classic UI ──
            console.print()
            console.print(Markdown(reply))
            if actions:
                console.print(f"[dim]🔧 {', '.join(actions)}  🪙 {t.get('total', 0):,}[/]")
            console.print()
        else:
            # ── Claude Code-like UI ──
            from ely.ui import reasoning_panel, reply_body

            if reasoning:
                reasoning_panel(reasoning)

            reply_body(reply)
            if actions:
                console.print(f"[dim]🔧 {'  '.join(actions)}  🪙 {t.get('total', 0):,}[/]")
            console.print()


def main():
    parser = argparse.ArgumentParser(description="Ely — CLI AI Agent")
    parser.add_argument("query", nargs="*", help="Question (single-shot mode)")
    parser.add_argument("--config", default="", help="Path to ely.yaml config file")
    parser.add_argument("--tui", action="store_true", default=False, help="TUI cockpit mode")
    parser.add_argument("--no-tui", action="store_true", help="Simple REPL mode (default)")
    parser.add_argument("--classic", action="store_true", default=False, help="Classic UI (original rendering)")
    parser.add_argument("--stream", action="store_true", default=False, help="Streaming UI with permissions (Claude Code-like)")
    parser.add_argument("--context", default="default", help="Context (default, code, sysadmin, research)")
    parser.add_argument("--pro", action="store_true", help="Use pro provider")
    parser.add_argument("--model", default="", help="Override model")
    parser.add_argument("--workspace", default="", help="Workspace directory (all file ops scoped here)")
    parser.add_argument("--sandbox", default="", help="Bash mode: docker or direct (agent cannot change this)")
    args = parser.parse_args()

    if args.config:
        from ely.config import set_config_path
        set_config_path(args.config)

    query = " ".join(args.query)
    slot = "pro_provider" if args.pro else "provider"
    context = args.context

    if args.model:
        os.environ["ELY_PROVIDER_MODEL"] = args.model
    if args.sandbox:
        os.environ["ELY_BASH_SANDBOX"] = args.sandbox
    if args.workspace:
        os.environ["ELY_WORKSPACE"] = args.workspace

    # Sandbox cleanup on exit (atexit covers normal exit + unhandled SIGINT)
    from ely.tools import cleanup_sandbox
    atexit.register(cleanup_sandbox)

    if args.tui:
        from ely.tui import run_tui
        run_tui()
    elif query:
        single_shot(query, context, slot)
    else:
        repl(context, slot, classic_ui=args.classic, stream_ui=args.stream)


if __name__ == "__main__":
    main()
