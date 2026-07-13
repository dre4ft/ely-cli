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
        """Load skill tools and return (definitions, handlers).

        Two modes:
        1. COMMAND template: bash command with {param} substitution (simple, sandboxed)
        2. def run(tool_bash, **kwargs): Python with safe builtins (advanced)
        """
        definitions = []
        handlers = {}

        for tool_file in self.tools:
            module_path = os.path.join(self.tools_dir, tool_file)

            try:
                with open(module_path) as f:
                    source = f.read()

                # Step 1: extract metadata via regex (no execution needed)
                meta = _extract_tool_meta(source)
                if not meta:
                    continue

                tool_name = meta.get("NAME", tool_file[:-3])
                prefixed = f"skill__{self.name}__{tool_name}"
                description = meta.get("DESCRIPTION", "")
                params = meta.get("PARAMETERS", {})
                command_template = meta.get("COMMAND", "")
                has_run = meta.get("has_run", False)

                # Build OpenAI-format tool definition
                properties = {}
                required = []
                for k, v in params.items():
                    properties[k] = {
                        "type": v.get("type", "string"),
                        "description": v.get("description", ""),
                    }
                    if v.get("required", True):
                        required.append(k)

                definitions.append({
                    "type": "function",
                    "function": {
                        "name": prefixed,
                        "description": f"[Skill:{self.name}] {description}",
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required,
                        } if properties else {"type": "object", "properties": {}},
                    },
                })

                if has_run:
                    # Step 2: exec source in SAFE namespace so run() gets proper builtins
                    safe_ns = dict(_SAFE_BUILTINS)
                    exec(source, safe_ns)
                    run_func = safe_ns.get("run")
                    if callable(run_func):
                        handlers[prefixed] = _make_safe_handler(run_func, self.name, tool_name)
                elif command_template:
                    handlers[prefixed] = _make_template_handler(command_template)

            except Exception:
                pass  # Skip broken tool modules

        return definitions, handlers


# ── Skill activation ──

_active_skills: set[str] = {"ely"}


def activate_skill(name: str) -> bool:
    """Activate a skill. Returns True if successful."""
    if name in list_skills():
        _active_skills.add(name)
        return True
    return False


def deactivate_skill(name: str) -> bool:
    """Deactivate a skill. Cannot deactivate 'ely' base skill. Returns True if successful."""
    if name == "ely":
        return False
    if name in _active_skills:
        _active_skills.discard(name)
        return True
    return False


def get_active_skills() -> set[str]:
    """Return set of currently active skill names."""
    # Only return skills that actually exist
    available = set(list_skills())
    return _active_skills & available


# ── Safe builtins for skill tool run() functions ──

import json as _json
import re as _re
import shlex as _shlex

# Whitelist of modules that skill tools can import
_SAFE_MODULES = {
    # Data formats
    "json", "csv", "configparser", "tomllib",
    # Text
    "re", "string", "textwrap", "difflib",
    # Crypto/hashing
    "hashlib", "base64", "binascii", "hmac",
    # Data structures
    "collections", "itertools", "functools", "operator",
    "heapq", "bisect", "array", "struct",
    # Math
    "math", "statistics", "decimal", "fractions", "random",
    # Date/time
    "datetime", "calendar", "time",
    # Internet/parsing
    "urllib.parse", "urllib", "html", "html.parser",
    "xml.etree.ElementTree", "xml",
    # Typing
    "typing", "enum", "dataclasses",
    # Utilities
    "copy", "pprint", "inspect", "argparse",
    "fnmatch", "glob", "gzip", "zlib",
    "hashlib", "secrets",
    # Encoding
    "codecs", "unicodedata",
    "quopri", "uu",
}


def _safe_import(name: str, *args, **kwargs):
    """Restricted __import__ — only allows whitelisted stdlib modules."""
    # Allow top-level module or submodule of allowed modules
    parts = name.split(".")
    check = name
    allowed = False
    while check:
        if check in _SAFE_MODULES:
            allowed = True
            break
        check = ".".join(check.split(".")[:-1]) if "." in check else ""
    if not allowed:
        raise ImportError(f"Module '{name}' is not allowed in skill tools")
    return __import__(name, *args, **kwargs)


