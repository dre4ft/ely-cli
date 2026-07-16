"""Custom global tools — user-defined tools always available."""
import os
from . import _action, _custom_tools_dir, _validate_tool_content


@_action("custom_tool_add", "Create a global custom tool available in all sessions.",
         {"tool_filename": {"type": "string", "description": "Python filename (must end with .py)."},
          "content": {"type": "string", "description": "Python code with TOOLS list and handle_tool(name, params) function."}})
def tool_custom_tool_add(tool_filename: str, content: str) -> str:
    if not tool_filename.endswith(".py"): return "Error: filename must end with .py"
    error = _validate_tool_content(content)
    if error: return f"Error: invalid tool — {error}"
    d = _custom_tools_dir()
    os.makedirs(d, exist_ok=True)
    tool_path = os.path.join(d, tool_filename)
    with open(tool_path, "w") as f: f.write(content)
    return f"Global custom tool '{tool_filename}' saved to {tool_path}"


@_action("custom_tool_list", "List global custom tools.", {})
def tool_custom_tool_list() -> str:
    d = _custom_tools_dir()
    if not os.path.isdir(d): return "No custom tools directory."
    files = sorted(f for f in os.listdir(d) if f.endswith(".py"))
    return "\n".join(f"  - {f}" for f in files) if files else "No custom tools."
