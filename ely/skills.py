'''
Skills loader — directory-based skills with SKILL.md, Python tools, references, and assets.

Skills are stored in:
  - ~/.ely/skills/<name>/   (user-level, global)
  - ./skills/<name>/         (project-level, local — overrides global)

Each skill directory:
  my-skill/
  ├── SKILL.md          # Required: YAML frontmatter + markdown instructions
  ├── tools/            # Optional: Python tools registered as agent tools
  ├── references/       # Optional: documentation files
  └── assets/           # Optional: templates, resources

Activation:
  - By default, only the "ely" base skill is active.
  - Use activate_skill(name) / deactivate_skill(name) to toggle.
  - When a non-ely skill is active, "expert mode" kicks in:
    the skill's instructions take priority in the system prompt.

Tool files are Python modules with:
  NAME = "tool_name"
  DESCRIPTION = "What this tool does"
  PARAMETERS = {"arg": {"type": "string", "description": "..."}}
  def run(**kwargs) -> str: ...
'''

import importlib.util
import os
from dataclasses import dataclass, field


@dataclass
class Skill:
    """A loaded skill with its directory structure."""
    name: str
    description: str = ""
    instructions: str = ""
    path: str = ""          # absolute path to skill directory
    tools_dir: str = ""
    references_dir: str = ""
    assets_dir: str = ""

    @property
    def tools(self) -> list[str]:
        if self.tools_dir and os.path.isdir(self.tools_dir):
            return sorted(f for f in os.listdir(self.tools_dir) if f.endswith(".py"))
        return []

    @property
    def references(self) -> list[str]:
        if self.references_dir and os.path.isdir(self.references_dir):
            return sorted(os.listdir(self.references_dir))
        return []

    @property
    def assets(self) -> list[str]:
        if self.assets_dir and os.path.isdir(self.assets_dir):
            return sorted(os.listdir(self.assets_dir))
        return []

    def load_tools(self) -> tuple[list[dict], dict[str, callable]]:
        """Load skill tools via standard Python import.

        Each .py file in tools/ defines tool_* functions.
        The backend auto-generates TOOLS + handle_tool from them.
        Explicit TOOLS/handle_tool still supported for backward compat.

        Tool names are prefixed: skill__<skill_name>__<tool_name>
        """
        from .tools import _extract_tools_from_module

        definitions = []
        handlers = {}

        for tool_file in self.tools:
            module_path = os.path.join(self.tools_dir, tool_file)

            try:
                spec = importlib.util.spec_from_file_location(
                    f"ely_skill_{self.name}_{tool_file[:-3]}", module_path
                )
                if not spec or not spec.loader:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)

                # Check for explicit TOOLS first (backward compat)
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

                    prefixed = f"skill__{self.name}__{original_name}"
                    prefixed_def = {
                        "type": "function",
                        "function": {
                            **func_info,
                            "name": prefixed,
                            "description": f"[Skill:{self.name}] {func_info.get('description', '')}",
                        },
                    }
                    definitions.append(prefixed_def)

                    def make_handler(d, orig_name):
                        def handler(**kwargs):
                            try:
                                return str(d(orig_name, kwargs))
                            except Exception as e:
                                return f"Tool error [{self.name}/{orig_name}]: {e}"
                        return handler

                    handlers[prefixed] = make_handler(dispatcher, original_name)

            except Exception:
                pass

        return definitions, handlers


# ── Skill activation ──

import json as _json

_active_skills: set[str] = {"ely"}


def _active_skills_file() -> str:
    from .config import get_ely_dir
    return os.path.join(get_ely_dir(), "active_skills.json")


def save_active_skills():
    """Persist active skills to disk."""
    os.makedirs(os.path.dirname(_active_skills_file()), exist_ok=True)
    with open(_active_skills_file(), "w") as f:
        _json.dump(sorted(_active_skills), f)


def load_active_skills():
    """Load persisted active skills from disk."""
    global _active_skills
    path = _active_skills_file()
    if os.path.isfile(path):
        try:
            with open(path) as f:
                saved = _json.load(f)
            if isinstance(saved, list):
                _active_skills = set(saved)
                # Always ensure 'ely' is active
                _active_skills.add("ely")
        except Exception:
            pass


def activate_skill(name: str) -> bool:
    """Activate a skill. Deactivates any previously active non-ely skill.
    Only one skill can be active at a time (plus 'ely'). Persists to disk."""
    if name not in list_skills():
        return False
    # Deactivate all non-ely skills first
    for s in list(_active_skills):
        if s != "ely":
            _active_skills.discard(s)
    _active_skills.add(name)
    save_active_skills()
    return True


def deactivate_skill(name: str) -> bool:
    """Deactivate a skill. Cannot deactivate 'ely' base skill. Persists to disk."""
    if name == "ely":
        return False
    if name in _active_skills:
        _active_skills.discard(name)
        save_active_skills()
        return True
    return False


