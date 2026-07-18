"""Shared infrastructure for tools — decorator, workspace, bash, parallel."""
import os, json, subprocess
from typing import Callable

ACTIONS: dict[str, dict] = {}


def action(name: str, description: str, parameters: dict, optional: list[str] = None):
    """Decorator: register a tool function."""
    optional = optional or []
    def decorator(func):
        ACTIONS[name] = {
            "definition": {"type": "function", "function": {
                "name": name, "description": description,
                "parameters": {"type": "object", "properties": parameters,
                               "required": [k for k in parameters if k not in optional]}}},
            "handler": func,
        }
        return func
    return decorator


# ── Workspace ──

def workspace_dir() -> str:
    from ..config import get
    ws = os.environ.get("ELY_WORKSPACE", "") or get("tools", "workspace", os.getcwd())
    return os.path.realpath(os.path.expanduser(ws))


def resolve_path(file_path: str) -> str:
    ws = workspace_dir()
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


def relative_path(abs_path: str) -> str:
    ws = workspace_dir()
    return abs_path[len(ws)+1:] if abs_path.startswith(ws + os.sep) else ("." if abs_path == ws else abs_path)


# ── Bash ──

def is_sandbox() -> bool:
    from ..config import get
    v = os.environ.get("ELY_BASH_SANDBOX", "") or get("tools", "bash_sandbox", "direct")
    return v.lower() in ("docker", "sandbox", "1", "true", "yes")


def run_direct(cmd: str, timeout: int = 30, sanitize: bool = True) -> str:
    ws = workspace_dir()
    if sanitize:
        cmd = _sanitize(cmd)
        err = _validate_paths(cmd, ws)
        if err: return err
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=ws)
    out = result.stdout
    if result.stderr: out += f"\n[stderr]\n{result.stderr}"
    return out[:3000] or f"(exit code {result.returncode})"


def run_sandboxed(cmd: str, timeout: int = 30) -> str:
    ws = workspace_dir()
    cmd = cmd.replace(ws, "/workspace")
    check = subprocess.run(["docker", "inspect", "ely-sandbox"], capture_output=True, text=True)
    if check.returncode != 0:
        create = subprocess.run(["docker", "run", "-d", "--name", "ely-sandbox", "--rm", "-v",
                                 f"{ws}:/workspace", "-w", "/workspace", "--network", "none",
                                 "alpine:latest", "tail", "-f", "/dev/null"],
                                capture_output=True, text=True, timeout=10)
        if create.returncode != 0: return f"Error creating sandbox: {create.stderr}"
    result = subprocess.run(["docker", "exec", "-i", "ely-sandbox", "sh", "-c", cmd],
                            capture_output=True, text=True, timeout=timeout)
    out = result.stdout
    if result.stderr: out += f"\n[stderr]\n{result.stderr}"
    return out[:3000] or f"(exit code {result.returncode})"


def _sanitize(cmd: str) -> str:
    ws = workspace_dir()
    cmd = cmd.replace(ws, ".")
    cmd = cmd.replace(ws.rstrip("/") + "/", "./")
    if ".." not in cmd: return cmd
    import shlex, re
    try: parts = shlex.split(cmd)
    except ValueError: parts = cmd.split()
    out = []
    for p in parts:
        if p == '..': out.append('.')
        elif '..' in p:
            n = os.path.normpath(p)
            n = re.sub(r'^(\.\./)+', '', n)
            n = re.sub(r'/\.\.$', '', n)
            n = re.sub(r'^\.\.$', '.', n)
            if not n: n = '.'
            if n.startswith('/'): n = '.' + n
            out.append(n)
        else: out.append(p)
    return ' '.join(out)


def _validate_paths(cmd: str, ws: str) -> str | None:
    import shlex
    try: parts = shlex.split(cmd)
    except ValueError: parts = cmd.split()
    for p in parts:
        if p.startswith('-') or p in ('|',';','&&','||','>','>>','<','2>','1>','&>'): continue
        if '=' in p and '/' not in p: continue
        if '/' not in p and not any(p.endswith(e) for e in ('.txt','.py','.js','.json','.yaml','.yml','.md','.log','.csv','.xml','.html','.css','.sh','.conf','.cfg','.ini','.env')) and not p.startswith('.'): continue
        try:
            r = os.path.realpath(p) if os.path.isabs(p) else os.path.realpath(os.path.join(ws, p))
            if not r.startswith(ws.rstrip('/') + '/') and r != ws.rstrip('/'):
                return f"Error: path '{p}' outside workspace ({r}). Use relative paths."
        except Exception: continue
    return None


# ── Parallel ──

def run_parallel(items: list, func) -> list[str]:
    from concurrent.futures import ThreadPoolExecutor
    n = min(len(items), 8)
    with ThreadPoolExecutor(max_workers=n) as ex:
        futures = {ex.submit(func, item): i for i, item in enumerate(items)}
        results = [""] * len(items)
        for f in futures:
            idx = futures[f]
            try: results[idx] = f.result(timeout=120)
            except Exception as e: results[idx] = f"Error: {e}"
    return results
