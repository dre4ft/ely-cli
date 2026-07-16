"""
Tool registry — decorator-based registration, dynamic selection, workspace scoping.
Individual tool implementations are in submodules (bash.py, files.py, web.py, etc.).
"""

import os
import json
import subprocess
from typing import Callable

# ── Registry ──

ACTIONS: dict[str, dict] = {}


def _action(name: str, description: str, parameters: dict, optional: list[str] = None):
    """Decorator: register a tool function."""
    optional = optional or []
    def decorator(func):
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
            "handler": func,
        }
        return func
    return decorator


# ── Tool categories for dynamic selection ──

TOOL_CATEGORIES = {
    "files": {"read_file", "write_file", "edit_file", "list_directory", "grep"},
    "bash": {"bash", "bash_batch"},
    "web": {"web_search", "web_fetch", "http_request", "http_batch", "socket_raw"},
    "diary": {"diary_add", "diary_list", "diary_search", "diary_get"},
    "skills": {"skill_create", "skill_add_tool", "skill_add_reference", "skill_add_asset", "skill_reference_list", "skill_reference_get", "custom_tool_add", "custom_tool_list"},
    "tasks": {"task", "task_poll", "task_list", "task_parallel", "plan"},
    "contexts": {"context_list", "context_create", "context_get"},
}

CATEGORY_KEYWORDS = {
    "web": ["http", "api", "url", "rest", "curl", "web", "site", "page", "recherche", "search", "fetch", "internet", "en ligne", "online", "cve", "vuln", "ssti", "xss", "csrf", "cors", "jwt", "injection", "flag", "ctf", "root-me", "challenge", "endpoint", "request", "response", "header", "cookie"],
    "diary": ["souvenir", "retenir", "mémoire", "memoire", "diary", "note", "sauvegarde", "rappelle", "oublie", "flag", "résultat", "important"],
    "skills": ["skill", "compétence", "competence", "créer un outil", "nouvel outil", "nouveau skill", "créer un skill"],
    "tasks": ["parallèle", "parallele", "plusieurs fichiers", "sous-agent", "subagent", "background", "en même temps", "simultané", "chaque fichier", "tous les"],
    "contexts": ["context", "contexte", "mode", "profil"],
}


def _get_dynamic_tool_names(message: str, history: list = None) -> set[str]:
    msg_lower = message.lower() if message else ""
    active = set(TOOL_CATEGORIES["files"]) | set(TOOL_CATEGORIES["bash"])
    for category, keywords in CATEGORY_KEYWORDS.items():
        if category in ("files", "bash"): continue
        if any(kw in msg_lower for kw in keywords):
            active |= set(TOOL_CATEGORIES.get(category, set()))
    if history and len(history) > 2:
        return set()
    all_categorized = set().union(*TOOL_CATEGORIES.values())
    active |= (set(ACTIONS.keys()) - all_categorized)
    return active


def _get_disabled_tools() -> set[str]:
    from ..config import get
    raw = get("tools", "disabled", "")
    if not raw: return set()
    return {t.strip() for t in raw.split(",") if t.strip()}


def get_tools(names: set[str] = None) -> tuple[list[dict], dict[str, Callable]]:
    disabled = _get_disabled_tools()
    defs, handlers = [], {}
    for name, action in ACTIONS.items():
        if name in disabled: continue
        if names is None or name in names:
            defs.append(action["definition"])
            handlers[name] = action["handler"]

    # Merge skill tools
    try:
        from ..skills import load_all_skill_tools
        sd, sh = load_all_skill_tools()
        for d in sd:
            n = d["function"]["name"]
            if n not in disabled:
                defs.append(d)
                if n in sh: handlers[n] = sh[n]
    except Exception: pass

    # Merge custom tools
    try: _merge_custom_tools(defs, handlers, disabled)
    except Exception: pass

    # Merge MCP tools
    try:
        from ..mcp import get_mcp_manager
        md, mh = get_mcp_manager().get_all_tools()
        for d in md:
            n = d["function"]["name"]
            if n not in disabled:
                defs.append(d)
                if n in mh: handlers[n] = mh[n]
    except Exception: pass

    return defs, handlers


def get_tool_names() -> list[str]:
    return list(ACTIONS.keys())


# ── Workspace scoping ──

def _workspace_dir() -> str:
    from ..config import get
    ws = os.environ.get("ELY_WORKSPACE", "")
    if not ws: ws = get("tools", "workspace", os.getcwd())
    return os.path.realpath(os.path.expanduser(ws))


