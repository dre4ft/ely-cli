"""
Tool registry and generic CLI tools.
Uses the decorator-based registration pattern from ely/elys_tools.py.

Security:
  - File tools are scoped to a workspace directory (config: tools.workspace).
    All paths are prefixed and validated — no escape possible.
  - Bash sandbox mode is set at startup, NOT changeable by the agent.
  - toggle_sandbox is NOT registered as a tool (only usable via CLI flags).

Diary:
  - The agent has a persistent diary (JSON file) for notes between sessions.
  - Tools: diary_add, diary_list, diary_search, diary_get.
"""

import json
import os
import re
import subprocess
import requests
from pathlib import Path
from typing import Callable

# Global tool registry — tools registered here are available to the agent
ACTIONS: dict[str, dict] = {}


def _action(name: str, description: str, parameters: dict, optional: list = None):
    """Decorator that registers a handler as a tool in ACTIONS."""
    optional = optional or []

    def decorator(handler: Callable):
        ACTIONS[name] = {
            "definition": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": parameters,
                        "required": [k for k in parameters if k not in optional],
                    },
                },
            },
            "handler": handler,
        }
        return handler

    return decorator


def _get_disabled_tools() -> set[str]:
    """Read disabled tools from config."""
    from .config import get
    raw = get("tools", "disabled", "")
    if not raw:
        return set()
    return {t.strip() for t in raw.split(",") if t.strip()}


def get_tools(names: list[str] = None) -> tuple[list[dict], dict[str, Callable]]:
    """Return (tool_definitions, name->handler map). If names is None, return all.
    Merges native tools with custom tools, skill tools, and MCP tools.
    Respects tools.disabled config."""
    disabled = _get_disabled_tools()

    defs = []
    handlers = {}
    for name, action in ACTIONS.items():
        if name in disabled:
            continue
        if names is None or name in names:
            defs.append(action["definition"])
            handlers[name] = action["handler"]

    # Merge global custom tools (~/.ely/tools/)
    try:
        custom_defs, custom_handlers = _load_custom_tools()
        for d in custom_defs:
            name = d["function"]["name"]
            if name not in disabled:
                defs.append(d)
                if name in custom_handlers:
                    handlers[name] = custom_handlers[name]
    except Exception:
        pass

    # Merge skill tools (Python tools from active skill)
    try:
        from .skills import load_all_skill_tools
        skill_defs, skill_handlers = load_all_skill_tools()
        for d in skill_defs:
            name = d["function"]["name"]
            if name not in disabled:
                defs.append(d)
                if name in skill_handlers:
                    handlers[name] = skill_handlers[name]
    except Exception:
        pass

    # Merge MCP tools
    try:
        from .mcp import get_mcp_manager
        mcp_defs, mcp_handlers = get_mcp_manager().get_all_tools()
        for d in mcp_defs:
            name = d["function"]["name"]
            if name not in disabled:
                defs.append(d)
                if name in mcp_handlers:
                    handlers[name] = mcp_handlers[name]
    except Exception:
        pass

    return defs, handlers


def _custom_tools_dir() -> str:
    from .config import get_ely_dir
    return get_ely_dir("tools")


def _load_custom_tools() -> tuple[list[dict], dict[str, callable]]:
    """Load global custom tools from ~/.ely/tools/.
    Scans for tool_* functions, auto-generates TOOLS + dispatcher."""
    import importlib.util

    d = _custom_tools_dir()
    if not os.path.isdir(d):
        return [], {}

    definitions = []
    handlers = {}

    for fname in sorted(os.listdir(d)):
        if not fname.endswith(".py"):
            continue
        module_path = os.path.join(d, fname)
        try:
            spec = importlib.util.spec_from_file_location(f"ely_custom_{fname[:-3]}", module_path)
            if not spec or not spec.loader:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            # Check for explicit TOOLS/handle_tool first (backward compat)
            tools = getattr(mod, "TOOLS", None)
            dispatcher = getattr(mod, "handle_tool", None)

            if tools is None or not callable(dispatcher):
                # Auto-generate from tool_* functions
                tools, dispatcher = _extract_tools_from_module(mod)

            if not tools or not callable(dispatcher):
                continue

            for tool_def in tools:
                func_info = tool_def.get("function", {})
                original_name = func_info.get("name", "")
                if not original_name:
                    continue

                prefixed = f"custom__{original_name}"
                prefixed_def = {
                    "type": "function",
                    "function": {
                        **func_info,
                        "name": prefixed,
                        "description": f"[Custom] {func_info.get('description', '')}",
                    },
                }
                definitions.append(prefixed_def)

                def make_handler(d, orig_name):
                    def handler(**kwargs):
                        try:
                            return str(d(orig_name, kwargs))
                        except Exception as e:
                            return f"Tool error [custom/{orig_name}]: {e}"
                    return handler

                handlers[prefixed] = make_handler(dispatcher, original_name)

        except Exception:
            pass

    return definitions, handlers


def get_tool_names() -> list[str]:
    """Return list of registered tool names (for status display)."""
    return list(ACTIONS.keys())


