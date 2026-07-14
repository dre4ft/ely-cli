"""
Context management — persistent, customizable contexts stored as markdown files.

Contexts are stored in:
  - ~/.ely/contexts/<name>.md   (user-level, global)
  - ./.ely/contexts/<name>.md   (project-level, overrides user)

Each context file uses YAML frontmatter:
  ---
  name: code
  description: Mode développement
  ---
  Prompt text here...

Active context is persisted in ~/.ely/context.json
"""

import os


def _contexts_dirs() -> list[str]:
    """Return context search directories, project first (higher priority)."""
    from .config import get_ely_dir
    dirs = []
    project = os.path.join(os.getcwd(), ".ely", "contexts")
    if os.path.isdir(project):
        dirs.append(project)
    user = os.path.join(get_ely_dir(), "contexts")
    if os.path.isdir(user):
        dirs.append(user)
    return dirs


def _ensure_defaults():
    """Create default contexts if they don't exist."""
    from .config import get_ely_dir
    user_dir = os.path.join(get_ely_dir(), "contexts")
    os.makedirs(user_dir, exist_ok=True)

    defaults = {
        "default": {
            "description": "Mode général — terminal et tâches courantes",
            "prompt": "Tu es dans un terminal. L'utilisateur travaille dans le répertoire courant.",
        },
        "code": {
            "description": "Mode développement — exploration et écriture de code",
            "prompt": "L'utilisateur travaille sur du code. Utilise read_file, grep, et list_directory pour explorer la codebase avant de proposer des changements. Lis les fichiers avant de les modifier.",
        },
        "sysadmin": {
            "description": "Mode administration système — commandes shell",
            "prompt": "L'utilisateur fait de l'administration système. Utilise bash pour les commandes, mais sois prudent avec les commandes destructives. Vérifie toujours avant d'exécuter.",
        },
        "research": {
            "description": "Mode recherche — web et documentation",
            "prompt": "L'utilisateur fait de la recherche. Utilise les fichiers du workspace, web_search et web_fetch pour trouver des informations récentes. Synthétise et cite tes sources.",
        },
    }

    for name, info in defaults.items():
        path = os.path.join(user_dir, f"{name}.md")
        if not os.path.isfile(path):
            content = f"---\nname: {name}\ndescription: {info['description']}\n---\n\n{info['prompt']}"
            with open(path, "w") as f:
                f.write(content)


def _parse_context_file(path: str) -> dict | None:
    """Parse a context .md file. Returns {name, description, prompt} or None."""
    try:
        with open(path) as f:
            content = f.read()
    except Exception:
        return None

    meta = {}
    body = content

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            try:
                import yaml
                meta = yaml.safe_load(parts[1]) or {}
            except Exception:
                for line in parts[1].strip().split("\n"):
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip()
            body = parts[2].strip()

    return {
        "name": meta.get("name", os.path.basename(path)[:-3]),
        "description": meta.get("description", ""),
        "prompt": body,
    }


def list_contexts() -> list[dict]:
    """List all available contexts with metadata."""
    _ensure_defaults()
    seen = set()
    result = []

    for d in _contexts_dirs():
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if f.endswith(".md") and f[:-3] not in seen:
                info = _parse_context_file(os.path.join(d, f))
                if info:
                    result.append(info)
                    seen.add(f[:-3])

    return sorted(result, key=lambda c: c["name"])


def get_context(name: str) -> dict | None:
    """Get a context by name. Returns {name, description, prompt} or None."""
    _ensure_defaults()
    for d in _contexts_dirs():
        path = os.path.join(d, f"{name}.md")
        if os.path.isfile(path):
            return _parse_context_file(path)
    return None


def get_context_prompt(name: str) -> str:
    """Get just the prompt text for a context."""
    ctx = get_context(name)
    return ctx["prompt"] if ctx else ""


def create_context(name: str, description: str, prompt: str) -> str:
    """Create a custom context in the user directory. Returns path."""
    from .config import get_ely_dir
    user_dir = os.path.join(get_ely_dir(), "contexts")
    os.makedirs(user_dir, exist_ok=True)

    content = f"---\nname: {name}\ndescription: {description}\n---\n\n{prompt}"
    path = os.path.join(user_dir, f"{name}.md")
    with open(path, "w") as f:
        f.write(content)
    return path


def _active_context_file() -> str:
    from .config import get_ely_dir
    return os.path.join(get_ely_dir(), "context.json")


def save_active_context(name: str):
    """Persist the active context name."""
    import json
    os.makedirs(os.path.dirname(_active_context_file()), exist_ok=True)
    with open(_active_context_file(), "w") as f:
        json.dump({"context": name}, f)


def load_active_context() -> str:
    """Load the persisted active context. Returns 'default' if not set."""
    import json
    path = _active_context_file()
    if os.path.isfile(path):
        try:
            with open(path) as f:
                data = json.load(f)
            return data.get("context", "default")
        except Exception:
            pass
    return "default"


def delete_context(name: str) -> bool:
    """Delete a custom context. Cannot delete built-in defaults."""
    if name in ("default", "code", "sysadmin", "research"):
        return False
    for d in _contexts_dirs():
        path = os.path.join(d, f"{name}.md")
        if os.path.isfile(path):
            os.remove(path)
            return True
    return False
