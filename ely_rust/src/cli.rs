use std::path::{Path, PathBuf};
use rustyline::{DefaultEditor, error::ReadlineError};
use colored::Colorize;
use crate::agent::Agent;
use crate::config::{ElyConfig, ProviderConfig, load_config};
use crate::tools::ToolRegistry;
use crate::contexts;
use crate::skills;
use crate::memory;

pub fn resolve_workspace(config: &ElyConfig) -> PathBuf {
    if config.tools.workspace.is_empty() || config.tools.workspace == "." {
        std::env::current_dir().unwrap_or_default()
    } else {
        PathBuf::from(shellexpand::full(&config.tools.workspace).unwrap_or_default().into_owned())
    }
}

pub fn get_provider(config: &ElyConfig, slot: &str) -> ProviderConfig {
    let cfg = if slot == "pro_provider" { &config.pro_provider } else { &config.provider };
    let mut p = cfg.clone();
    if p.url.is_empty() {
        p.url = match p.r#type.as_str() {
            "ollama" => "http://localhost:11434/v1/chat/completions".into(),
            "lmstudio" => "http://localhost:1234/v1/chat/completions".into(),
            _ => "https://api.openai.com/v1/chat/completions".into(),
        };
    }
    if p.api_key.is_empty() {
        let is_local = p.url.contains("localhost") || p.url.contains("127.0.0.1");
        if is_local { p.api_key = "not-needed".into(); }
    }
    p
}

pub fn build_system_prompt(ely_dir: &PathBuf, context: &str, workspace: &PathBuf, sandbox: bool) -> String {
    let mut prompt = crate::prompts::BASE_PROMPT.replace("{name}", "Ely");
    prompt.push_str(&format!("\n\n**Environnement :** Workspace: {} | Bash: {}",
        workspace.display(), if sandbox { "docker" } else { "direct" }));
    prompt.push_str(&format!("\n\n**Contexte :** {}", contexts::get_context_prompt(ely_dir, context)));
    let sp = skills::build_skills_prompt(ely_dir);
    if !sp.is_empty() { prompt.push_str(&sp); }
    let mp = memory::build_memory_prompt(ely_dir, "default");
    if !mp.is_empty() { prompt.push_str(&mp); }
    prompt
}

fn status_line(_config: &ElyConfig, provider: &ProviderConfig, context: &str, ws: &PathBuf, sandbox: bool, total_tokens: u64) -> String {
    let ws_name = ws.file_name().unwrap_or_default().to_string_lossy();
    let sk = skills::build_skills_status_line();
    let sand = if sandbox { "sandbox" } else { "direct" };
    let mut parts = vec![
        "Ely".bold().to_string(),
        provider.model.cyan().to_string(),
        format!("ctx={}", context.green()),
        format!("bash={}", sand.yellow()),
        format!("📁 {}", ws_name.blue()),
    ];
    if !sk.is_empty() { parts.push(sk); }
    parts.push(format!("🪙 {}", total_tokens));
    parts.join("  ")
}

pub async fn run_repl(config_path: Option<&str>, context: &str, slot: &str) {
    let config = load_config(config_path);
    let ely_dir = crate::config::get_ely_dir(&config);

    skills::load_active_skills(&ely_dir);
    let mut current_context = if context.is_empty() {
        contexts::load_active_context(&ely_dir)
    } else {
        context.to_string()
    };
    contexts::save_active_context(&ely_dir, &current_context);

    let provider = get_provider(&config, slot);
    let workspace = resolve_workspace(&config);
    let sandbox = config.tools.bash_sandbox == "docker";
    let tools = ToolRegistry::new(workspace.clone(), sandbox);
    let agent = Agent::new(provider.clone(), tools, config.agent.max_turns);

    let mut history: Vec<(String, String)> = Vec::new();
    let mut total_tokens = 0u64;

    let mut rl = DefaultEditor::new().expect("rustyline");
    let history_file = ely_dir.join("history");
    let _ = rl.load_history(history_file.as_path());

    println!("{}", status_line(&config, &provider, &current_context, &workspace, sandbox, total_tokens));
    println!("#cmd | /help | Tab | exit");

    loop {
        let prompt = "› ";
        match rl.readline(prompt) {
            Ok(line) => {
                let input = line.trim().to_string();
                if input.is_empty() { continue; }
                let _ = rl.add_history_entry(&input);
                let _ = rl.save_history(history_file.as_path());

                if input == "exit" || input == "quit" || input == "q" { break; }

                // # bash direct
                if input.starts_with('#') {
                    let cmd = &input[1..].trim();
                    if !cmd.is_empty() {
                        println!("$ {}", cmd);
                        match crate::tools::run_direct(&workspace, cmd) {
                            Ok(out) => println!("{}", out),
                            Err(e) => println!("Error: {}", e),
                        }
                    }
                    continue;
                }

                // / commands
                if input.starts_with('/') {
                    let parts: Vec<&str> = input.splitn(2, ' ').collect();
                    let cmd = parts[0].to_lowercase();
                    let args = parts.get(1).unwrap_or(&"");

                    match cmd.as_str() {
                        "/help" => {
                            println!("/explain /fix /refactor /test  — LLM commands");
                            println!("/context [list|activate|create|delete]");
                            println!("/skill [list|activate|deactivate|delete]");
                            println!("/diary [list|add|search]");
                            println!("/tokens /clear /pro /flash");
                        }
                        "/clear" => { history.clear(); total_tokens = 0; println!("✓ Purgée."); }
                        "/tokens" => { println!("🪙 Total: {}", total_tokens); }
                        "/context" => {
                            if let Some(new_ctx) = handle_context(&ely_dir, args, &current_context) {
                                current_context = new_ctx;
                            }
                        }
                        "/skill" => { handle_skill(&ely_dir, args); }
                        "/diary" => { handle_diary(&ely_dir, args); }
                        "/pro" | "/flash" => {
                            let p = get_provider(&config, if cmd == "/pro" { "pro_provider" } else { "provider" });
                            println!("✓ Provider : {}", p.model);
                        }
                        _ => { println!("Commande inconnue : {}", cmd); }
                    }
                    continue;
                }

                // Agent
                history.push(("user".into(), input.clone()));
                print!("🤔 Réflexion...\r");

                let system_prompt = build_system_prompt(&ely_dir, &current_context, &workspace, sandbox);
                match agent.chat(&input, &history, &system_prompt, None).await {
                    Ok(result) => {
                        total_tokens += result.tokens.total_tokens as u64;
                        history.push(("assistant".into(), result.reply.clone()));

                        if !result.reasoning.is_empty() {
                            let lines: Vec<&str> = result.reasoning.lines().take(3).collect();
                            println!("💭 {}", lines.join(" "));
                        }
                        println!();
                        println!("{}", result.reply);
                        println!();
                        if !result.actions.is_empty() {
                            println!("🔧 {}  🪙 {}", result.actions.join("  "), result.tokens.total_tokens);
                        }
                        println!();
                    }
                    Err(e) => {
                        println!("Error: {}", e);
                    }
                }
            }
            Err(ReadlineError::Interrupted) => { println!(); break; }
            Err(ReadlineError::Eof) => { break; }
            Err(_) => { break; }
        }
    }

    let _ = rl.save_history(history_file.as_path());
}

