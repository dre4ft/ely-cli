"""Filesystem sandbox — OS-level write restriction (macOS sandbox-exec / Linux bwrap)."""
import os
import subprocess
import tempfile
from . import _workspace_dir


def _fs_sandbox_enabled() -> bool:
    from ..config import get_bool
    return get_bool("tools", "fs_sandbox", True) and _has_sandbox_available()


def _has_sandbox_available() -> bool:
    import shutil
    return shutil.which("sandbox-exec") is not None or shutil.which("bwrap") is not None


def _run_fs_sandboxed(cmd: str, workspace: str, timeout: int = 30) -> str:
    import shutil
    if shutil.which("sandbox-exec"): return _sandbox_macos(cmd, workspace, timeout)
    elif shutil.which("bwrap"): return _sandbox_linux(cmd, workspace, timeout)
    else:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=workspace)
        out = result.stdout
        if result.stderr: out += f"\n[stderr]\n{result.stderr}"
        return out[:3000] or f"(exit code {result.returncode})"


def _sandbox_macos(cmd: str, workspace: str, timeout: int = 30) -> str:
    profile = f"""(version 1)
(allow default)
(deny file-write* (subpath "/"))
(allow file-write*
    (subpath "{workspace}")
    (subpath "/tmp")
    (subpath "/var/tmp")
    (subpath "/dev"))
(allow process-exec)
(allow signal (target self))
(allow process-fork)
(allow network*)
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sb', delete=False) as f:
        f.write(profile); profile_path = f.name
    try:
        result = subprocess.run(["sandbox-exec", "-f", profile_path, "sh", "-c", cmd],
                                capture_output=True, text=True, timeout=timeout, cwd=workspace)
        out = result.stdout.strip(); err = result.stderr.strip()
        if result.returncode != 0:
            msg = f"Error: sandbox blocked write outside workspace ({workspace})." if result.returncode == -6 else f"Error: command failed (exit code {result.returncode})"
            if out: msg += f"\n{out}"
            if err: msg += f"\n{err}"
            return msg
        if err: out += f"\n[stderr]\n{err}"
        return out[:3000] if out else "(no output)"
    finally:
        try: os.unlink(profile_path)
        except Exception: pass


def _sandbox_linux(cmd: str, workspace: str, timeout: int = 30) -> str:
    result = subprocess.run(
        ["bwrap", "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin", "--ro-bind", "/sbin", "/sbin",
         "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64", "--ro-bind", "/etc", "/etc",
         "--bind", workspace, workspace, "--chdir", workspace, "--unshare-all", "--share-net",
         "--die-with-parent", "sh", "-c", cmd],
        capture_output=True, text=True, timeout=timeout)
    out = result.stdout.strip(); err = result.stderr.strip()
    if result.returncode != 0:
        msg = f"Error: sandbox blocked access outside workspace ({workspace})." if result.returncode == 126 or "denied" in (err or "").lower() else f"Error: command failed (exit code {result.returncode})"
        if out: msg += f"\n{out}"
        if err: msg += f"\n{err}"
        return msg
    if err: out += f"\n[stderr]\n{err}"
    return out[:3000] if out else "(no output)"
