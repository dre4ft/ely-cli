use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ElyConfig {
    #[serde(default)]
    pub ely: ElySection,
    #[serde(default)]
    pub provider: ProviderConfig,
    #[serde(default)]
    pub pro_provider: ProviderConfig,
    #[serde(default)]
    pub agent: AgentSection,
    #[serde(default)]
    pub memory: MemorySection,
    #[serde(default)]
    pub tools: ToolsSection,
    #[serde(default)]
    pub mcp: McpSection,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ElySection {
    #[serde(default = "default_ely_dir")]
    pub dir: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderConfig {
    #[serde(default = "default_provider_type")]
    pub r#type: String,
    #[serde(default = "default_model")]
    pub model: String,
    #[serde(default)]
    pub url: String,
    #[serde(default)]
    pub api_key: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentSection {
    #[serde(default = "default_max_turns")]
    pub max_turns: u32,
    #[serde(default = "default_name")]
    pub name: String,
    #[serde(default = "default_language")]
    pub language: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemorySection {
    #[serde(default = "default_compaction_rounds")]
    pub compaction_rounds: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolsSection {
    #[serde(default = "default_workspace")]
    pub workspace: String,
    #[serde(default = "default_bash_sandbox")]
    pub bash_sandbox: String,
    #[serde(default)]
    pub disabled: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct McpSection {
    #[serde(default)]
    pub servers: Vec<McpServer>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct McpServer {
    pub name: String,
    #[serde(default)]
    pub command: String,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(default)]
    pub env: std::collections::HashMap<String, String>,
    #[serde(default)]
    pub url: String,
    #[serde(default = "default_transport")]
    pub transport: String,
    #[serde(default)]
    pub headers: std::collections::HashMap<String, String>,
}

impl Default for ElyConfig {
    fn default() -> Self {
        Self {
            ely: ElySection::default(),
            provider: ProviderConfig::default(),
            pro_provider: ProviderConfig::default(),
            agent: AgentSection::default(),
            memory: MemorySection::default(),
            tools: ToolsSection::default(),
            mcp: McpSection::default(),
        }
    }
}

impl Default for ElySection {
    fn default() -> Self {
        Self { dir: default_ely_dir() }
    }
}

impl Default for ProviderConfig {
    fn default() -> Self {
        Self {
            r#type: default_provider_type(),
            model: default_model(),
            url: String::new(),
            api_key: String::new(),
        }
    }
}

impl Default for AgentSection {
    fn default() -> Self {
        Self {
            max_turns: default_max_turns(),
            name: default_name(),
            language: default_language(),
        }
    }
}

impl Default for MemorySection {
    fn default() -> Self {
        Self { compaction_rounds: default_compaction_rounds() }
    }
}

impl Default for ToolsSection {
    fn default() -> Self {
        Self {
            workspace: default_workspace(),
            bash_sandbox: default_bash_sandbox(),
            disabled: String::new(),
        }
    }
}

fn default_ely_dir() -> String { "~/.ely".into() }
fn default_provider_type() -> String { "openai".into() }
fn default_model() -> String { "gpt-4o-mini".into() }
fn default_max_turns() -> u32 { 8 }
fn default_name() -> String { "Ely".into() }
fn default_language() -> String { "fr".into() }
fn default_compaction_rounds() -> u32 { 10 }
fn default_workspace() -> String { ".".into() }
fn default_bash_sandbox() -> String { "direct".into() }
fn default_transport() -> String { "stdio".into() }

/// Load config from standard paths or explicit path.
pub fn load_config(explicit_path: Option<&str>) -> ElyConfig {
    let config_path = if let Some(path) = explicit_path {
        PathBuf::from(path)
    } else {
        find_config_path().unwrap_or_else(|| PathBuf::from("ely.yaml"))
    };

    match std::fs::read_to_string(&config_path) {
        Ok(content) => serde_yaml::from_str(&content).unwrap_or_default(),
        Err(_) => ElyConfig::default(),
    }
}

fn find_config_path() -> Option<PathBuf> {
    let candidates = vec![
        PathBuf::from("ely.yaml"),
        dirs::home_dir()?.join(".ely").join("config.yaml"),
        dirs::home_dir()?.join(".ely.yaml"),
    ];
    candidates.into_iter().find(|p| p.exists())
}

pub fn get_ely_dir(config: &ElyConfig) -> PathBuf {
    let dir = config.ely.dir.replace('~', &dirs::home_dir().unwrap_or_default().to_string_lossy());
    PathBuf::from(dir)
}
