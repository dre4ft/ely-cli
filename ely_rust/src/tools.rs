use std::collections::HashMap;
use std::path::PathBuf;
use crate::providers::ToolDefinition;

pub type ToolHandler = Box<dyn Fn(&HashMap<String, serde_json::Value>) -> Result<String, String> + Send + Sync>;

pub struct ToolRegistry {
    definitions: Vec<ToolDefinition>,
    handlers: HashMap<String, ToolHandler>,
}

impl ToolRegistry {
    pub fn new(workspace: PathBuf, sandbox: bool) -> Self {
        let mut registry = Self {
            definitions: Vec::new(),
            handlers: HashMap::new(),
        };
        registry.register_builtins(workspace, sandbox);
        registry
    }

    pub fn definitions(&self) -> &[ToolDefinition] {
        &self.definitions
    }

    pub fn get_handler(&self, name: &str) -> Option<&ToolHandler> {
        self.handlers.get(name)
    }

    fn register(&mut self, name: &str, description: &str, parameters: serde_json::Value, handler: ToolHandler) {
        self.definitions.push(ToolDefinition {
            def_type: "function".into(),
            function: crate::providers::FunctionDef {
                name: name.into(),
                description: description.into(),
                parameters,
            },
        });
        self.handlers.insert(name.into(), handler);
    }

    fn register_builtins(&mut self, workspace: PathBuf, sandbox: bool) {
        let ws = workspace.clone();
        self.register("bash", "Execute a shell command in the workspace directory.",
            serde_json::json!({"type":"object","properties":{"command":{"type":"string","description":"Shell command"}},"required":["command"]}),
            Box::new(move |params| {
                let cmd = get_str(params, "command")?;
                run_command(&ws, &cmd, sandbox)
            }),
        );

        let ws = workspace.clone();
        self.register("read_file", "Read a file within the workspace.",
            serde_json::json!({"type":"object","properties":{"file_path":{"type":"string","description":"Path relative to workspace"},"limit":{"type":"integer","description":"Max lines (default 200)"}},"required":["file_path"]}),
            Box::new(move |params| {
                let path = get_str(params, "file_path")?;
                let limit = params.get("limit").and_then(|v| v.as_u64()).unwrap_or(200) as usize;
                let full = resolve_path(&ws, &path)?;
                let content = std::fs::read_to_string(&full).map_err(|e| format!("Error: {e}"))?;
                let lines: Vec<&str> = content.lines().take(limit).collect();
                Ok(format!("{} ({}/{})\n```\n{}\n```", path, lines.len(), content.lines().count(), lines.join("\n")))
            }),
        );

        let ws = workspace.clone();
        self.register("write_file", "Write or overwrite a file in the workspace.",
            serde_json::json!({"type":"object","properties":{"file_path":{"type":"string","description":"Path relative to workspace"},"content":{"type":"string","description":"File content"}},"required":["file_path","content"]}),
            Box::new(move |params| {
                let path = get_str(params, "file_path")?;
                let content = get_str(params, "content")?;
                let full = resolve_path(&ws, &path)?;
                if let Some(parent) = full.parent() { std::fs::create_dir_all(parent).ok(); }
                std::fs::write(&full, content).map_err(|e| format!("Error: {e}"))?;
                Ok(format!("Written to {}", path))
            }),
        );

        let ws = workspace.clone();
        self.register("list_directory", "List files and directories within the workspace.",
            serde_json::json!({"type":"object","properties":{"path":{"type":"string","description":"Subdirectory (default: root)"}}}),
            Box::new(move |params| {
                let subpath = params.get("path").and_then(|v| v.as_str()).unwrap_or(".");
                let full = resolve_path(&ws, &subpath)?;
                let entries = std::fs::read_dir(&full).map_err(|e| format!("Error: {e}"))?;
                let mut dirs = Vec::new(); let mut files = Vec::new();
                for e in entries.flatten() {
                    let name = e.file_name().to_string_lossy().into_owned();
                    if e.file_type().map(|t| t.is_dir()).unwrap_or(false) { dirs.push(format!("{}/", name)); }
                    else { let size = e.metadata().map(|m| m.len()).unwrap_or(0); files.push(format!("{} ({})", name, fmt_size(size))); }
                }
                dirs.sort(); files.sort();
                Ok(format!("📁 {}\n[Dirs]\n  {}\n[Files]\n  {}", subpath, dirs.join("\n  "), files.join("\n  ")))
            }),
        );

        let ws = workspace.clone();
        self.register("grep", "Search for a regex pattern in workspace files.",
            serde_json::json!({"type":"object","properties":{"pattern":{"type":"string","description":"Regex pattern (case-insensitive)"},"path":{"type":"string","description":"Subdirectory (default: root)"}},"required":["pattern"]}),
            Box::new(move |params| {
                let pattern = get_str(params, "pattern")?;
                let subpath = params.get("path").and_then(|v| v.as_str()).unwrap_or(".");
                let re = regex::RegexBuilder::new(&pattern).case_insensitive(true).build()
                    .or_else(|_| regex::RegexBuilder::new(&regex::escape(&pattern)).case_insensitive(true).build())
                    .map_err(|e| format!("Regex error: {e}"))?;
                let full = resolve_path(&ws, subpath)?;
                let mut results = Vec::new();
                let skip = ["node_modules","__pycache__",".git","venv",".venv","dist","build",".ely"];
                if full.is_file() { search_file(&full, &re, &mut results); }
                else if full.is_dir() {
                    for entry in walkdir::WalkDir::new(&full).max_depth(50).into_iter().flatten() {
                        if entry.file_type().is_file() && !skip.iter().any(|s| entry.path().to_string_lossy().contains(s)) {
                            search_file(entry.path(), &re, &mut results);
                            if results.len() >= 15 { break; }
                        }
                    }
                }
                Ok(if results.is_empty() { format!("No matches for '{}'", pattern) } else { results.join("\n") })
            }),
        );
    }
}