def _resolve_path(file_path: str) -> str:
    ws = _workspace_dir()
    clean = file_path.lstrip("/").lstrip("\\")
    parts = []
    for p in clean.replace("\\", "/").split("/"):
        if p in ("", "."): continue
        if p == "..":
            if parts: parts.pop()
            else: raise ValueError(f"Path escapes workspace: {file_path}")
        else: parts.append(p)
    resolved = os.path.realpath(os.path.join(ws, *parts))
    if not resolved.startswith(ws + os.sep) and resolved != ws:
        raise ValueError(f"Path escapes workspace: {file_path}")
    return resolved


def _relative_path(abs_path: str) -> str:
    ws = _workspace_dir()
    if abs_path.startswith(ws + os.sep): return abs_path[len(ws) + 1:]
    elif abs_path == ws: return "."
    return abs_path


def _validate_skill_path(base_dir: str, name: str) -> str:
    clean = name.lstrip("/").replace("\\", "/")
    parts = []
    for p in clean.split("/"):
        if p in ("", "."): continue
        if p == "..": raise ValueError(f"Path traversal denied: {name}")
        parts.append(p)
    if not parts: raise ValueError("Empty name")
    return os.path.join(base_dir, *parts)


def _skills_user_dir() -> str:
    from ..config import get_ely_dir
    return os.path.join(get_ely_dir(), "skills")


# ── Bash infrastructure ──

def _is_sandbox_enabled() -> bool:
    from ..config import get
    val = os.environ.get("ELY_BASH_SANDBOX", "")
    if val: return val.lower() in ("docker", "sandbox", "1", "true", "yes")
    return get("tools", "bash_sandbox", "direct").lower() in ("docker", "sandbox", "1", "true", "yes")


SANDBOX_CONTAINER = "ely-sandbox"


def cleanup_sandbox():
    try:
        r = subprocess.run(["docker", "inspect", SANDBOX_CONTAINER], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            subprocess.run(["docker", "rm", "-f", SANDBOX_CONTAINER], capture_output=True, timeout=10)
    except Exception: pass


def _sanitize_command(command: str, sandbox: bool = False, block_dotdot: bool = False) -> str:
    ws = _workspace_dir()
    replacement = "/workspace" if sandbox else "."
    cmd = command.replace(ws, replacement)
    cmd = cmd.replace(ws.rstrip("/") + "/", replacement.rstrip("/") + "/")
    if block_dotdot and ".." in cmd:
        import shlex as _shlex
        import re as _re
        try: parts = _shlex.split(cmd)
        except ValueError: parts = cmd.split()
        resolved = []
        for p in parts:
            if '..' in p:
                if p == '..': resolved.append('.')
                else:
                    norm = os.path.normpath(p)
                    norm = _re.sub(r'^(\.\./)+', '', norm)
                    norm = _re.sub(r'/\.\.$', '', norm)
                    norm = _re.sub(r'^\.\.$', '.', norm)
                    if not norm: norm = '.'
                    if norm.startswith('/'): norm = '.' + norm
                    resolved.append(norm)
            else: resolved.append(p)
        cmd = ' '.join(resolved)
    return cmd


def _extract_paths(command: str) -> list[str]:
    import shlex as _shlex
    try: parts = _shlex.split(command)
    except ValueError: parts = command.split()
    paths = []
    for p in parts:
        if p.startswith('-') or p in ('|', ';', '&&', '||', '>', '>>', '<', '2>', '1>', '&>'): continue
        if '=' in p and '/' not in p: continue
        if '/' in p or p.startswith('.') or p.endswith(('.txt', '.py', '.js', '.json', '.yaml', '.yml', '.md', '.log', '.csv', '.xml', '.html', '.css', '.sh', '.conf', '.cfg', '.ini', '.env')):
            paths.append(p)
    return paths


def _validate_paths(command: str, workspace: str) -> str | None:
    paths = _extract_paths(command)
    for p in paths:
        try:
            resolved = os.path.realpath(p) if os.path.isabs(p) else os.path.realpath(os.path.join(workspace, p))
        except Exception: continue
        if not resolved.startswith(workspace.rstrip('/') + '/') and resolved != workspace.rstrip('/'):
            return f"Error: path '{p}' resolves outside workspace ({resolved}). All file operations must stay within the workspace. Use relative paths."
    return None


def _run_direct(command: str, timeout: int = 30, sanitize: bool = True) -> str:
    from .sandbox import _fs_sandbox_enabled, _run_fs_sandboxed
    cmd = _sanitize_command(command, block_dotdot=sanitize) if sanitize else command
    ws = _workspace_dir()
    if sanitize:
        err = _validate_paths(cmd, ws)
        if err: return err
    if sanitize and _fs_sandbox_enabled():
        return _run_fs_sandboxed(cmd, ws, timeout)
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=ws)
    out = result.stdout
    if result.stderr: out += f"\n[stderr]\n{result.stderr}"
    return out[:3000] or f"(exit code {result.returncode})"


