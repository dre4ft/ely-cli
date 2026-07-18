"""File tools — read, write, edit, list, grep within workspace."""
import os
import re as _re
from ._core import action, resolve_path, workspace_dir, relative_path


@action("read_file", "Read a file within the workspace.",
         {"file_path": {"type": "string", "description": "Path relative to workspace root."},
          "limit": {"type": "integer", "description": "Max lines to read (default 200)."}},
         optional=["limit"])
def tool_read_file(file_path: str, limit: int = 200) -> str:
    try: path = resolve_path(file_path)
    except ValueError as e: return f"Error: {e}"
    if not os.path.isfile(path): return f"Error: file not found: {file_path}"
    try:
        with open(path, "r", errors="replace") as f: lines = f.readlines()
        total = len(lines)
        content = "".join(lines[:limit])
        rel = relative_path(path)
        return f"{rel} ({min(total, limit)}/{total} lines)\n```\n{content}```"[:4000]
    except Exception as e: return f"Error: {e}"


@action("write_file", "Write or overwrite a file in the workspace. Supports any file type.",
         {"file_path": {"type": "string", "description": "Path relative to workspace root (e.g. 'src/main.py')."},
          "content": {"type": "string", "description": "File content."}})
def tool_write_file(file_path: str, content: str) -> str:
    try: path = resolve_path(file_path)
    except ValueError as e: return f"Error: {e}"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f: f.write(content)
        return f"Written {len(content)} chars to {relative_path(path)}"
    except Exception as e: return f"Error: {e}"


@action("edit_file", "Edit a file — replace or delete specific lines, or insert after a line.",
         {"file_path": {"type": "string", "description": "Path relative to workspace root."},
          "action": {"type": "string", "description": "Action: replace_line, replace_range, replace_text, insert_after, delete_range."},
          "start_line": {"type": "integer", "description": "Line number (1-based)."},
          "end_line": {"type": "integer", "description": "End line number (inclusive). For replace_range and delete_range."},
          "new_content": {"type": "string", "description": "New content. For replace_*: replacement. For insert_after: lines to insert."}},
         optional=["end_line", "new_content"])
def tool_edit_file(file_path: str, action: str, start_line: int, end_line: int = 0, new_content: str = "") -> str:
    try: path = resolve_path(file_path)
    except ValueError as e: return f"Error: {e}"
    if not os.path.isfile(path): return f"Error: file not found: {file_path}"
    try:
        with open(path, "r", errors="replace") as f: lines = f.readlines()
        total = len(lines)
        if start_line < 1 or start_line > total: return f"Error: start_line {start_line} out of range (1-{total})"

        if action == "replace_text":
            old_text = new_content.split("→")[0].strip() if "→" in new_content else ""
            new_text = new_content.split("→")[1].strip() if "→" in new_content else new_content
            if not old_text: return "Error: replace_text needs 'old → new' format in new_content"
            count = 0
            for i, line in enumerate(lines):
                if old_text in line: lines[i] = line.replace(old_text, new_text); count += 1
            if count == 0: return f"Error: text '{old_text[:50]}' not found in file"
        elif action == "replace_line":
            lines[start_line - 1] = new_content + "\n"
        elif action == "replace_range":
            if end_line < start_line or end_line > total: return f"Error: invalid range {start_line}-{end_line} (file has {total} lines)"
            replacement = (new_content + "\n").splitlines(True)
            replacement = [l if l.endswith("\n") else l + "\n" for l in replacement]
            lines[start_line - 1:end_line] = replacement
        elif action == "insert_after":
            insertion = (new_content + "\n").splitlines(True)
            insertion = [l if l.endswith("\n") else l + "\n" for l in insertion]
            for i, line in enumerate(insertion): lines.insert(start_line + i, line)
        elif action == "delete_range":
            if end_line < start_line or end_line > total: return f"Error: invalid range {start_line}-{end_line} (file has {total} lines)"
            del lines[start_line - 1:end_line]
        else:
            return f"Error: unknown action '{action}'. Use: replace_line, replace_range, replace_text, insert_after, delete_range."

        with open(path, "w") as f: f.writelines(lines)
        new_total = len(lines)
        rel = relative_path(path)
        if action == "replace_text": return f"Edited {rel}: replaced {count} occurrence(s) ({total} lines)"
        return f"Edited {rel}: {action} at line {start_line}" + (f"-{end_line}" if end_line else "") + f" ({total} → {new_total} lines)"
    except Exception as e: return f"Error: {e}"


@action("list_directory", "List files and directories within the workspace.",
         {"path": {"type": "string", "description": "Directory path relative to workspace (default: root)."}},
         optional=["path"])
def tool_list_directory(path: str = ".") -> str:
    try: target = resolve_path(path) if path else workspace_dir()
    except ValueError as e: return f"Error: {e}"
    if not os.path.isdir(target): return f"Error: not a directory: {path}"
    try:
        entries = sorted(os.listdir(target))
        dirs, files = [], []
        for e in entries:
            full = os.path.join(target, e)
            if os.path.isdir(full): dirs.append(e + "/")
            else: files.append(f"{e} ({_fmt_size(os.path.getsize(full))})")
        rel = relative_path(target)
        lines = [f"📁 {rel}"]
        if dirs: lines.extend(["[Dirs]", "  " + "\n  ".join(dirs)])
        if files: lines.extend(["[Files]", "  " + "\n  ".join(files)])
        return "\n".join(lines)
    except Exception as e: return f"Error: {e}"


@action("grep", "Search for a regex pattern in workspace files (case-insensitive).",
         {"pattern": {"type": "string", "description": "Regex pattern to search for."},
          "path": {"type": "string", "description": "Subdirectory to search in (default: entire workspace)."}},
         optional=["path"])
def tool_grep(pattern: str, path: str = ".") -> str:
    try: target = resolve_path(path) if path else workspace_dir()
    except ValueError as e: return f"Error: {e}"
    try: pat = _re.compile(pattern, _re.IGNORECASE)
    except Exception: pat = _re.compile(_re.escape(pattern), _re.IGNORECASE)
    results, skip = [], {".git", "node_modules", "__pycache__", "venv", ".venv", "dist", "build", ".ely"}
    files = [target] if os.path.isfile(target) else []
    if not files and os.path.isdir(target):
        for root, dirs, filenames in os.walk(target):
            dirs[:] = [d for d in dirs if d not in skip]
            for fn in filenames:
                if fn.startswith("."): continue
                fp = os.path.join(root, fn)
                if os.path.getsize(fp) > 1_000_000: continue
                files.append(fp)
    for fp in files:
        try:
            with open(fp, "r", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    if pat.search(line):
                        results.append(f"{relative_path(fp)}:{i}: {line.strip()[:200]}")
                        if len(results) >= 15: break
            if len(results) >= 15: break
        except Exception: pass
    return "\n".join(results[:15]) if results else f"No matches for '{pattern}'"


def _fmt_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024: return f"{size:.0f}{unit}"
        size /= 1024
    return f"{size:.0f}TB"