fn handle_context(ely_dir: &PathBuf, args: &str, current: &str) -> Option<String> {
    let parts: Vec<&str> = args.splitn(2, ' ').collect();
    match parts.get(0).unwrap_or(&"") {
        &"list" | &"" => {
            for c in contexts::list_contexts(ely_dir) {
                let m = if c.name == current { "●" } else { "○" };
                println!("  {} {} — {}", m, c.name, c.description);
            }
            None
        }
        &"activate" => {
            let name = parts.get(1).unwrap_or(&"");
            if !name.is_empty() {
                contexts::save_active_context(ely_dir, name);
                println!("✓ Contexte : {}", name);
                Some(name.to_string())
            } else { None }
        }
        name => {
            if contexts::get_context(ely_dir, name).is_some() {
                contexts::save_active_context(ely_dir, name);
                println!("✓ Contexte : {}", name);
                Some(name.to_string())
            } else { None }
        }
    }
}

fn handle_skill(ely_dir: &PathBuf, args: &str) {
    let parts: Vec<&str> = args.splitn(2, ' ').collect();
    let sub = parts.get(0).unwrap_or(&"");
    let rest = parts.get(1).unwrap_or(&"");
    match *sub {
        "list" | "" => {
            let active = skills::get_active_skills();
            for s in skills::list_skills(ely_dir) {
                let m = if active.contains(&s) { "●" } else { "○" };
                println!("  {} {}", m, s);
            }
        }
        "activate" => {
            if skills::activate_skill(ely_dir, rest) {
                println!("✓ '{}' activée", rest);
            }
        }
        "deactivate" => {
            if skills::deactivate_skill(ely_dir, rest) {
                println!("✓ '{}' désactivée", rest);
            }
        }
        "delete" => {
            if *rest == "ely" { println!("Protégé."); return; }
            let dir = ely_dir.join("skills").join(rest);
            if dir.exists() {
                std::fs::remove_dir_all(&dir).ok();
                skills::deactivate_skill(ely_dir, rest);
                println!("✓ '{}' supprimée", rest);
            }
        }
        _ => {}
    }
}

fn handle_diary(ely_dir: &PathBuf, args: &str) {
    let diary_dir = ely_dir.join("memory").join("diary");
    let _ = std::fs::create_dir_all(&diary_dir);
    let parts: Vec<&str> = args.splitn(2, ' ').collect();
    match parts.get(0).unwrap_or(&"") {
        &"list" | &"" => {
            if let Ok(entries) = std::fs::read_dir(&diary_dir) {
                let mut files: Vec<_> = entries.flatten().collect();
                files.sort_by_key(|e| e.file_name());
                for e in files.iter().rev().take(10) {
                    if let Ok(content) = std::fs::read_to_string(e.path()) {
                        let preview: String = content.chars().take(200).collect();
                        println!("  {} — {}", e.file_name().to_string_lossy().trim_end_matches(".json"), preview);
                    }
                }
            }
        }
        &"add" => {
            let content = parts.get(1).unwrap_or(&"");
            if !content.is_empty() {
                let id = chrono::Utc::now().timestamp_millis();
                let path = diary_dir.join(format!("{}.json", id));
                let entry = serde_json::json!({"id": id, "content": content, "tags": [], "timestamp": chrono::Utc::now().to_rfc3339()});
                std::fs::write(&path, serde_json::to_string_pretty(&entry).unwrap_or_default()).ok();
                println!("✓ Diary #{} saved", id);
            }
        }
        &"search" => {
            let query = parts.get(1).unwrap_or(&"").to_lowercase();
            if let Ok(entries) = std::fs::read_dir(&diary_dir) {
                for e in entries.flatten() {
                    if let Ok(content) = std::fs::read_to_string(e.path()) {
                        if content.to_lowercase().contains(&query) {
                            let preview: String = content.chars().take(300).collect();
                            println!("  {}", preview);
                        }
                    }
                }
            }
        }
        _ => {}
    }
}