def _run_in_sandbox(command: str, timeout: int = 30) -> str:
    cmd = _sanitize_command(command, sandbox=True)
    ws = _workspace_dir()
    check = subprocess.run(["docker", "inspect", SANDBOX_CONTAINER], capture_output=True, text=True)
    if check.returncode != 0:
        create = subprocess.run(["docker", "run", "-d", "--name", SANDBOX_CONTAINER, "--rm", "-v", f"{ws}:/workspace", "-w", "/workspace", "--network", "none", "alpine:latest", "tail", "-f", "/dev/null"], capture_output=True, text=True, timeout=10)
        if create.returncode != 0: return f"Error creating sandbox: {create.stderr}"
    result = subprocess.run(["docker", "exec", "-i", SANDBOX_CONTAINER, "sh", "-c", cmd], capture_output=True, text=True, timeout=timeout)
    out = result.stdout
    if result.stderr: out += f"\n[stderr]\n{result.stderr}"
    return out[:3000] or f"(exit code {result.returncode})"


# ── Parallel execution ──

def _run_parallel(items: list, func) -> list[str]:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = [""] * len(items)
    with ThreadPoolExecutor(max_workers=min(len(items), 8)) as executor:
        futures = {executor.submit(func, item): i for i, item in enumerate(items)}
        for future in as_completed(futures):
            idx = futures[future]
            try: results[idx] = future.result(timeout=120)
            except Exception as e: results[idx] = f"Error: {e}"
    return results


# ── Diary infra ──

def _diary_dir() -> str:
    from ..config import get_ely_dir
    d = get_ely_dir("memory/diary")
    os.makedirs(d, exist_ok=True)
    return d


def _load_diary() -> list:
    d = _diary_dir()
    entries = []
    for name in sorted(os.listdir(d)):
        if name.endswith(".json"):
            try:
                with open(os.path.join(d, name)) as f:
                    e = json.load(f)
                    if isinstance(e, dict) and "id" in e: entries.append(e)
            except Exception: pass
    entries.sort(key=lambda e: e.get("id", 0))
    return entries


def _save_entry(entry: dict):
    path = os.path.join(_diary_dir(), f"{entry['id']}.json")
    with open(path, "w") as f: json.dump(entry, f, indent=2)


def _next_diary_id() -> int:
    entries = _load_diary()
    return max((e.get("id", 0) for e in entries), default=0) + 1


def _migrate_old_diary():
    from ..config import get_ely_dir
    old = get_ely_dir("memory/diary.json")
    if os.path.isfile(old):
        try:
            with open(old) as f:
                data = json.load(f)
            if isinstance(data, list):
                for e in data: _save_entry(e)
            os.rename(old, old + ".migrated")
        except Exception: pass


# ── Custom tools infra ──

def _custom_tools_dir() -> str:
    from ..config import get_ely_dir
    return get_ely_dir("tools")

TOOL_TEMPLATE = '''# Ely tool — define functions with tool_ prefix.
def tool_my_tool(arg1: str = "") -> str:
    """What this tool does."""
    return f"Result: {arg1}"
'''

def _validate_tool_content(content: str) -> str | None:
    if len(content) > 65536: return "Tool file too large (max 64KB)"
    try: compile(content, "<tool>", "exec")
    except SyntaxError as e: return f"Syntax error: {e}"
    return None

def _extract_tools_from_module(mod) -> tuple[list[dict], Callable]:
    import inspect
    tools, funcs = [], {}
    for attr in dir(mod):
        if not attr.startswith("tool_"): continue
        func = getattr(mod, attr)
        if not callable(func): continue
        name = attr[5:]
        desc = (func.__doc__ or "").strip().split("\n")[0]
        funcs[name] = func
        try: sig = inspect.signature(func)
        except (ValueError, TypeError): sig = None
        props, required = {}, []
        if sig:
            for pn, p in sig.parameters.items():
                if pn in ("self", "cls"): continue
                pt = "string"
                if p.annotation is not inspect.Parameter.empty:
                    a = p.annotation
                    if a is int: pt = "integer"
                    elif a is bool: pt = "boolean"
                    elif a is float: pt = "number"
                props[pn] = {"type": pt, "description": f"Parameter: {pn}"}
                if p.default is inspect.Parameter.empty: required.append(pn)
        tools.append({"type": "function", "function": {"name": name, "description": desc, "parameters": {"type": "object", "properties": props, "required": required} if props else {"type": "object", "properties": {}}}})
    def handle_tool(tool_name: str, params: dict) -> str:
        f = funcs.get(tool_name)
        if not f: return f"Unknown tool: {tool_name}"
        try: return str(f(**params))
        except Exception as e: return f"Tool error [{tool_name}]: {e}"
    return tools, handle_tool