fn get_str(params: &HashMap<String, serde_json::Value>, key: &str) -> Result<String, String> {
    params.get(key)
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| format!("Missing parameter: {}", key))
}

fn resolve_path(workspace: &PathBuf, file_path: &str) -> Result<PathBuf, String> {
    let clean = file_path.trim_start_matches('/').trim_start_matches('\\');
    let normalized = clean.replace('\\', "/");
    let mut parts = Vec::new();
    for p in normalized.split('/') {
        if p.is_empty() || p == "." { continue; }
        if p == ".." {
            if parts.is_empty() { return Err(format!("Path escapes workspace: {}", file_path)); }
            parts.pop();
        } else {
            parts.push(p);
        }
    }
    let resolved = workspace.join(parts.iter().collect::<PathBuf>());
    let canonical = resolved.canonicalize().unwrap_or(resolved);
    if !canonical.starts_with(workspace) && canonical != *workspace {
        return Err(format!("Path escapes workspace: {}", file_path));
    }
    Ok(canonical)
}

fn run_command(workspace: &PathBuf, cmd: &str, sandbox: bool) -> Result<String, String> {
    if sandbox {
        run_sandboxed(workspace, cmd)
    } else {
        run_cmd(workspace, cmd, 30)
    }
}

pub fn run_direct(workspace: &PathBuf, cmd: &str) -> Result<String, String> {
    run_cmd(workspace, cmd, 30)
}

fn run_cmd(workspace: &PathBuf, cmd: &str, _timeout: u64) -> Result<String, String> {
    let output = std::process::Command::new("sh")
        .arg("-c").arg(cmd)
        .current_dir(workspace)
        .output()
        .map_err(|e| format!("Error: {e}"))?;
    let out = String::from_utf8_lossy(&output.stdout).to_string();
    let err = String::from_utf8_lossy(&output.stderr).to_string();
    let mut result = out;
    if !err.is_empty() { result.push_str(&format!("\n[stderr]\n{}", err)); }
    if result.trim().is_empty() { result = format!("(exit code {})", output.status.code().unwrap_or(-1)); }
    Ok(result.chars().take(3000).collect())
}

fn run_sandboxed(workspace: &PathBuf, cmd: &str) -> Result<String, String> {
    let ws_path = workspace.to_string_lossy();
    // Check/create Docker container
    let check = std::process::Command::new("docker")
        .args(["inspect", "ely-sandbox"])
        .output();
    if check.map(|o| !o.status.success()).unwrap_or(true) {
        std::process::Command::new("docker")
            .args(["run", "-d", "--name", "ely-sandbox", "--rm", "-v", &format!("{}:/workspace", ws_path), "-w", "/workspace", "--network", "none", "alpine:latest", "tail", "-f", "/dev/null"])
            .output().map_err(|e| format!("Docker error: {e}"))?;
    }
    let output = std::process::Command::new("docker")
        .args(["exec", "-i", "ely-sandbox", "sh", "-c", cmd])
        .output()
        .map_err(|e| format!("Docker exec error: {e}"))?;
    let out = String::from_utf8_lossy(&output.stdout).to_string();
    Ok(out.chars().take(3000).collect())
}

fn search_file(path: &std::path::Path, re: &regex::Regex, results: &mut Vec<String>) {
    if let Ok(content) = std::fs::read_to_string(path) {
        for (i, line) in content.lines().enumerate() {
            if re.is_match(line) {
                results.push(format!("{}:{}: {}", path.file_name().unwrap_or_default().to_string_lossy(), i + 1, &line[..line.len().min(200)]));
                if results.len() >= 15 { break; }
            }
        }
    }
}

fn fmt_size(size: u64) -> String {
    if size < 1024 { format!("{}B", size) }
    else if size < 1024 * 1024 { format!("{:.0}KB", size as f64 / 1024.0) }
    else { format!("{:.0}MB", size as f64 / (1024.0 * 1024.0)) }
}
