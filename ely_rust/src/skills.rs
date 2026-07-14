use std::path::PathBuf;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Skill {
    pub name: String,
    pub description: String,
    pub instructions: String,
    pub path: PathBuf,
    pub tools_dir: Option<PathBuf>,
    pub references_dir: Option<PathBuf>,
    pub assets_dir: Option<PathBuf>,
}

impl Skill {
    pub fn tools(&self) -> Vec<String> {
        if let Some(ref dir) = self.tools_dir {
            if let Ok(entries) = std::fs::read_dir(dir) {
                return entries.flatten()
                    .filter_map(|e| {
                        let name = e.file_name().to_string_lossy().into_owned();
                        if name.ends_with(".py") { Some(name) } else { None }
                    })
                    .collect();
            }
        }
        Vec::new()
    }

    pub fn references(&self) -> Vec<String> {
        list_dir_files(&self.references_dir)
    }

    pub fn assets(&self) -> Vec<String> {
        list_dir_files(&self.assets_dir)
    }
}

fn list_dir_files(dir: &Option<PathBuf>) -> Vec<String> {
    if let Some(ref d) = dir {
        if let Ok(entries) = std::fs::read_dir(d) {
            return entries.flatten()
                .filter_map(|e| Some(e.file_name().to_string_lossy().into_owned()))
                .collect();
        }
    }
    Vec::new()
}

fn skill_dirs(ely_dir: &PathBuf) -> Vec<PathBuf> {
    let mut dirs = Vec::new();
    if let Ok(cwd) = std::env::current_dir() {
        let project = cwd.join("skills");
        if project.is_dir() { dirs.push(project); }
    }
    let user = ely_dir.join("skills");
    if user.is_dir() { dirs.push(user); }
    dirs
}

fn parse_frontmatter(content: &str) -> (HashMap<String, String>, String) {
    if content.starts_with("---") {
        let parts: Vec<&str> = content.splitn(3, "---").collect();
        if parts.len() >= 3 {
            let meta: HashMap<String, String> = serde_yaml::from_str(parts[1]).unwrap_or_default();
            return (meta, parts[2].trim().to_string());
        }
    }
    (HashMap::new(), content.to_string())
}

pub fn load_skill(ely_dir: &PathBuf, name: &str) -> Option<Skill> {
    for d in skill_dirs(ely_dir) {
        let dir = d.join(name);
        let md = dir.join("SKILL.md");
        if md.is_file() {
            let content = std::fs::read_to_string(&md).ok()?;
            let (meta, instructions) = parse_frontmatter(&content);
            let tools = dir.join("tools");
            let refs = dir.join("references");
            let assets = dir.join("assets");
            return Some(Skill {
                name: meta.get("name").cloned().unwrap_or_else(|| name.into()),
                description: meta.get("description").cloned().unwrap_or_default(),
                instructions,
                path: dir,
                tools_dir: if tools.is_dir() { Some(tools) } else { None },
                references_dir: if refs.is_dir() { Some(refs) } else { None },
                assets_dir: if assets.is_dir() { Some(assets) } else { None },
            });
        }
    }
    None
}

pub fn list_skills(ely_dir: &PathBuf) -> Vec<String> {
    let mut names = std::collections::HashSet::new();
    for d in skill_dirs(ely_dir) {
        if let Ok(entries) = std::fs::read_dir(&d) {
            for e in entries.flatten() {
                let name = e.file_name().to_string_lossy().into_owned();
                if e.path().join("SKILL.md").exists() {
                    names.insert(name);
                }
            }
        }
    }
    let mut sorted: Vec<String> = names.into_iter().collect();
    sorted.sort();
    sorted
}

pub fn read_skill_reference(ely_dir: &PathBuf, skill_name: &str, ref_name: &str) -> Option<String> {
    let skill = load_skill(ely_dir, skill_name)?;
    let refs_dir = skill.references_dir?;
    let path = refs_dir.join(ref_name);
    if path.starts_with(&refs_dir) && path.is_file() {
        std::fs::read_to_string(&path).ok()
    } else {
        None
    }
}

// Skill activation with persistence
use std::sync::Mutex;
use std::collections::HashMap;
use once_cell::sync::Lazy;

static ACTIVE_SKILLS: Lazy<Mutex<Vec<String>>> = Lazy::new(|| Mutex::new(vec!["ely".into()]));

pub fn get_active_skills() -> Vec<String> {
    ACTIVE_SKILLS.lock().unwrap().clone()
}

pub fn activate_skill(ely_dir: &PathBuf, name: &str) -> bool {
    if !list_skills(ely_dir).contains(&name.to_string()) { return false; }
    let mut skills = ACTIVE_SKILLS.lock().unwrap();
    skills.retain(|s| s == "ely");
    if name != "ely" { skills.push(name.into()); }
    save_active_skills(ely_dir);
    true
}

pub fn deactivate_skill(ely_dir: &PathBuf, name: &str) -> bool {
    if name == "ely" { return false; }
    ACTIVE_SKILLS.lock().unwrap().retain(|s| s != name);
    save_active_skills(ely_dir);
    true
}

fn save_active_skills(ely_dir: &PathBuf) {
    let skills = ACTIVE_SKILLS.lock().unwrap();
    if let Ok(json) = serde_json::to_string(&*skills) {
        std::fs::write(ely_dir.join("active_skills.json"), json).ok();
    }
}

pub fn load_active_skills(ely_dir: &PathBuf) {
    if let Ok(content) = std::fs::read_to_string(ely_dir.join("active_skills.json")) {
        if let Ok(saved) = serde_json::from_str::<Vec<String>>(&content) {
            let mut skills = ACTIVE_SKILLS.lock().unwrap();
            *skills = saved;
            if !skills.contains(&"ely".into()) { skills.push("ely".into()); }
        }
    }
}

pub fn build_skills_prompt(ely_dir: &PathBuf) -> String {
    let active = get_active_skills();
    let mut lines = Vec::new();

    let experts: Vec<_> = active.iter().filter(|n| *n != "ely").collect();
    if let Some(name) = experts.first() {
        if let Some(skill) = load_skill(ely_dir, name) {
            let instructions = skill.instructions.clone();
            let tools = skill.tools();
            let refs = skill.references();
            lines.push(format!("\n**Mode Expert — Compétence active : `{}`**", name));
            if !skill.description.is_empty() {
                lines.push(format!(" — {}", skill.description));
            }
            lines.push(instructions);
            if !tools.is_empty() {
                lines.push(format!("\n**Outils spécialisés :** {}", tools.join(", ")));
            }
            if !refs.is_empty() {
                lines.push(format!("\n**Références disponibles :** {}", refs.join(", ")));
                lines.push("Utilise skill_reference_list pour les lister, skill_reference_get pour lire.".into());
            }
        }
    }

    if let Some(base) = load_skill(ely_dir, "ely") {
        if !experts.is_empty() {
            lines.push("\n---\n**Compétence de base :**".into());
        }
        lines.push(base.instructions);
    }

    lines.join("\n")
}

pub fn build_skills_status_line() -> String {
    let active = get_active_skills();
    active.iter().find(|n| *n != "ely")
        .map(|n| format!("🧠 {}", n))
        .unwrap_or_default()
}
