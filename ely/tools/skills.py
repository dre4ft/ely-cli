"""Skill management tools — create and extend agent skills."""
import os
from ._core import action
from ..config import get_ely_dir


def _skills_dir() -> str:
    return os.path.join(get_ely_dir(), "skills")


def _safe_path(base: str, name: str) -> str:
    clean = name.lstrip("/").replace("\\", "/")
    parts = [p for p in clean.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts): raise ValueError(f"Path traversal: {name}")
    return os.path.join(base, *parts) if parts else base


def _validate_tool(content: str) -> str | None:
    if len(content) > 65536: return "Too large (max 64KB)"
    try: compile(content, "<tool>", "exec")
    except SyntaxError as e: return f"Syntax error: {e}"
    return None


@action("skill_create", "Create a new skill directory with SKILL.md.",
         {"name": {"type": "string", "description": "Skill name (slug)."},
          "description": {"type": "string", "description": "One-line description."},
          "instructions": {"type": "string", "description": "Markdown instructions for the system prompt."}})
def tool_skill_create(name: str, description: str, instructions: str) -> str:
    try: skill_dir = _safe_path(_skills_dir(), name)
    except ValueError as e: return f"Error: {e}"
    os.makedirs(skill_dir, exist_ok=True)
    frontmatter = f"---\nname: {name}\ndescription: {description}\n---\n\n"
    with open(os.path.join(skill_dir, "SKILL.md"), "w") as f: f.write(frontmatter + instructions)
    for sub in ("tools", "references", "assets"): os.makedirs(os.path.join(skill_dir, sub), exist_ok=True)
    return f"Skill '{name}' created in {skill_dir}"


@action("skill_add_tool", "Add a Python tool module to a skill.",
         {"skill_name": {"type": "string", "description": "The skill to add the tool to."},
          "tool_filename": {"type": "string", "description": "Python filename (must end with .py)."},
          "content": {"type": "string", "description": "Python code with TOOLS list and handle_tool(name, params) function."}})
def tool_skill_add_tool(skill_name: str, tool_filename: str, content: str) -> str:
    if not tool_filename.endswith(".py"): return "Error: tool filename must end with .py"
    error = _validate_tool(content)
    if error: return f"Error: invalid tool — {error}"
    try:
        skill_dir = _safe_path(_skills_dir(), skill_name)
        tool_path = _safe_path(os.path.join(skill_dir, "tools"), tool_filename)
    except ValueError as e: return f"Error: {e}"
    if not os.path.isdir(skill_dir): return f"Error: skill '{skill_name}' not found."
    os.makedirs(os.path.dirname(tool_path), exist_ok=True)
    with open(tool_path, "w") as f: f.write(content)
    return f"Tool '{tool_filename}' added to skill '{skill_name}' ({len(content)} bytes)."


@action("skill_add_reference", "Add a reference document to a skill.",
         {"skill_name": {"type": "string", "description": "The skill."},
          "ref_name": {"type": "string", "description": "Reference filename (e.g. 'api-docs.md')."},
          "content": {"type": "string", "description": "Reference content in markdown."}})
def tool_skill_add_reference(skill_name: str, ref_name: str, content: str) -> str:
    try:
        skill_dir = _safe_path(_skills_dir(), skill_name)
        ref_path = _safe_path(os.path.join(skill_dir, "references"), ref_name)
    except ValueError as e: return f"Error: {e}"
    if not os.path.isdir(skill_dir): return f"Error: skill '{skill_name}' not found."
    os.makedirs(os.path.dirname(ref_path), exist_ok=True)
    with open(ref_path, "w") as f: f.write(content)
    return f"Reference '{ref_name}' added to skill '{skill_name}' ({len(content)} bytes)."


@action("skill_add_asset", "Add an asset file to a skill.",
         {"skill_name": {"type": "string", "description": "The skill."},
          "asset_name": {"type": "string", "description": "Asset filename."},
          "content": {"type": "string", "description": "Asset file content."}})
def tool_skill_add_asset(skill_name: str, asset_name: str, content: str) -> str:
    try:
        skill_dir = _safe_path(_skills_dir(), skill_name)
        asset_path = _safe_path(os.path.join(skill_dir, "assets"), asset_name)
    except ValueError as e: return f"Error: {e}"
    if not os.path.isdir(skill_dir): return f"Error: skill '{skill_name}' not found."
    os.makedirs(os.path.dirname(asset_path), exist_ok=True)
    with open(asset_path, "w") as f: f.write(content)
    return f"Asset '{asset_name}' added to skill '{skill_name}' ({len(content)} bytes)."


@action("skill_reference_list", "List reference documents available for the active skill.", {})
def tool_skill_reference_list() -> str:
    from ..skills import get_active_skills, load_skill
    active = get_active_skills()
    expert = next((n for n in active if n != "ely"), None)
    if not expert: return "No active skill."
    skill = load_skill(expert)
    if not skill or not skill.references: return f"No references for skill '{expert}'."
    return f"References for '{expert}':\n" + "\n".join(f"  - {r}" for r in skill.references)


@action("skill_reference_get", "Read a specific reference document from the active skill.",
         {"ref_name": {"type": "string", "description": "Reference filename."}})
def tool_skill_reference_get(ref_name: str) -> str:
    from ..skills import get_active_skills, load_skill, read_skill_reference
    active = get_active_skills()
    expert = next((n for n in active if n != "ely"), None)
    if not expert: return "No active skill."
    skill = load_skill(expert)
    if not skill: return f"Skill '{expert}' not found."
    content = read_skill_reference(expert, ref_name)
    if content is None: return f"Reference '{ref_name}' not found. Available: {', '.join(skill.references)}"
    return content
