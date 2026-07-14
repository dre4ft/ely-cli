use std::collections::HashMap;
use crate::config::ProviderConfig;
use crate::providers::{self, ChatMessage, ChatResponse};
use crate::tools::ToolRegistry;

pub struct Agent {
    provider_config: ProviderConfig,
    tools: ToolRegistry,
    max_turns: u32,
}

impl Agent {
    pub fn new(provider_config: ProviderConfig, tools: ToolRegistry, max_turns: u32) -> Self {
        Self { provider_config, tools, max_turns }
    }

    pub async fn chat(
        &self,
        message: &str,
        history: &[(String, String)],
        system_prompt: &str,
        status_cb: Option<&(dyn Fn(&str, &str) + Sync)>,
    ) -> Result<ChatResult, String> {
        let mut messages = vec![
            ChatMessage {
                role: "system".into(),
                content: Some(system_prompt.into()),
                tool_calls: None,
                tool_call_id: None,
            },
        ];

        for (role, content) in history.iter().rev().take(10).rev() {
            messages.push(ChatMessage {
                role: role.clone(),
                content: Some(content.chars().take(2500).collect()),
                tool_calls: None,
                tool_call_id: None,
            });
        }

        messages.push(ChatMessage {
            role: "user".into(),
            content: Some(message.into()),
            tool_calls: None,
            tool_call_id: None,
        });

        let mut actions = Vec::new();
        let mut total_usage = providers::Usage::default();
        let mut reply = String::new();
        let mut all_reasoning = Vec::new();

        let tool_defs = self.tools.definitions().to_vec();

        for turn in 0..self.max_turns {
            if let Some(cb) = status_cb {
                cb("thinking", &format!("Réflexion... (tour {}/{})", turn + 1, self.max_turns));
            }

            let resp = providers::chat_completion(
                &self.provider_config,
                &messages,
                if tool_defs.is_empty() { None } else { Some(&tool_defs) },
            ).await.map_err(|e| {
                if turn == 0 {
                    // Try once without tools
                    format!("Erreur: {}", e)
                } else {
                    format!("Erreur LLM: {}", e)
                }
            })?;

            total_usage.prompt_tokens += resp.usage.prompt_tokens;
            total_usage.completion_tokens += resp.usage.completion_tokens;
            total_usage.total_tokens += resp.usage.total_tokens;

            if !resp.reasoning.is_empty() {
                all_reasoning.push(resp.reasoning.clone());
                if let Some(cb) = status_cb {
                    cb("reasoning", &resp.reasoning.chars().take(500).collect::<String>());
                }
            }

            match resp.tool_calls {
                Some(ref tcs) if !tcs.is_empty() => {
                    messages.push(ChatMessage {
                        role: "assistant".into(),
                        content: Some(resp.content.clone()),
                        tool_calls: Some(tcs.clone()),
                        tool_call_id: None,
                    });

                    for tc in tcs {
                        let args: HashMap<String, serde_json::Value> = serde_json::from_str(&tc.function.arguments)
                            .unwrap_or_default();

                        if let Some(cb) = status_cb {
                            cb("tool_call", &format!("{} {}", tc.function.name,
                                serde_json::to_string(&args).unwrap_or_default().chars().take(100).collect::<String>()));
                        }

                        actions.push(tc.function.name.clone());

                        let result = match self.tools.get_handler(&tc.function.name) {
                            Some(handler) => handler(&args).unwrap_or_else(|e| format!("Tool error: {}", e)),
                            None => format!("Unknown tool: {}", tc.function.name),
                        };

                        if let Some(cb) = status_cb {
                            cb("tool_result", &result.chars().take(120).collect::<String>());
                        }

                        messages.push(ChatMessage {
                            role: "tool".into(),
                            content: Some(result),
                            tool_calls: None,
                            tool_call_id: Some(tc.id.clone()),
                        });
                    }

                    if turn == self.max_turns - 3 {
                        messages.push(ChatMessage {
                            role: "user".into(),
                            content: Some("Donne ta réponse finale maintenant. N'appelle plus d'outils.".into()),
                            tool_calls: None,
                            tool_call_id: None,
                        });
                    }
                }
                _ => {
                    reply = resp.content;
                    if let Some(cb) = status_cb {
                        cb("reply", &reply);
                    }
                    break;
                }
            }
        }

        if reply.is_empty() {
            let resp = providers::chat_completion(&self.provider_config, &messages, None).await
                .unwrap_or(ChatResponse { content: "Je n'ai pas pu générer de réponse.".into(), reasoning: String::new(), tool_calls: None, usage: Default::default() });
            reply = resp.content;
            total_usage.prompt_tokens += resp.usage.prompt_tokens;
            total_usage.completion_tokens += resp.usage.completion_tokens;
            total_usage.total_tokens += resp.usage.total_tokens;
        }

        Ok(ChatResult {
            reply,
            reasoning: all_reasoning.join("\n"),
            actions,
            tokens: total_usage,
            model: self.provider_config.model.clone(),
        })
    }
}

#[derive(Debug, Clone)]
pub struct ChatResult {
    pub reply: String,
    pub reasoning: String,
    pub actions: Vec<String>,
    pub tokens: providers::Usage,
    pub model: String,
}
