use serde::{Deserialize, Serialize};
use crate::config::ProviderConfig;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatMessage {
    pub role: String,
    pub content: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_calls: Option<Vec<ToolCall>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCall {
    pub id: String,
    #[serde(rename = "type")]
    pub call_type: String,
    pub function: FunctionCall,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FunctionCall {
    pub name: String,
    pub arguments: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolDefinition {
    #[serde(rename = "type")]
    pub def_type: String,
    pub function: FunctionDef,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FunctionDef {
    pub name: String,
    pub description: String,
    pub parameters: serde_json::Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatResponse {
    pub content: String,
    pub reasoning: String,
    pub tool_calls: Option<Vec<ToolCall>>,
    pub usage: Usage,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Usage {
    pub prompt_tokens: u32,
    pub completion_tokens: u32,
    pub total_tokens: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct OpenAIRequest {
    model: String,
    messages: Vec<ChatMessage>,
    #[serde(skip_serializing_if = "Option::is_none")]
    tools: Option<Vec<ToolDefinition>>,
}

#[derive(Debug, Clone, Deserialize)]
struct OpenAIResponse {
    choices: Vec<OpenAIChoice>,
    usage: Option<OpenAIUsage>,
}

#[derive(Debug, Clone, Deserialize)]
struct OpenAIChoice {
    message: OpenAIChoiceMessage,
}

#[derive(Debug, Clone, Deserialize)]
struct OpenAIChoiceMessage {
    content: Option<String>,
    tool_calls: Option<Vec<OpenAIToolCall>>,
    #[serde(default)]
    reasoning_content: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
struct OpenAIToolCall {
    id: String,
    #[serde(rename = "type")]
    call_type: String,
    function: OpenAIFunctionCall,
}

#[derive(Debug, Clone, Deserialize)]
struct OpenAIFunctionCall {
    name: String,
    arguments: String,
}

#[derive(Debug, Clone, Deserialize)]
struct OpenAIUsage {
    prompt_tokens: u32,
    completion_tokens: u32,
    total_tokens: u32,
}

pub async fn chat_completion(
    config: &ProviderConfig,
    messages: &[ChatMessage],
    tools: Option<&[ToolDefinition]>,
) -> Result<ChatResponse, String> {
    let client = reqwest::Client::builder()
        .danger_accept_invalid_certs(true)
        .build()
        .map_err(|e| format!("HTTP client error: {e}"))?;

    let url = resolve_url(config);
    let api_key = resolve_api_key(config);

    let request = OpenAIRequest {
        model: config.model.clone(),
        messages: messages.to_vec(),
        tools: tools.map(|t| t.to_vec()),
    };

    let mut req = client.post(&url).json(&request);

    if !api_key.is_empty() {
        req = req.header("Authorization", format!("Bearer {}", api_key));
    }
    if url.contains("litellm") && !api_key.is_empty() {
        req = req.header("x-litellm-api-key", &api_key);
    }

    let resp = req.send().await.map_err(|e| format!("HTTP error: {e}"))?;
    let status = resp.status();

    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(format!("Error code: {} — {}", status.as_u16(), body));
    }

    let data: OpenAIResponse = resp.json().await.map_err(|e| format!("Parse error: {e}"))?;
    let choice = data.choices.into_iter().next().ok_or("No choices")?;
    let msg = choice.message;

    let tool_calls = msg.tool_calls.map(|tc| {
        tc.into_iter().map(|t| ToolCall {
            id: t.id,
            call_type: t.call_type,
            function: FunctionCall {
                name: t.function.name,
                arguments: t.function.arguments,
            },
        }).collect()
    });

    Ok(ChatResponse {
        content: msg.content.unwrap_or_default(),
        reasoning: msg.reasoning_content.unwrap_or_default(),
        tool_calls,
        usage: data.usage.map(|u| Usage {
            prompt_tokens: u.prompt_tokens,
            completion_tokens: u.completion_tokens,
            total_tokens: u.total_tokens,
        }).unwrap_or_default(),
    })
}

fn resolve_url(config: &ProviderConfig) -> String {
    if !config.url.is_empty() {
        return config.url.clone();
    }
    match config.r#type.as_str() {
        "ollama" => "http://localhost:11434/v1/chat/completions".into(),
        "lmstudio" => "http://localhost:1234/v1/chat/completions".into(),
        _ => "https://api.openai.com/v1/chat/completions".into(),
    }
}

fn resolve_api_key(config: &ProviderConfig) -> String {
    if !config.api_key.is_empty() {
        return config.api_key.clone();
    }
    let url = resolve_url(config);
    let is_local = url.contains("localhost") || url.contains("127.0.0.1");
    if is_local { "not-needed".into() } else { String::new() }
}