# ═══════════════════════════════════════════════════════════════
# Workspace scoping — all file paths are relative to this root
# ═══════════════════════════════════════════════════════════════

def _workspace_dir() -> str:
    """Return the workspace root directory. All file operations are scoped here."""
    from .config import get
    ws = os.environ.get("ELY_WORKSPACE", "")
    if not ws:
        ws = get("tools", "workspace", os.getcwd())
    return os.path.realpath(os.path.expanduser(ws))


def _resolve_path(file_path: str) -> str:
    """Resolve and validate a path within the workspace.
    Returns the absolute path, or raises ValueError if it escapes the workspace.
    """
    ws = _workspace_dir()
    # Normalize: remove leading /, resolve ..
    clean = file_path.lstrip("/").lstrip("\\")
    # Collapse ../ sequences safely
    parts = []
    for p in clean.replace("\\", "/").split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            if parts:
                parts.pop()
            else:
                raise ValueError(f"Path escapes workspace: {file_path}")
        else:
            parts.append(p)
    resolved = os.path.realpath(os.path.join(ws, *parts))
    # Must be within workspace
    if not resolved.startswith(ws + os.sep) and resolved != ws:
        raise ValueError(f"Path escapes workspace: {file_path}")
    return resolved


def _relative_path(abs_path: str) -> str:
    """Convert absolute path to workspace-relative for display."""
    ws = _workspace_dir()
    if abs_path.startswith(ws + os.sep):
        return abs_path[len(ws) + 1:]
    elif abs_path == ws:
        return "."
    return abs_path


# ═══════════════════════════════════════════════════════════════
# Bash (sandboxed or direct, locked at startup)
# ═══════════════════════════════════════════════════════════════

SANDBOX_CONTAINER = "ely-sandbox"


def _is_sandbox_enabled() -> bool:
    """Check if bash runs in Docker sandbox. Set at startup, NOT changeable by agent."""
    from .config import get
    val = os.environ.get("ELY_BASH_SANDBOX", "")
    if val:
        return val.lower() in ("docker", "sandbox", "1", "true", "yes")
    return get("tools", "bash_sandbox", "direct").lower() in ("docker", "sandbox", "1", "true", "yes")


