mod cli;
mod config;
mod providers;
mod prompts;
mod tools;
mod agent;
mod skills;
mod contexts;
mod memory;
mod mcp;
mod subagent;
mod guard;

#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().collect();
    let mut config_path = None;
    let mut context = String::new();
    let mut slot = "provider".to_string();
    let mut query = String::new();

    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--config" => { i += 1; if i < args.len() { config_path = Some(args[i].as_str()); } }
            "--context" => { i += 1; if i < args.len() { context = args[i].clone(); } }
            "--pro" => { slot = "pro_provider".into(); }
            "--help" | "-h" => { print_usage(); return; }
            _ => {
                if !args[i].starts_with("--") {
                    query.push_str(&args[i]);
                    query.push(' ');
                }
            }
        }
        i += 1;
    }

    let query = query.trim().to_string();

    if !query.is_empty() {
        // Single-shot mode
        let config = config::load_config(config_path);
        let provider = cli::get_provider(&config, &slot);
        let workspace = cli::resolve_workspace(&config);
        let sandbox = config.tools.bash_sandbox == "docker";
        let ely_dir = config::get_ely_dir(&config);
        let tools = tools::ToolRegistry::new(workspace.clone(), sandbox);
        let agent = agent::Agent::new(provider, tools, config.agent.max_turns);
        let system_prompt = cli::build_system_prompt(&ely_dir, &context, &workspace, sandbox);

        match agent.chat(&query, &[], &system_prompt, None).await {
            Ok(result) => { println!("{}", result.reply); }
            Err(e) => { eprintln!("Error: {}", e); }
        }
    } else {
        // REPL mode
        cli::run_repl(config_path, &context, &slot).await;
    }
}

fn print_usage() {
    println!(r#"Ely — CLI AI Agent

Usage:
  ely                              REPL mode
  ely "question"                   Single-shot
  ely --config <path>              Custom config
  ely --context <name>             Set context
  ely --pro                        Use pro provider
  ely --help                       This help
"#);
}
