"""Bash tools."""
import json
from ._core import action, is_sandbox, run_direct, run_sandboxed, run_parallel


@action("bash", "Execute a shell command in the workspace directory.",
        {"command": {"type": "string", "description": "Shell command to execute."}})
def tool_bash(command: str) -> str:
    try:
        return run_sandboxed(command) if is_sandbox() else run_direct(command)
    except Exception as e:
        return f"Error: {e}"


@action("bash_batch", "Execute multiple bash commands in PARALLEL.",
        {"commands": {"type": "string", "description": "JSON array of commands, e.g. [\"ls\", \"cat f.txt\"]."}})
def tool_bash_batch(commands: str) -> str:
    try:
        cmds = json.loads(commands)
        if not isinstance(cmds, list): return "Error: commands must be a JSON array"
    except json.JSONDecodeError: return "Error: invalid JSON"
    runner = run_direct if not is_sandbox() else run_sandboxed
    results = run_parallel(cmds, runner)
    return "\n\n".join(f"--- [{i}] $ {cmd} ---\n{o}" for i, (cmd, o) in enumerate(zip(cmds, results)))
