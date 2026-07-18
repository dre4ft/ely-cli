"""Tool registry — all tools registered via @action decorator."""
from ._core import ACTIONS, action, workspace_dir, resolve_path, relative_path, is_sandbox, run_direct, run_sandboxed, run_parallel
from ._core import _sanitize, _validate_paths
from typing import Callable

# ── Dynamic tool selection ──

CATEGORIES = {
    "files": {"read_file", "write_file", "edit_file", "list_directory", "grep"},
    "bash":  {"bash", "bash_batch"},
    "web":   {"web_search", "web_fetch", "http_request", "http_batch", "socket_raw",
              "browser_navigate", "browser_snapshot", "browser_click", "browser_fill", "browser_screenshot", "browser_exec"},
    "diary": {"diary_add", "diary_list", "diary_search", "diary_get"},
    "skills":{"skill_create", "skill_add_tool", "skill_add_reference", "skill_add_asset", "skill_reference_list", "skill_reference_get", "custom_tool_add", "custom_tool_list"},
    "tasks": {"task", "task_poll", "task_list", "task_parallel", "plan"},
    "ctx":   {"context_list", "context_create", "context_get"},
}

KEYWORDS = {
    "web":   ["http", "api", "url", "curl", "web", "site", "page", "recherche", "search", "fetch", "browser", "cve", "vuln", "ssti", "xss", "csrf", "jwt", "injection", "flag", "ctf", "endpoint"],
    "diary": ["souvenir", "retenir", "diary", "note", "sauvegarde", "flag", "important"],
    "skills":["skill", "competence", "creer", "outil", "nouveau"],
    "tasks": ["parallele", "plusieurs fichiers", "sous-agent", "subagent", "background", "simultane", "chaque fichier"],
    "ctx":   ["context", "contexte", "mode", "profil"],
}


def _get_disabled() -> set[str]:
    from ..config import get
    raw = get("tools", "disabled", "")
    return {t.strip() for t in raw.split(",") if t.strip()} if raw else set()


def _dynamic_names(msg: str, history: list = None) -> set[str]:
    lo = msg.lower()
    active = CATEGORIES["files"] | CATEGORIES["bash"]
    for cat, kws in KEYWORDS.items():
        if any(kw in lo for kw in kws):
            active |= CATEGORIES.get(cat, set())
    if history and len(history) > 2: return set()
    all_cat = set().union(*CATEGORIES.values())
    return active | (set(ACTIONS.keys()) - all_cat)


def get_tools(names: set[str] = None) -> tuple[list[dict], dict[str, Callable]]:
    disabled = _get_disabled()
    defs, handlers = [], {}
    for name, a in ACTIONS.items():
        if name in disabled: continue
        if names is None or name in names:
            defs.append(a["definition"]); handlers[name] = a["handler"]
    _merge_custom(defs, handlers, disabled)
    try:
        from ..skills import load_all_skill_tools
        sd, sh = load_all_skill_tools()
        for d in sd:
            n = d["function"]["name"]
            if n not in disabled: defs.append(d); handlers[n] = sh.get(n)
    except Exception: pass
    try:
        from ..mcp import get_mcp_manager
        md, mh = get_mcp_manager().get_all_tools()
        for d in md:
            n = d["function"]["name"]
            if n not in disabled: defs.append(d); handlers[n] = mh.get(n)
    except Exception: pass
    return defs, handlers


def get_tool_names() -> list[str]: return list(ACTIONS.keys())


# ── Custom tools loader ──

def _custom_tools_dir() -> str:
    from ..config import get_ely_dir
    return get_ely_dir("tools")


def _merge_custom(defs: list, handlers: dict, disabled: set):
    import importlib.util as iu, sys
    d = _custom_tools_dir()
    if not _os.path.isdir(d): return
    for fn in sorted(_os.listdir(d)):
        if not fn.endswith(".py") or fn.startswith("_"): continue
        mp = _os.path.join(d, fn)
        mn = f"ely_custom_{fn[:-3]}"
        if mn in sys.modules: del sys.modules[mn]
        try:
            spec = iu.spec_from_file_location(mn, mp)
            if not spec or not spec.loader: continue
            mod = iu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            tools = getattr(mod, "TOOLS", None)
            disp = getattr(mod, "handle_tool", None)
            if tools is None or not callable(disp):
                at, ad = _auto_extract(mod)
                if tools is None: tools = at
                else: tools.extend(at)
                if not callable(disp) and ad: disp = ad
            if not tools or not callable(disp): continue
            for td in tools:
                n = _normalize(td)
                if not n: continue
                fi = n["function"]; on = fi.get("name", "")
                if not on: continue
                prefixed = f"custom__{on}"
                if prefixed in disabled: continue
                defs.append({"type": "function", "function": {**fi, "name": prefixed, "description": f"[Custom] {fi.get('description', '')}"}})
                handlers[prefixed] = (lambda d, o: lambda **kw: str(d(o, kw)))(disp, on)
        except Exception: pass


