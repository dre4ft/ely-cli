use std::path::PathBuf;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Context {
    pub name: String,
    pub description: String,
    pub prompt: String,
}

fn context_dirs(ely_dir: &PathBuf) -> Vec<PathBuf> {
    let mut dirs = vec![std::env::current_dir().unwrap_or_default().join(".ely").join("contexts")];
    dirs.push(ely_dir.join("contexts"));
    dirs.into_iter().filter(|d| d.is_dir()).collect()
}

pub fn ensure_defaults(ely_dir: &PathBuf) {
    let user_dir = ely_dir.join("contexts");
    std::fs::create_dir_all(&user_dir).ok();
    let defaults = [
        ("default", "Mode général — terminal et tâches courantes", "Tu es dans un terminal. L'utilisateur travaille dans le répertoire courant."),
        ("code", "Mode développement — exploration et écriture de code", "L'utilisateur travaille sur du code. Utilise read_file, grep, et list_directory pour explorer la codebase."),
        ("sysadmin", "Mode administration système — commandes shell", "L'utilisateur fait de l'administration système. Utilise bash pour les commandes, sois prudent."),
        ("research", "Mode recherche — web et documentation", "L'utilisateur fait de la recherche. Utilise web_search et web_fetch."),
    ];
    for (name, desc, prompt) in &defaults {
        let path = user_dir.join(format!("{}.md", name));
        if !path.exists() {
            let content = format!("---\nname: {}\ndescription: {}\n---\n\n{}", name, desc, prompt);
            std::fs::write(&path, content).ok();
        }
    }
}

pub fn list_contexts(ely_dir: &PathBuf) -> Vec<Context> {
    ensure_defaults(ely_dir);
    let mut seen = std::collections::HashSet::new();
    let mut result = Vec::new();
    for d in context_dirs(ely_dir) {
        if let Ok(entries) = std::fs::read_dir(&d) {
            for e in entries.flatten() {
                let name = e.file_name().to_string_lossy().into_owned();
                if name.ends_with(".md") {
                    let context_name = name.trim_end_matches(".md").to_string();
                    if seen.insert(context_name.clone()) {
                        if let Some(ctx) = parse_context(&e.path()) {
                            result.push(ctx);
                        }
                    }
                }
            }
        }
    }
    result.sort_by(|a, b| a.name.cmp(&b.name));
    result
}

pub fn get_context(ely_dir: &PathBuf, name: &str) -> Option<Context> {
    ensure_defaults(ely_dir);
    for d in context_dirs(ely_dir) {
        let path = d.join(format!("{}.md", name));
        if path.exists() {
            return parse_context(&path);
        }
    }
    None
}

pub fn get_context_prompt(ely_dir: &PathBuf, name: &str) -> String {
    get_context(ely_dir, name).map(|c| c.prompt).unwrap_or_default()
}

pub fn create_context(ely_dir: &PathBuf, name: &str, description: &str, prompt: &str) -> PathBuf {
    let dir = ely_dir.join("contexts");
    std::fs::create_dir_all(&dir).ok();
    let content = format!("---\nname: {}\ndescription: {}\n---\n\n{}", name, description, prompt);
    let path = dir.join(format!("{}.md", name));
    std::fs::write(&path, content).ok();
    path
}

pub fn delete_context(ely_dir: &PathBuf, name: &str) -> bool {
    if ["default", "code", "sysadmin", "research"].contains(&name) { return false; }
    for d in context_dirs(ely_dir) {
        let path = d.join(format!("{}.md", name));
        if path.exists() { return std::fs::remove_file(&path).is_ok(); }
    }
    false
}

pub fn save_active_context(ely_dir: &PathBuf, name: &str) {
    let path = ely_dir.join("context.json");
    if let Ok(json) = serde_json::to_string(&serde_json::json!({"context": name})) {
        std::fs::write(&path, json).ok();
    }
}

pub fn load_active_context(ely_dir: &PathBuf) -> String {
    let path = ely_dir.join("context.json");
    std::fs::read_to_string(&path).ok()
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        .and_then(|v| v["context"].as_str().map(|s| s.to_string()))
        .unwrap_or_else(|| "default".into())
}

fn parse_context(path: &PathBuf) -> Option<Context> {
    let content = std::fs::read_to_string(path).ok()?;
    let (meta, body) = if content.starts_with("---") {
        let parts: Vec<&str> = content.splitn(3, "---").collect();
        if parts.len() >= 3 {
            let meta: HashMap<String, String> = serde_yaml::from_str(parts[1]).unwrap_or_default();
            (meta, parts[2].trim().to_string())
        } else {
            (HashMap::new(), content)
        }
    } else {
        (HashMap::new(), content)
    };
    Some(Context {
        name: meta.get("name").cloned().unwrap_or_else(|| path.file_stem().unwrap_or_default().to_string_lossy().into()),
        description: meta.get("description").cloned().unwrap_or_default(),
        prompt: body,
    })
}

use std::collections::HashMap;
