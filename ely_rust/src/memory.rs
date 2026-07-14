use std::path::PathBuf;
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
struct MemoryData {
    situation: String,
    round_count: u32,
    created_at: f64,
}

impl Default for MemoryData {
    fn default() -> Self {
        Self { situation: String::new(), round_count: 0, created_at: 0.0 }
    }
}

fn memory_file(ely_dir: &PathBuf, user_id: &str) -> PathBuf {
    let dir = ely_dir.join("memory");
    std::fs::create_dir_all(&dir).ok();
    dir.join(format!("{}.json", user_id))
}

fn load(ely_dir: &PathBuf, user_id: &str) -> MemoryData {
    let path = memory_file(ely_dir, user_id);
    std::fs::read_to_string(&path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn save(ely_dir: &PathBuf, user_id: &str, data: &MemoryData) {
    if let Ok(json) = serde_json::to_string_pretty(data) {
        std::fs::write(memory_file(ely_dir, user_id), json).ok();
    }
}

pub fn get_situation(ely_dir: &PathBuf, user_id: &str) -> String {
    load(ely_dir, user_id).situation
}

pub fn update_situation(ely_dir: &PathBuf, user_id: &str, situation: &str) {
    let mut data = load(ely_dir, user_id);
    data.situation = situation.chars().take(1000).collect();
    save(ely_dir, user_id, &data);
}

pub fn get_round_count(ely_dir: &PathBuf, user_id: &str) -> u32 {
    load(ely_dir, user_id).round_count
}

pub fn increment_round(ely_dir: &PathBuf, user_id: &str) {
    let mut data = load(ely_dir, user_id);
    data.round_count += 1;
    save(ely_dir, user_id, &data);
}

pub fn build_memory_prompt(ely_dir: &PathBuf, user_id: &str) -> String {
    let situation = get_situation(ely_dir, user_id);
    if situation.is_empty() {
        return String::new();
    }
    format!("\n**Mémoire (contexte de la session) :**\n{}", situation)
}

pub fn maybe_compact(ely_dir: &PathBuf, user_id: &str, interval: u32) -> bool {
    let count = get_round_count(ely_dir, user_id);
    count > 0 && count % interval == 0
}