def _auto_extract(mod) -> tuple[list, Callable]:
    import inspect
    tools, funcs = [], {}
    for attr in dir(mod):
        if not attr.startswith("tool_"): continue
        f = getattr(mod, attr)
        if not callable(f): continue
        name = attr[5:]; desc = (f.__doc__ or "").strip().split("\n")[0]
        funcs[name] = f
        try: sig = inspect.signature(f)
        except: sig = None
        props, req = {}, []
        if sig:
            for pn, p in sig.parameters.items():
                if pn in ("self","cls"): continue
                pt = "integer" if p.annotation is int else "boolean" if p.annotation is bool else "number" if p.annotation is float else "string"
                props[pn] = {"type": pt, "description": f"Parameter: {pn}"}
                if p.default is inspect.Parameter.empty: req.append(pn)
        tools.append({"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": req} if props else {"type": "object", "properties": {}}}})
    def dispatch(name: str, params: dict) -> str:
        f = funcs.get(name); return str(f(**params)) if f else f"Unknown: {name}"
    return tools, dispatch if funcs else None


def _normalize(td: dict) -> dict | None:
    if "type" in td and "function" in td: return td
    name = td.get("name", ""); desc = td.get("description", "")
    if not name: return None
    params = td.get("parameters", {"type": "object", "properties": {}})
    if "type" not in params:
        props = params
        req = [k for k, v in props.items() if isinstance(v, dict) and v.get("required")]
        params = {"type": "object", "properties": props}; params["required"] = req if req else []
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": params}}


def reload_custom():
    import sys
    d = _custom_tools_dir()
    if not _os.path.isdir(d): return
    for fn in sorted(_os.listdir(d)):
        if fn.endswith(".py"):
            sys.modules.pop(f"ely_custom_{fn[:-3]}", None)


# ── Utilities for agent.py ──

def get_workspace_info() -> str:
    ws = workspace_dir(); s = "docker" if is_sandbox() else "direct"
    return f"Workspace: {ws} | Bash: {s}"

def get_diary_context(limit: int = 5) -> str:
    import os as _os, json as _j
    from ..config import get_ely_dir
    d = get_ely_dir("memory/diary")
    if not _os.path.isdir(d): return ""
    entries = []
    for fn in sorted(_os.listdir(d)):
        if fn.endswith(".json"):
            try:
                with open(_os.path.join(d, fn)) as f: e = _j.load(f)
                if isinstance(e, dict) and "id" in e: entries.append(e)
            except: pass
    if not entries: return ""
    entries.sort(key=lambda e: e.get("id", 0))
    lines = ["\n**Diary :**"]
    for e in entries[-limit:]:
        tags = f" [{', '.join(e.get('tags', []))}]" if e.get("tags") else ""
        lines.append(f"- [#{e['id']}] {e['content'][:150]}{tags}")
    return "\n".join(lines)


# ── Import all tool modules to trigger @action ──
import os as _os
from . import bash, files, web, diary, skills, contexts, subagents, custom, sandbox
try: from . import browser
except ImportError: pass

# Re-export commonly used symbols
from .subagents import _background_tasks, _task_lock, _task_id_counter, _get_background_results
from .bash import tool_bash, tool_bash_batch
from .files import tool_read_file, tool_write_file, tool_edit_file, tool_list_directory, tool_grep
from .web import tool_web_search, tool_web_fetch, tool_http_request, tool_http_batch, tool_socket_raw
from .diary import tool_diary_add, tool_diary_list, tool_diary_search, tool_diary_get, _load_diary, _save_entry
from .sandbox import _fs_sandbox_enabled

# Backward-compat aliases
from ._core import is_sandbox as _is_sandbox_enabled, workspace_dir as _workspace_dir, run_direct as _run_direct
cleanup_sandbox = lambda: None  # No-op, sandbox cleanup handled by Docker --rm
def _skills_user_dir():
    import os as _os
    from ..config import get_ely_dir as _d
    return _os.path.join(_d(), "skills")