def _parse_params_from_description(description: str) -> dict | None:
    import re as _re
    m = _re.search(r'Params?:\s*(.+)', description, re.IGNORECASE)
    if not m: return None
    props, required = {}, []
    for part in _re.split(r',\s*(?![^(]*\))', m.group(1)):
        part = part.strip()
        if not part: continue
        pm = _re.match(r'(\w+)\s*(?:\((.+)\))?', part)
        if not pm: continue
        pname, detail = pm.group(1), (pm.group(2) or "").lower()
        is_req = "required" in detail
        default = None
        if not is_req:
            dm = _re.search(r'default:\s*["\']?([^"\')\]]+)', detail)
            if dm: default = dm.group(1).strip().strip('"').strip("'")
        props[pname] = {"type": "string", "description": f"Parameter: {pname}" + (f" (default: {default})" if default else "")}
        if is_req: required.append(pname)
    if not props: return None
    return {"type": "object", "properties": props, "required": required} if required else {"type": "object", "properties": props}

def _normalize_tool_def(tool: dict) -> dict | None:
    if "type" in tool and "function" in tool: return tool
    name = tool.get("name", "")
    if not name: return None
    desc = tool.get("description", "")
    params = tool.get("parameters")
    if not params:
        params = _parse_params_from_description(desc)
        if params and "Params:" in desc: desc = desc.split("Params:")[0].strip().rstrip(".")
    if not params: params = {"type": "object", "properties": {}}
    if "type" not in params:
        props = params
        req = [k for k, v in props.items() if isinstance(v, dict) and v.get("required")]
        params = {"type": "object", "properties": props}
        if req: params["required"] = req
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": params}}

def _merge_custom_tools(defs: list, handlers: dict, disabled: set):
    import importlib.util as _iu
    d = _custom_tools_dir()
    if not os.path.isdir(d): return
    for fname in sorted(os.listdir(d)):
        if not fname.endswith(".py"): continue
        mp = os.path.join(d, fname)
        try:
            spec = _iu.spec_from_file_location(f"ely_custom_{fname[:-3]}", mp)
            if not spec or not spec.loader: continue
            mod = _iu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            tools = getattr(mod, "TOOLS", None)
            dispatcher = getattr(mod, "handle_tool", None)
            if tools is None or not callable(dispatcher):
                tools, dispatcher = _extract_tools_from_module(mod)
            if not tools or not callable(dispatcher): continue
            for td in tools:
                normalized = _normalize_tool_def(td)
                if not normalized: continue
                fi = normalized["function"]
                on = fi.get("name", "")
                prefixed = f"custom__{on}"
                pdef = {"type": "function", "function": {**fi, "name": prefixed, "description": f"[Custom] {fi.get('description', '')}"}}
                defs.append(pdef)
                handlers[prefixed] = (lambda d, o: lambda **kw: str(d(o, kw)))(dispatcher, on)
        except Exception: pass


# ── Import all tool modules to trigger @_action registration ──

from . import bash, files, web, diary, skills, contexts, subagents, custom, sandbox

# ── Utilities imported by agent.py ──

def get_workspace_info() -> str:
    ws = _workspace_dir()
    sandbox = "docker" if _is_sandbox_enabled() else "direct"
    return f"Workspace: {ws} | Bash: {sandbox}"


def get_diary_context(limit: int = 5) -> str:
    entries = _load_diary()
    if not entries: return ""
    recent = entries[-limit:]
    lines = ["\n**Diary (connaissances sauvegardées par l'utilisateur) :**"]
    for e in reversed(recent):
        tags = f" [{', '.join(e.get('tags', []))}]" if e.get("tags") else ""
        lines.append(f"- [#{e['id']}] {e['content'][:150]}{tags}")
    return "\n".join(lines)


# Re-export all public symbols for backward compatibility
from .bash import tool_bash, tool_bash_batch
from .files import tool_read_file, tool_write_file, tool_edit_file, tool_list_directory, tool_grep
from .web import tool_web_search, tool_web_fetch, tool_http_request, tool_http_batch, tool_socket_raw
from .diary import tool_diary_add, tool_diary_list, tool_diary_search, tool_diary_get
from .skills import tool_skill_create, tool_skill_add_tool, tool_skill_add_reference, tool_skill_add_asset, tool_skill_reference_list, tool_skill_reference_get
from .contexts import tool_context_list, tool_context_create, tool_context_get
from .subagents import tool_task, tool_task_poll, tool_task_list, tool_task_parallel, tool_plan, _background_tasks, _task_lock, _task_id_counter, _get_background_results
from .custom import tool_custom_tool_add, tool_custom_tool_list
from .sandbox import _fs_sandbox_enabled
