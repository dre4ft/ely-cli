"""Bash tools — shell command execution (sandboxed or direct)."""
import json
from . import _action, _is_sandbox_enabled, _run_direct, _run_in_sandbox, _run_parallel


@_action("bash", "Execute a shell command in the workspace directory.",
         {"command": {"type": "string", "description": "The shell command to execute."}})
def tool_bash(command: str) -> str:
    try:
        if _is_sandbox_enabled():
            return _run_in_sandbox(command)
        else:
            return _run_direct(command)
    except Exception as e:
        return f"Error: {e}"


@_action("bash_batch", "Execute multiple bash commands in PARALLEL. Much faster than calling bash N times.",
         {"commands": {"type": "string", "description": "JSON array of commands, e.g. [\"ls -la\", \"cat file.txt\"]."}})
def tool_bash_batch(commands: str) -> str:
    try:
        cmds = json.loads(commands)
        if not isinstance(cmds, list): return "Error: commands must be a JSON array"
    except json.JSONDecodeError:
        return "Error: invalid JSON for commands"
    runner = _run_direct if not _is_sandbox_enabled() else _run_in_sandbox
    results = _run_parallel(cmds, runner)
    return "\n\n".join(f"--- [{i}] $ {cmd} ---\n{output}" for i, (cmd, output) in enumerate(zip(cmds, results)))