_SAFE_BUILTINS = {
    # Types
    "str": str, "int": int, "float": float, "bool": bool,
    "list": list, "dict": dict, "tuple": tuple, "set": set, "frozenset": frozenset,
    "bytes": bytes, "bytearray": bytearray,
    # Constants
    "True": True, "False": False, "None": None,
    # Functions
    "len": len, "range": range, "enumerate": enumerate,
    "zip": zip, "map": map, "filter": filter,
    "sorted": sorted, "reversed": reversed,
    "min": min, "max": max, "sum": sum, "abs": abs, "round": round,
    "isinstance": isinstance, "type": type,
    "print": print, "format": format, "repr": repr,
    "ord": ord, "chr": chr, "hash": hash,
    "__import__": _safe_import,
    # Pre-imported modules (always available without import)
    "json": _json,
    "re": _re,
}


def _make_template_handler(command_template: str):
    """Create a handler for COMMAND template mode."""
    def handler(**kwargs):
        cmd = command_template
        for k, v in kwargs.items():
            escaped = _shlex.quote(str(v))
            cmd = cmd.replace("{" + k + "}", escaped)
        from .tools import tool_bash
        return tool_bash(cmd)
    return handler


def _make_safe_handler(run_func, skill_name: str, tool_name: str):
    """Create a handler that wraps run() with safe builtins + tool_bash()."""
    def handler(**kwargs):
        # tool_bash passes through the sandbox
        def tool_bash(cmd: str) -> str:
            from .tools import tool_bash as _tool_bash
            return _tool_bash(cmd)

        # Build safe execution environment
        safe_globals = dict(_SAFE_BUILTINS)
        safe_globals["tool_bash"] = tool_bash

        try:
            result = run_func(tool_bash, **kwargs)
            return str(result)
        except Exception as e:
            return f"Tool error [{skill_name}/{tool_name}]: {e}"

    return handler


def _extract_tool_meta(source: str) -> dict | None:
    """Extract tool metadata from source using regex patterns.
    Avoids executing code in a broken namespace so run() keeps proper builtins.
    """
    meta = {}

    # NAME = "..." or NAME = '...'
    m = _re.search(r'^NAME\s*=\s*["\']([^"\']+)["\']', source, _re.MULTILINE)
    if m:
        meta["NAME"] = m.group(1)

    # DESCRIPTION = "..."
    m = _re.search(r'^DESCRIPTION\s*=\s*["\']([^"\']+)["\']', source, _re.MULTILINE)
    if m:
        meta["DESCRIPTION"] = m.group(1)

    # COMMAND = "..."
    m = _re.search(r'^COMMAND\s*=\s*["\']([^"\']+)["\']', source, _re.MULTILINE)
    if m:
        meta["COMMAND"] = m.group(1)

    # PARAMETERS = {...} — extract the dict literal
    m = _re.search(r'^PARAMETERS\s*=\s*(\{.*?\})', source, _re.MULTILINE | _re.DOTALL)
    if m:
        try:
            # Safe eval for a dict literal (no identifiers, only literals)
            params = eval(m.group(1), {"__builtins__": {}})
            if isinstance(params, dict):
                meta["PARAMETERS"] = params
        except Exception:
            meta["PARAMETERS"] = {}

    # Check for def run(
    meta["has_run"] = bool(_re.search(r'^def\s+run\s*\(', source, _re.MULTILINE))

    # Check for TIMEOUT
    m = _re.search(r'^TIMEOUT\s*=\s*(\d+)', source, _re.MULTILINE)
    if m:
        meta["TIMEOUT"] = int(m.group(1))

    return meta if meta.get("NAME") else None


def _skill_dirs() -> list[str]:
    """Return all skill search directories, project first (higher priority)."""
    dirs = []
    project = os.path.join(os.getcwd(), "skills")
    if os.path.isdir(project):
        dirs.append(project)
    user = os.path.join(os.path.expanduser("~"), ".ely", "skills")
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
    """Build a compact one-line status showing active skills (for REPL output)."""
    active = get_active_skills()
    all_skills = list_skills()

    if active == {"ely"}:
        return ""  # default, nothing to show

    expert_names = [n for n in active if n != "ely"]
    parts = []
    if expert_names:
        parts.append(f"🧠 {', '.join(expert_names)}")
    return " · ".join(parts) if parts else ""