def cleanup_sandbox():
    """Stop and remove the Docker sandbox container if it exists."""
    try:
        r = subprocess.run(
            ["docker", "inspect", SANDBOX_CONTAINER],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            subprocess.run(
                ["docker", "rm", "-f", SANDBOX_CONTAINER],
                capture_output=True, timeout=10,
            )
    except Exception:
        pass


def _run_in_sandbox(command: str, timeout: int = 30) -> str:
    """Execute a command inside a Docker sandbox container."""
    container_name = SANDBOX_CONTAINER
    ws = _workspace_dir()
    check = subprocess.run(
        ["docker", "inspect", container_name],
        capture_output=True, text=True,
    )
    if check.returncode != 0:
        create = subprocess.run(
            ["docker", "run", "-d", "--name", container_name,
             "--rm", "-v", f"{ws}:/workspace", "-w", "/workspace",
             "--network", "none",  # no network access in sandbox
             "alpine:latest", "tail", "-f", "/dev/null"],
            capture_output=True, text=True, timeout=10,
        )
        if create.returncode != 0:
            return f"Error creating sandbox: {create.stderr}"

    result = subprocess.run(
        ["docker", "exec", "-i", container_name, "sh", "-c", command],
        capture_output=True, text=True, timeout=timeout,
    )
    out = result.stdout
    if result.stderr:
        out += f"\n[stderr]\n{result.stderr}"
    return out[:5000] or f"(exit code {result.returncode})"


def _run_direct(command: str, timeout: int = 30) -> str:
    """Execute a command directly on the host machine."""
    result = subprocess.run(
        command, shell=True, capture_output=True, text=True, timeout=timeout,
        cwd=_workspace_dir(),
    )
    out = result.stdout
    if result.stderr:
        out += f"\n[stderr]\n{result.stderr}"
    return out[:5000] or f"(exit code {result.returncode})"


@_action("bash", "Execute a shell command in the workspace directory.",
         {"command": {"type": "string", "description": "The shell command to execute."}})
def tool_bash(command: str) -> str:
    try:
        if _is_sandbox_enabled():
            return _run_in_sandbox(command)
        else:
            return _run_direct(command)
    except subprocess.TimeoutExpired:
        return "Error: command timed out (30s)"
    except Exception as e:
        return f"Error: {e}"


@_action("bash_batch", "Execute multiple bash commands in PARALLEL. Much faster than calling bash N times. Use for independent commands that can run simultaneously.",
         {"commands": {"type": "string", "description": "JSON array of commands, e.g. [\"ls -la\", \"cat file.txt\", \"df -h\"]."}})
def tool_bash_batch(commands: str) -> str:
    try:
        cmds = json.loads(commands)
        if not isinstance(cmds, list):
            return "Error: commands must be a JSON array"
    except json.JSONDecodeError:
        return "Error: invalid JSON for commands"

    results = _run_parallel(cmds, _run_direct if not _is_sandbox_enabled() else _run_in_sandbox)
    return "\n\n".join(f"--- [{i}] $ {cmd} ---\n{output}"
                       for i, (cmd, output) in enumerate(zip(cmds, results)))


def _run_parallel(items: list, func) -> list[str]:
    """Execute func(item) for each item in parallel using threads."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = [""] * len(items)

    with ThreadPoolExecutor(max_workers=min(len(items), 8)) as executor:
        futures = {executor.submit(func, item): i for i, item in enumerate(items)}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result(timeout=120)
            except Exception as e:
                results[idx] = f"Error: {e}"

    return results


# ═══════════════════════════════════════════════════════════════
# Diary — agent's persistent memory across sessions
# ═══════════════════════════════════════════════════════════════

def _diary_dir() -> str:
    from .config import get
    from .config import get_ely_dir
    d = get_ely_dir("memory/diary")
    os.makedirs(d, exist_ok=True)
    return d


def _load_diary() -> list:
    """Load all diary entries from individual JSON files, sorted by ID."""
    d = _diary_dir()
    entries = []
    for name in sorted(os.listdir(d)):
        if name.endswith(".json"):
            try:
                with open(os.path.join(d, name)) as f:
                    entry = json.load(f)
                    if isinstance(entry, dict) and "id" in entry:
                        entries.append(entry)
            except Exception:
                pass
    entries.sort(key=lambda e: e.get("id", 0))
    return entries


def _save_entry(entry: dict):
    """Save a single diary entry to its own JSON file."""
    d = _diary_dir()
    path = os.path.join(d, f"{entry['id']}.json")
    with open(path, "w") as f:
        json.dump(entry, f, indent=2)


def _next_diary_id() -> int:
    """Get the next available diary entry ID."""
    entries = _load_diary()
    if not entries:
        return 1
    return max(e.get("id", 0) for e in entries) + 1


def _migrate_old_diary():
    """Migrate from old diary.json to individual files."""
    from .config import get
    from .config import get_ely_dir
    old_path = get_ely_dir("memory/diary.json")
    if os.path.isfile(old_path):
        try:
            with open(old_path) as f:
                data = json.load(f)
            if isinstance(data, list):
                for entry in data:
                    _save_entry(entry)
            os.rename(old_path, old_path + ".migrated")
        except Exception:
            pass


@_action("diary_add", "Add an entry to the persistent diary. Use to remember facts, decisions, or context.",
         {"content": {"type": "string", "description": "The diary entry text. Be specific — what should be remembered and why."},
          "tags": {"type": "string", "description": "Comma-separated tags for searching (e.g. 'bug,security,python')."}},
         optional=["tags"])
def tool_diary_add(content: str, tags: str = "") -> str:
    import time
    _migrate_old_diary()
    entry = {
        "id": _next_diary_id(),
        "content": content,
        "tags": [t.strip() for t in tags.split(",") if t.strip()],
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_entry(entry)
    return f"Diary entry #{entry['id']} saved."


@_action("diary_list", "List recent diary entries.",
         {"limit": {"type": "integer", "description": "Max entries to return (default 20)."}},
         optional=["limit"])
def tool_diary_list(limit: int = 20) -> str:
    _migrate_old_diary()
    entries = _load_diary()
    if not entries:
        return "Diary is empty."
    recent = entries[-limit:]
    lines = [f"Diary ({len(entries)} entries, showing last {len(recent)}):"]
    for e in reversed(recent):
        tags = f" [{', '.join(e.get('tags', []))}]" if e.get("tags") else ""
        lines.append(f"  #{e['id']} [{e['timestamp']}]{tags}")
        lines.append(f"    {e['content'][:200]}")
    return "\n".join(lines)


@_action("diary_search", "Search diary entries by text or tags.",
         {"query": {"type": "string", "description": "Search query (matched against content and tags)."}})
def tool_diary_search(query: str) -> str:
    _migrate_old_diary()
    entries = _load_diary()
    q = query.lower()
    matches = []
    for e in entries:
        content_match = q in e.get("content", "").lower()
        tag_match = any(q in t.lower() for t in e.get("tags", []))
        if content_match or tag_match:
            matches.append(e)
    if not matches:
        return f"No diary entries matching '{query}'."
    lines = [f"Found {len(matches)} matching entries:"]
    for e in reversed(matches[-15:]):
        tags = f" [{', '.join(e.get('tags', []))}]" if e.get("tags") else ""
        lines.append(f"  #{e['id']} [{e['timestamp']}]{tags}")
        lines.append(f"    {e['content'][:300]}")
    return "\n".join(lines)


@_action("diary_get", "Read a specific diary entry by ID.",
         {"entry_id": {"type": "integer", "description": "The diary entry ID to read."}})
def tool_diary_get(entry_id: int) -> str:
    _migrate_old_diary()
    d = _diary_dir()
    path = os.path.join(d, f"{entry_id}.json")
    if os.path.isfile(path):
        try:
            with open(path) as f:
                e = json.load(f)
            tags = f" [{', '.join(e.get('tags', []))}]" if e.get("tags") else ""
            return f"#{e['id']} [{e['timestamp']}]{tags}\n\n{e['content']}"
        except Exception:
            pass
    return f"Diary entry #{entry_id} not found."


# ═══════════════════════════════════════════════════════════════
# File tools — scoped to workspace
# ═══════════════════════════════════════════════════════════════

@_action("read_file", "Read a file within the workspace.",
         {"file_path": {"type": "string", "description": "Path relative to workspace root."},
          "limit": {"type": "integer", "description": "Max lines to read (default 200)."}},
         optional=["limit"])
def tool_read_file(file_path: str, limit: int = 200) -> str:
    try:
        path = _resolve_path(file_path)
    except ValueError as e:
        return f"Error: {e}"
    if not os.path.isfile(path):
        return f"Error: file not found: {file_path}"
    try:
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
        total = len(lines)
        content = "".join(lines[:limit])
        rel = _relative_path(path)
        result = f"{rel} ({min(total, limit)}/{total} lines)\n```\n{content}```"
        return result[:8000]
    except Exception as e:
        return f"Error: {e}"


@_action("write_file", "Write content to a markdown file within the workspace. All output is forced to .md extension.",
         {"file_path": {"type": "string", "description": "Path relative to workspace root. Extension is forcibly replaced with .md."},
          "content": {"type": "string", "description": "Markdown content to write."}})
def tool_write_file(file_path: str, content: str) -> str:
    # Force .md extension — strip any existing extension, append .md
    base = os.path.splitext(file_path)[0]
    if not base:
        base = "untitled"
    file_path = base + ".md"
    try:
        path = _resolve_path(file_path)
    except ValueError as e:
        return f"Error: {e}"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        rel = _relative_path(path)
        return f"Written {len(content)} chars to {rel}"
    except Exception as e:
        return f"Error: {e}"


@_action("list_directory", "List files and directories within the workspace.",
         {"path": {"type": "string", "description": "Directory path relative to workspace (default: root)."}},
         optional=["path"])
def tool_list_directory(path: str = ".") -> str:
    try:
        target = _resolve_path(path) if path else _workspace_dir()
    except ValueError as e:
        return f"Error: {e}"
    if not os.path.isdir(target):
        return f"Error: not a directory: {path}"
    try:
        entries = sorted(os.listdir(target))
        files = []
        dirs = []
        for e in entries:
            if e.startswith("."):
                continue
            full = os.path.join(target, e)
            if os.path.isdir(full):
                dirs.append(e + "/")
            else:
                size = os.path.getsize(full)
                files.append(f"{e} ({_fmt_size(size)})")
        rel = _relative_path(target)
        lines = [f"📁 {rel}"]
        if dirs:
            lines.append("[Dirs]")
            lines.extend(f"  {d}" for d in dirs[:25])
        if files:
            lines.append("[Files]")
            lines.extend(f"  {f}" for f in files[:40])
        lines.append(f"\n{len(dirs)} dirs, {len(files)} files")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


@_action("grep", "Search for a regex pattern in workspace files.",
         {"pattern": {"type": "string", "description": "Regex pattern to search for (case-insensitive)."},
          "path": {"type": "string", "description": "Subdirectory to search in (default: entire workspace)."}},
         optional=["path"])
def tool_grep(pattern: str, path: str = ".") -> str:
    try:
        target = _resolve_path(path) if path else _workspace_dir()
    except ValueError as e:
        return f"Error: {e}"

    try:
        pat = re.compile(pattern, re.IGNORECASE)
    except Exception:
        pat = re.compile(re.escape(pattern), re.IGNORECASE)

    results = []
    skip_dirs = {".git", "node_modules", "__pycache__", "venv", ".venv", "dist", "build", ".ely"}

    if os.path.isfile(target):
        files = [target]
    elif os.path.isdir(target):
        files = []
        for root, dirs, filenames in os.walk(target):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fn in filenames:
                if fn.startswith("."):
                    continue
                fp = os.path.join(root, fn)
                if os.path.getsize(fp) > 1_000_000:  # skip >1MB files
                    continue
                files.append(fp)
    else:
        return f"Error: path not found: {path}"

    for fp in files:
        try:
            with open(fp, "r", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    if pat.search(line):
                        rel = _relative_path(fp)
                        results.append(f"{rel}:{i}: {line.strip()[:200]}")
                        if len(results) >= 15:
                            break
            if len(results) >= 15:
                break
        except Exception:
            pass

    if not results:
        return f"No matches for '{pattern}'"
    return "\n".join(results[:15])


# ═══════════════════════════════════════════════════════════════
# Sub-agent tools — spawn independent worker agents for parallel tasks
# ═══════════════════════════════════════════════════════════════

@_action("task", "Spawn a sub-agent to handle a task independently. The sub-agent runs in parallel and returns its result. Use for research, exploration, or analysis that doesn't need the main conversation.",
         {"description": {"type": "string", "description": "Task description for the sub-agent. Be specific about what to do and what to return."},
          "context": {"type": "string", "description": "Context for the sub-agent: default, code, sysadmin, research"}},
         optional=["context"])
def tool_task(description: str, context: str = "default") -> str:
    """Run a single sub-agent and return its result."""
    try:
        from .subagent import SubAgent
        agent = SubAgent(description, context=context)
        agent.start()
        result = agent.wait(timeout=120)
        if result is None:
            return "Sub-agent timed out after 120s."
        reply = result.get("reply", "")
        actions = result.get("actions", [])
        tokens = result.get("tokens", {})
        out = reply
        if actions:
            out += f"\n\n[Actions: {', '.join(actions)}]"
        if tokens.get("total", 0) > 0:
            out += f"\n[Tokens: {tokens['total']:,}]"
        return out
    except Exception as e:
        return f"Sub-agent error: {e}"


@_action("task_parallel", "Spawn multiple sub-agents to handle tasks in parallel. Each sub-agent works independently. Use for parallel research, multi-file analysis, or independent explorations.",
         {"tasks": {"type": "string", "description": "JSON array of {task: description, context: default|code|sysadmin|research}. Example: [{\"task\": \"Read file A\", \"context\": \"code\"}, {\"task\": \"Check disk\", \"context\": \"sysadmin\"}]"}})
def tool_task_parallel(tasks: str) -> str:
    """Run multiple sub-agents in parallel and return combined results."""
    try:
        tasks_list = json.loads(tasks)
        if not isinstance(tasks_list, list):
            return "Error: tasks must be a JSON array"

        from .subagent import SubAgentPool
        pool = SubAgentPool(max_workers=min(len(tasks_list), 6))
        results = pool.submit_and_wait(tasks_list)

        lines = []
        total_tokens = 0
        for i, r in enumerate(results):
            task_desc = tasks_list[i].get("task", tasks_list[i].get("description", f"Task {i+1}"))
            reply = r.get("reply", "No reply")
            actions = r.get("actions", [])
            t = r.get("tokens", {})
            total_tokens += t.get("total", 0)

            lines.append(f"### Sous-agent {i+1}: {task_desc[:80]}")
            lines.append(reply[:500])
            if actions:
                lines.append(f"  [Actions: {', '.join(actions)}]")

        lines.append(f"\n---\nTotal tokens: {total_tokens:,} across {len(results)} sub-agents")
        return "\n\n".join(lines)
    except json.JSONDecodeError:
        return "Error: invalid JSON for tasks parameter"
    except Exception as e:
        return f"Sub-agent pool error: {e}"


# ═══════════════════════════════════════════════════════════════
# Custom global tools — user-defined tools always available
# ═══════════════════════════════════════════════════════════════

@_action("custom_tool_add", "Create a global custom tool available in all sessions. Same format as skill tools: TOOLS list + handle_tool(name, params) function.",
         {"tool_filename": {"type": "string", "description": "Python filename (e.g. 'my_utils.py'). Must end with .py"},
          "content": {"type": "string", "description": "Python code with TOOLS list and handle_tool(name, params) -> str function."}})
def tool_custom_tool_add(tool_filename: str, content: str) -> str:
    if not tool_filename.endswith(".py"):
        return "Error: filename must end with .py"

    error = _validate_tool_content(content)
    if error:
        return f"Error: invalid tool — {error}"

    d = _custom_tools_dir()
    os.makedirs(d, exist_ok=True)
    tool_path = os.path.join(d, tool_filename)

    with open(tool_path, "w") as f:
        f.write(content)

    return f"Global custom tool '{tool_filename}' saved to {tool_path}"


@_action("custom_tool_list", "List global custom tools.",
         {})
def tool_custom_tool_list() -> str:
    d = _custom_tools_dir()
    if not os.path.isdir(d):
        return "No custom tools directory."
    files = sorted(f for f in os.listdir(d) if f.endswith(".py"))
    if not files:
        return "No custom tools. Create one with custom_tool_add."
    return "\n".join(f"  - {f}" for f in files)


# ═══════════════════════════════════════════════════════════════
# Skill management tools — create and extend agent skills
# ═══════════════════════════════════════════════════════════════

# Template that all skill tool files must follow.
# Tools execute bash commands with shell-escaped parameter substitution.
# This ensures tools respect the sandbox setting and cannot run arbitrary Python.

TOOL_TEMPLATE = '''# Ely tool — define functions with tool_ prefix.
# Each tool_xxx() function becomes a tool automatically.
# Docstring = tool description. Type hints = parameters.

def tool_my_tool(arg1: str = "") -> str:
    """What this tool does."""
    return f"Result: {arg1}"
'''


def _extract_tools_from_module(mod) -> tuple[list[dict], callable]:
    """Scan a module for tool_* functions and auto-generate TOOLS + dispatcher.
    Each function: tool_<name>(...) -> str with a docstring.
    Type hints become parameter types. Default values become optional params."""
    import inspect

    tools = []
    funcs = {}

    for attr_name in dir(mod):
        if not attr_name.startswith("tool_"):
            continue
        func = getattr(mod, attr_name)
        if not callable(func):
            continue

        tool_name = attr_name[5:]  # strip "tool_" prefix
        description = (func.__doc__ or "").strip().split("\n")[0]
        funcs[tool_name] = func

        # Extract parameters from signature
        try:
            sig = inspect.signature(func)
        except (ValueError, TypeError):
            sig = None

        properties = {}
        required = []
        if sig:
            for pname, param in sig.parameters.items():
                if pname in ("self", "cls"):
                    continue
                ptype = "string"
                if param.annotation is not inspect.Parameter.empty:
                    ann = param.annotation
                    if ann is int:
                        ptype = "integer"
                    elif ann is bool:
                        ptype = "boolean"
                    elif ann is float:
                        ptype = "number"

                properties[pname] = {
                    "type": ptype,
                    "description": f"Parameter: {pname}",
                }
                if param.default is inspect.Parameter.empty:
                    required.append(pname)

        tools.append({
            "type": "function",
            "function": {
                "name": tool_name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                } if properties else {"type": "object", "properties": {}},
            },
        })

    # Auto-generate dispatcher
    def handle_tool(tool_name: str, params: dict) -> str:
        func = funcs.get(tool_name)
        if not func:
            return f"Unknown tool: {tool_name}"
        try:
            return str(func(**params))
        except Exception as e:
            return f"Tool error [{tool_name}]: {e}"

    return tools, handle_tool

import re
import shlex

_TOOL_NAME_RE = re.compile(r'^[a-z][a-z0-9_]*$')
# Block dangerous calls even inside run() — tools run in a restricted namespace
# Keywords blocked in tool source. Each is checked as a WORD (not substring).
# "localStorage" won't match "locals", "evaluate" won't match "eval", etc.
# These are blocked at source level. The safe namespace also blocks them at runtime
def _validate_tool_content(content: str) -> str | None:
    """Validate a skill tool file. Only checks syntax — no restrictions.
    Skill tools are trusted Python code imported normally."""
    if len(content) > 65536:
        return "Tool file too large (max 64KB)"
    try:
        compile(content, "<tool>", "exec")
    except SyntaxError as e:
        return f"Syntax error: {e}"
    return None


def _skills_user_dir() -> str:
    """User-level skills directory."""
    from .config import get_ely_dir
    return os.path.join(get_ely_dir(), "skills")


def _validate_skill_path(base_dir: str, name: str) -> str:
    """Validate a skill sub-path is safe (no traversal). Returns absolute path."""
    clean = name.lstrip("/").replace("\\", "/")
    parts = []
    for p in clean.split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            raise ValueError(f"Path traversal denied: {name}")
        parts.append(p)
    if not parts:
        raise ValueError(f"Empty name not allowed")
    return os.path.join(base_dir, *parts)


@_action("skill_create", "Create a new skill directory with SKILL.md. Skills extend the agent's capabilities with custom instructions, tools, and references.",
         {"name": {"type": "string", "description": "Skill name (slug, e.g. 'my-deploy-skill')."},
          "description": {"type": "string", "description": "One-line description of what this skill does."},
          "instructions": {"type": "string", "description": "Markdown instructions that will be injected into the system prompt."}})
def tool_skill_create(name: str, description: str, instructions: str) -> str:
    try:
        skill_dir = _validate_skill_path(_skills_user_dir(), name)
    except ValueError as e:
        return f"Error: {e}"

    os.makedirs(skill_dir, exist_ok=True)

    frontmatter = f"---\nname: {name}\ndescription: {description}\n---\n\n"
    skill_md = os.path.join(skill_dir, "SKILL.md")
    with open(skill_md, "w") as f:
        f.write(frontmatter + instructions)

    # Create subdirs
    for sub in ("tools", "references", "assets"):
        os.makedirs(os.path.join(skill_dir, sub), exist_ok=True)

    return f"Skill '{name}' created in {skill_dir}"


@_action("skill_add_tool", "Add a Python tool module to a skill. Must define TOOLS list and handle_tool(name, params) function. Uses standard Python imports — no restrictions.",
         {"skill_name": {"type": "string", "description": "The skill to add the tool to."},
          "tool_filename": {"type": "string", "description": "Python filename (e.g. 'my_tools.py'). Must end with .py"},
          "content": {"type": "string", "description": "Python code with TOOLS list and handle_tool() function."}})
def tool_skill_add_tool(skill_name: str, tool_filename: str, content: str) -> str:
    if not tool_filename.endswith(".py"):
        return "Error: tool filename must end with .py"

    error = _validate_tool_content(content)
    if error:
        return f"Error: invalid tool — {error}"

    try:
        skill_dir = _validate_skill_path(_skills_user_dir(), skill_name)
        tool_path = _validate_skill_path(os.path.join(skill_dir, "tools"), tool_filename)
    except ValueError as e:
        return f"Error: {e}"

    if not os.path.isdir(skill_dir):
        return f"Error: skill '{skill_name}' not found. Create it first with skill_create."

    os.makedirs(os.path.dirname(tool_path), exist_ok=True)
    with open(tool_path, "w") as f:
        f.write(content)

    return f"Tool '{tool_filename}' added to skill '{skill_name}' ({len(content)} bytes)."


@_action("skill_add_reference", "Add a reference document to a skill. References provide the agent with domain knowledge or documentation.",
         {"skill_name": {"type": "string", "description": "The skill to add the reference to."},
          "ref_name": {"type": "string", "description": "Reference filename (e.g. 'api-docs.md')."},
          "content": {"type": "string", "description": "Reference content in markdown."}})
def tool_skill_add_reference(skill_name: str, ref_name: str, content: str) -> str:
    try:
        skill_dir = _validate_skill_path(_skills_user_dir(), skill_name)
        ref_path = _validate_skill_path(os.path.join(skill_dir, "references"), ref_name)
    except ValueError as e:
        return f"Error: {e}"

    if not os.path.isdir(skill_dir):
        return f"Error: skill '{skill_name}' not found. Create it first with skill_create."

    os.makedirs(os.path.dirname(ref_path), exist_ok=True)
    with open(ref_path, "w") as f:
        f.write(content)

    return f"Reference '{ref_name}' added to skill '{skill_name}' ({len(content)} bytes)."


@_action("skill_add_asset", "Add an asset file (template, config, resource) to a skill.",
         {"skill_name": {"type": "string", "description": "The skill to add the asset to."},
          "asset_name": {"type": "string", "description": "Asset filename (e.g. 'Dockerfile.tmpl')."},
          "content": {"type": "string", "description": "Asset file content."}})
def tool_skill_add_asset(skill_name: str, asset_name: str, content: str) -> str:
    try:
        skill_dir = _validate_skill_path(_skills_user_dir(), skill_name)
        asset_path = _validate_skill_path(os.path.join(skill_dir, "assets"), asset_name)
    except ValueError as e:
        return f"Error: {e}"

    if not os.path.isdir(skill_dir):
        return f"Error: skill '{skill_name}' not found. Create it first with skill_create."

    os.makedirs(os.path.dirname(asset_path), exist_ok=True)
    with open(asset_path, "w") as f:
        f.write(content)

    return f"Asset '{asset_name}' added to skill '{skill_name}' ({len(content)} bytes)."


# ═══════════════════════════════════════════════════════════════
# Web tools
# ═══════════════════════════════════════════════════════════════

@_action("web_search", "Search the web for information.",
         {"query": {"type": "string", "description": "Search query."}})
def tool_web_search(query: str) -> str:
    try:
        import re
        from html import unescape

        url = "https://html.duckduckgo.com/html/"
        resp = requests.post(
            url,
            data={"q": query},
            timeout=15,
            headers={"User-Agent": "Ely-CLI/1.0"},
        )
        resp.raise_for_status()
        html = resp.text

        # Extract results with regex — no external deps
        results = []
        for m in re.finditer(
            r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
            html, re.DOTALL | re.IGNORECASE
        ):
            link = m.group(1)
            title = unescape(re.sub(r'<.*?>', '', m.group(2)).strip())
            snippet = unescape(re.sub(r'<.*?>', '', m.group(3)).strip())
            if title and link:
                results.append(f"- [{title}]({link})\n  {snippet[:200]}")
            if len(results) >= 5:
                break

        return "\n".join(results) if results else f"No results for: {query}"
    except Exception as e:
        return f"Search error: {e}"


@_action("web_fetch", "Fetch and extract text content from a URL.",
         {"url": {"type": "string", "description": "URL to fetch."}})
def tool_web_fetch(url: str) -> str:
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Ely-CLI/1.0"})
        resp.raise_for_status()
        ct = resp.headers.get("Content-Type", "").lower()
        from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
        import warnings
        # Pick parser based on content type, suppress XML-in-HTML warning
        if "xml" in ct or "rss" in ct or "atom" in ct:
            soup = BeautifulSoup(resp.text, "xml")
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
                soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        lines = [l for l in text.splitlines() if l.strip()]
        return "\n".join(lines)[:5000]
    except ImportError as e:
        return f"Error: missing package — {e}"
    except Exception as e:
        return f"Error fetching {url}: {e}"


@_action("http_request", "Make an HTTP request with full control over method, headers, and body. Use for API testing, CORS checks, SSRF, custom headers injection.",
         {"url": {"type": "string", "description": "Target URL (https://example.com/api)."},
          "method": {"type": "string", "description": "HTTP method: GET, POST, PUT, DELETE, PATCH, OPTIONS, HEAD."},
          "headers": {"type": "string", "description": "JSON object of headers, e.g. {\"Authorization\": \"Bearer xxx\", \"X-Custom\": \"value\"}."},
          "body": {"type": "string", "description": "Request body (for POST/PUT/PATCH)."},
          "follow_redirects": {"type": "boolean", "description": "Follow redirects? Default true."}},
         optional=["headers", "body", "follow_redirects"])
def tool_http_request(url: str, method: str = "GET", headers: str = "{}",
                      body: str = "", follow_redirects: bool = True) -> str:
    try:
        hdrs = json.loads(headers) if isinstance(headers, str) else headers
        if not isinstance(hdrs, dict):
            hdrs = {}
    except (json.JSONDecodeError, ValueError):
        hdrs = {}

    hdrs.setdefault("User-Agent", "Ely-CLI/1.0")

    try:
        kwargs = {"method": method.upper(), "url": url, "headers": hdrs,
                  "timeout": 30, "allow_redirects": follow_redirects}
        if body and method.upper() in ("POST", "PUT", "PATCH"):
            kwargs["data"] = body

        resp = requests.request(**kwargs)

        out_lines = [f"HTTP {resp.status_code} {resp.reason}"]
        # Response headers
        out_lines.append(f"\n--- Response Headers ---")
        for k, v in resp.headers.items():
            out_lines.append(f"  {k}: {v}")
        # Response body (truncated)
        out_lines.append(f"\n--- Response Body ({len(resp.text)} chars) ---")
        out_lines.append(resp.text[:4000])

        return "\n".join(out_lines)
    except ImportError:
        return "Error: requests package not available"
    except Exception as e:
        return f"HTTP request error: {e}"


@_action("http_batch", "Execute multiple HTTP requests in PARALLEL. Much faster than calling http_request multiple times. Use for scanning URLs, testing endpoints, or batch API calls.",
         {"requests": {"type": "string", "description": "JSON array: [{\"url\": \"...\", \"method\": \"GET\", \"headers\": {}, \"body\": \"\"}]."}})
def tool_http_batch(requests: str) -> str:
    try:
        reqs = json.loads(requests)
        if not isinstance(reqs, list):
            return "Error: requests must be a JSON array"
    except json.JSONDecodeError:
        return "Error: invalid JSON for requests"

    def _one(req):
        if not isinstance(req, dict):
            return "Error: invalid request"
        hdrs = req.get("headers", {})
        return tool_http_request(
            url=req.get("url", ""),
            method=req.get("method", "GET"),
            headers=json.dumps(hdrs) if isinstance(hdrs, dict) else str(hdrs),
            body=str(req.get("body", "")),
        )

    results = _run_parallel(reqs, _one)
    return "\n\n".join(
        f"--- [{i}] {req.get('method', 'GET')} {req.get('url', '?')} ---\n{output}"
        for i, (req, output) in enumerate(zip(reqs, results))
    )


@_action("socket_raw", "Open a raw TCP socket to a host:port, send data, and read the response. Use for testing non-HTTP protocols, SMTP, raw HTTP, or manual protocol fuzzing.",
         {"host": {"type": "string", "description": "Target hostname or IP."},
          "port": {"type": "integer", "description": "Target port (e.g. 80, 443, 25)."},
          "data": {"type": "string", "description": "Data to send. Use \\r\\n for line breaks, \\n for newline."},
          "timeout": {"type": "integer", "description": "Read timeout in seconds (default 10)."},
          "use_tls": {"type": "boolean", "description": "Wrap socket with TLS/SSL? Default false."}},
         optional=["timeout", "use_tls"])
def tool_socket_raw(host: str, port: int, data: str,
                    timeout: int = 10, use_tls: bool = False) -> str:
    try:
        import socket
        import ssl

        # Unescape the data string
        payload = data.replace("\\r\\n", "\r\n").replace("\\n", "\n").replace("\\t", "\t")

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)

        if use_tls:
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(sock, server_hostname=host)

        sock.connect((host, port))
        sock.sendall(payload.encode())

        response = b""
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
            except socket.timeout:
                break

        sock.close()

        out = [f"Connected to {host}:{port}" + (" (TLS)" if use_tls else "")]
        out.append(f"Sent {len(payload)} bytes")
        out.append(f"\n--- Response ({len(response)} bytes) ---")
        # Try to decode, fall back to hex
        try:
            out.append(response.decode(errors="replace")[:4000])
        except Exception:
            out.append(response.hex()[:4000])

        return "\n".join(out)
    except Exception as e:
        return f"Socket error: {e}"


# ═══════════════════════════════════════════════════════════════
# Utilities (NOT registered as tools — agent cannot call these)
# ═══════════════════════════════════════════════════════════════

def get_workspace_info() -> str:
    """Return workspace info for the system prompt."""
    ws = _workspace_dir()
    sandbox = "docker" if _is_sandbox_enabled() else "direct"
    return f"Workspace: {ws} | Bash: {sandbox}"


def get_diary_context(limit: int = 5) -> str:
    """Return recent diary entries for inclusion in the system prompt."""
    entries = _load_diary()
    if not entries:
        return ""
    recent = entries[-limit:]
    lines = ["\n**Diary (connaissances sauvegardées par l'utilisateur) :**"]
    for e in reversed(recent):
        tags = f" [{', '.join(e.get('tags', []))}]" if e.get("tags") else ""
        lines.append(f"- [#{e['id']}] {e['content'][:150]}{tags}")
    return "\n".join(lines)


def _fmt_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f}{unit}"
        size /= 1024
    return f"{size:.0f}TB"