def get_active_skills() -> set[str]:
    """Return set of currently active skill names."""
    available = set(list_skills())
    return _active_skills & available


def _skill_dirs() -> list[str]:
    """Return all skill search directories, project first (higher priority)."""
    dirs = []
    project = os.path.join(os.getcwd(), "skills")
    if os.path.isdir(project):
        dirs.append(project)
    from .config import get_ely_dir
    user = os.path.join(get_ely_dir(), "skills")
    if os.path.isdir(user):
        dirs.append(user)
    return dirs


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from SKILL.md. Returns (metadata, body)."""
    metadata = {}
    body = content

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            try:
                import yaml
                metadata = yaml.safe_load(parts[1]) or {}
            except Exception:
                for line in parts[1].strip().split("\n"):
                    if ":" in line:
                        k, v = line.split(":", 1)
                        metadata[k.strip()] = v.strip()
            body = parts[2].strip()
    return metadata, body


def load_skill(name: str) -> Skill | None:
    """Load a skill by name. Returns Skill or None."""
    for d in _skill_dirs():
        skill_dir = os.path.join(d, name)
        skill_md = os.path.join(skill_dir, "SKILL.md")
        if os.path.isfile(skill_md):
            with open(skill_md, encoding="utf-8") as f:
                content = f.read()

            meta, instructions = _parse_frontmatter(content)

            tools_dir = os.path.join(skill_dir, "tools")
            refs_dir = os.path.join(skill_dir, "references")
            assets_dir = os.path.join(skill_dir, "assets")

            return Skill(
                name=meta.get("name", name),
                description=meta.get("description", ""),
                instructions=instructions,
                path=skill_dir,
                tools_dir=tools_dir if os.path.isdir(tools_dir) else "",
                references_dir=refs_dir if os.path.isdir(refs_dir) else "",
                assets_dir=assets_dir if os.path.isdir(assets_dir) else "",
            )
    return None


def list_skills() -> list[str]:
    """List available skill names (project-level overrides user-level)."""
    names = set()
    for d in _skill_dirs():
        if os.path.isdir(d):
            for name in os.listdir(d):
                sd = os.path.join(d, name)
                if os.path.isdir(sd) and os.path.isfile(os.path.join(sd, "SKILL.md")):
                    names.add(name)
    return sorted(names)


def load_all_skill_tools() -> tuple[list[dict], dict[str, callable]]:
    """Load tools from ACTIVE skills only.
    Returns (definitions, handlers) for merging into get_tools().
    """
    all_defs = []
    all_handlers = {}
    for name in get_active_skills():
        skill = load_skill(name)
        if skill:
            defs, handlers = skill.load_tools()
            all_defs.extend(defs)
            all_handlers.update(handlers)
    return all_defs, all_handlers


def read_skill_reference(skill_name: str, ref_name: str) -> str | None:
    """Read a reference file from a skill."""
    skill = load_skill(skill_name)
    if not skill or not skill.references_dir:
        return None
    path = os.path.join(skill.references_dir, ref_name)
    if not os.path.realpath(path).startswith(os.path.realpath(skill.references_dir)):
        return None
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return None


def read_skill_asset(skill_name: str, asset_name: str) -> str | None:
    """Read an asset file from a skill."""
    skill = load_skill(skill_name)
    if not skill or not skill.assets_dir:
        return None
    path = os.path.join(skill.assets_dir, asset_name)
    if not os.path.realpath(path).startswith(os.path.realpath(skill.assets_dir)):
        return None
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return None


def build_skills_prompt() -> str:
    """Build the skills section for the system prompt.
    When a non-ely skill is active, uses 'expert mode' layout
    where the active skill takes priority over the base skill.
    """
    active = get_active_skills()
    if not active:
        return ""

    base = load_skill("ely")
    experts = [(name, load_skill(name)) for name in active if name != "ely"]
    experts = [(n, s) for n, s in experts if s]

    lines = []

    if experts:
        # Expert mode: active skill first, then base
        for name, skill in experts:
            desc = f" — {skill.description}" if skill.description else ""
            lines.append(f"\n**Mode Expert — Compétence active : `{name}`{desc}**")
            lines.append(skill.instructions)
            if skill.tools:
                tool_names = [t[:-3] for t in skill.tools]
                lines.append(f"\n**Outils spécialisés :** {', '.join(tool_names)}")
            if skill.references:
                lines.append(f"**Références :** {', '.join(skill.references)}")

        # Base skill in secondary position
        if base and "ely" in active:
            lines.append(f"\n---\n**Compétence de base :**")
            lines.append(base.instructions)
    else:
        # No expert skills — just the base
        if base:
            lines.append(base.instructions)

    return "\n".join(lines)


def build_skills_status_line() -> str:
    """Build a compact one-line status showing the active skill (for REPL output)."""
    active = get_active_skills()
    expert = next((n for n in active if n != "ely"), None)
    return f"🧠 {expert}" if expert else ""
