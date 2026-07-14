"""
AI Providers — OpenAI, Ollama, LM Studio.
Extracted from ai_core/providers_api/. No dependencies on Elyria.
"""

import json
from openai import OpenAI, DefaultHttpxClient


class OpenAIProvider:
    """OpenAI-compatible provider (works with OpenAI, LM Studio, DeepSeek, etc.)."""

    def __init__(self, model: str, url: str, api_key: str = ""):
        self.model = model
        self.url = url
        http_client = DefaultHttpxClient(verify=False)
        # Only use "not-needed" for local servers, pass empty otherwise
        is_local = "localhost" in url or "127.0.0.1" in url
        key = api_key if api_key else ("not-needed" if is_local else "sk-no-key")
        headers = {}
        if "litellm" in url:
            headers["x-litellm-api-key"] = api_key or "not-needed"
        self.client = OpenAI(base_url=url, api_key=key, http_client=http_client, default_headers=headers)

    def chat(self, messages: list, tools: list = None) -> dict:
        kwargs = {"model": self.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools

        try:
            resp = self.client.chat.completions.create(**kwargs)
        except Exception as e:
            return {"content": f"Error: {e}", "tool_calls": None, "usage": {}}

        choice = resp.choices[0]
        msg = choice.message

        tool_calls = None
        if msg.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]

        # Capture reasoning/thinking content (DeepSeek-R1, o1, etc.)
        reasoning = getattr(msg, "reasoning_content", "") or ""

        return {
            "content": msg.content or "",
            "reasoning": reasoning,
            "tool_calls": tool_calls,
            "usage": {
                "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
                "total_tokens": resp.usage.total_tokens if resp.usage else 0,
            },
        }

    def get_models(self) -> list:
        try:
            return [m.id for m in self.client.models.list()]
        except Exception:
            return [self.model]

    def get_config(self) -> dict:
        return {"provider": "openai", "model": self.model, "url": self.url}


class OllamaProvider:
    """Ollama local LLM provider."""

    def __init__(self, model: str, host: str = "http://localhost:11434", **_):
        self.model = model
        self.host = host
        try:
            import ollama as _ollama
            self._ollama = _ollama
            self._ollama.set_host(host)
        except ImportError:
            raise RuntimeError("ollama package not installed. Run: pip install ollama")

    def chat(self, messages: list, tools: list = None) -> dict:
        kwargs = {"model": self.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools

        try:
            resp = self._ollama.chat(**kwargs)
        except Exception as e:
            return {"content": f"Error: {e}", "tool_calls": None, "usage": {}}

        msg = resp.get("message", {})
        tool_calls = None
        if msg.get("tool_calls"):
            tool_calls = [
                {
                    "id": tc.get("id", f"call_{i}"),
                    "type": "function",
                    "function": {"name": tc["function"]["name"], "arguments": json.dumps(tc["function"]["arguments"])},
                }
                for i, tc in enumerate(msg["tool_calls"])
            ]

        return {
            "content": msg.get("content", ""),
            "reasoning": "",
            "tool_calls": tool_calls,
            "usage": {},
        }

    def get_models(self) -> list:
        try:
            return [m["name"] for m in self._ollama.list().get("models", [])]
        except Exception:
            return [self.model]

    def get_config(self) -> dict:
        return {"provider": "ollama", "model": self.model, "host": self.host}


def create_provider(config: dict):
    """Factory: create a provider from a config dict.
    config must have: type, model, url (or host), api_key
    """
    ptype = config.get("type", "openai").lower()
    model = config.get("model", "gpt-4o-mini")
    url = config.get("url", "")

    # Auto-select URL based on type if not explicitly configured
    if not url:
        if ptype == "ollama":
            url = "http://localhost:11434"
        elif ptype == "lmstudio":
            url = "http://localhost:1234/v1"
        else:
            url = "https://api.openai.com/v1"

    if ptype == "ollama":
        return OllamaProvider(model=model, host=url)
    elif ptype == "lmstudio":
        return OpenAIProvider(model=model, url=url, api_key="lm-studio")
    else:
        return OpenAIProvider(model=model, url=url, api_key=config.get("api_key", ""))
