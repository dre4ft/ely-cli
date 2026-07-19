"""Ely WebApp — FastAPI server with SSE streaming on port 10080."""

import json, asyncio, time
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Ely WebApp")

# ── State ──
conversation = []
total_tokens = {"prompt": 0, "completion": 0, "total": 0}
current_context = "default"
current_slot = "provider"


@app.get("/", response_class=HTMLResponse)
async def index():
    return (Path(__file__).parent / "index.html").read_text()


@app.post("/chat")
async def chat(request: Request):
    """Stream agent response via SSE."""
    global conversation, total_tokens

    body = await request.json()
    msg = body.get("message", "").strip()
    if not msg:
        return StreamingResponse(_sse_error("Empty message"), media_type="text/event-stream")

    # Handle prefix commands
    if msg.startswith("/"):
        return StreamingResponse(_handle_slash(msg), media_type="text/event-stream")
    if msg.startswith("#"):
        return StreamingResponse(_handle_bash(msg[1:]), media_type="text/event-stream")
    if msg.startswith("?"):
        return StreamingResponse(_handle_quick(msg[1:]), media_type="text/event-stream")

    # Agent chat with streaming
    return StreamingResponse(_handle_chat(msg), media_type="text/event-stream")


async def _handle_chat(msg: str):
    """Stream agent response with tools."""
    global conversation, total_tokens

    yield _sse("status", "⏳ Reflexion...")

    from ely.agent import _build_system_prompt
    from ely.tools import get_tools
    from ely.memory import build_memory_prompt
    from ely.providers import create_provider
    from ely.config import get_provider_config, get_int

    system_prompt = _build_system_prompt(current_context)
    mem = build_memory_prompt("default")
    if mem: system_prompt += mem

    provider = create_provider(get_provider_config(current_slot))
    tool_defs, tool_handlers = get_tools()

    messages = [{"role": "system", "content": system_prompt}]
    for h in conversation[-10:]:
        if h.get("role") in ("user", "assistant"):
            messages.append({"role": h["role"], "content": str(h.get("content", ""))[:2500]})
    messages.append({"role": "user", "content": msg})

    conversation.append({"role": "user", "content": msg})

    actions = []
    tokens = {"prompt": 0, "completion": 0, "total": 0}
    reply = ""
    all_reasoning = ""

    max_turns = get_int("agent", "max_turns", 8)

    for turn in range(max_turns):
        content = ""
        reasoning = ""
        tool_calls = []

        for event, data in provider.chat_stream(messages, tools=tool_defs if tool_defs else None):
            if event == "content":
                content += data
                yield _sse("stream", data)

            elif event == "reasoning":
                reasoning += data
                yield _sse("reasoning", data)

            elif event == "tool_calls":
                tool_calls = data
                yield _sse("tool_calls", json.dumps([{"name": tc["function"]["name"],
                          "args": tc["function"]["arguments"]} for tc in data]))

            elif event == "done":
                d = data
                content = d.get("content", content)
                tool_calls = d.get("tool_calls", tool_calls)
                u = d.get("usage", {})
                tokens["prompt"] += u.get("prompt_tokens", 0)
                tokens["completion"] += u.get("completion_tokens", 0)
                tokens["total"] += u.get("total_tokens", 0)

            elif event == "error":
                yield _sse("error", data)
                return

        if reasoning: all_reasoning += reasoning

        if not tool_calls:
            reply = content
            break

        for tc in tool_calls:
            name = tc["function"]["name"]
            args_str = tc["function"]["arguments"]
            try: args = json.loads(args_str)
            except Exception: args = {}
            actions.append(name)

            yield _sse("tool_start", json.dumps({"name": name, "args": args_str[:200]}))

            handler = tool_handlers.get(name)
            if handler:
                try: result = str(handler(**args))
                except Exception as e: result = f"Error: {e}"
            else:
                result = f"Unknown: {name}"

            yield _sse("tool_result", result[:300])

            messages.append({"role": "assistant", "content": content or "",
                             "tool_calls": [{"id": tc.get("id", f"call_{turn}"), "type": "function",
                                             "function": {"name": name, "arguments": args_str}}]})
            messages.append({"role": "tool", "tool_call_id": tc.get("id", f"call_{turn}"), "content": result})

        if turn == max_turns - 3:
            messages.append({"role": "user", "content": "Reponse finale. Plus d'outils."})

    if not reply:
        resp = provider.chat(messages, tools=None)
        reply = resp.get("content", "")

    total_tokens["prompt"] += tokens["prompt"]
    total_tokens["completion"] += tokens["completion"]
    total_tokens["total"] += tokens["total"]

    conversation.append({"role": "assistant", "content": reply})

    yield _sse("done", json.dumps({
        "reply": reply,
        "reasoning": all_reasoning,
        "actions": actions,
        "tokens": tokens["total"],
        "total_tokens": total_tokens["total"],
    }))


async def _handle_quick(msg: str):
    """Quick LLM query — no tools."""
    from ely.providers import create_provider
    from ely.config import get_provider_config

    provider = create_provider(get_provider_config(current_slot))
    msgs = [{"role": "user", "content": msg}]
    for event, data in provider.chat_stream(msgs, tools=None):
        if event == "content": yield _sse("stream", data)
        elif event == "done": yield _sse("done", json.dumps({"reply": data.get("content", ""), "tokens": data.get("usage", {}).get("total_tokens", 0)}))
        elif event == "error": yield _sse("error", data)


async def _handle_bash(cmd: str):
    """Direct bash command."""
    from ely.tools._core import run_direct
    result = run_direct(cmd.strip(), sanitize=False)
    yield _sse("bash", result)


async def _handle_slash(cmd_line: str):
    """Handle slash commands."""
    parts = cmd_line.split()
    cmd = parts[0].lower()

    if cmd == "/help":
        yield _sse("help", "Commands: /help /clear /tokens /context /skill /diary /pro /flash")
    elif cmd == "/clear":
        global conversation, total_tokens
        conversation = []
        total_tokens = {"prompt": 0, "completion": 0, "total": 0}
        yield _sse("cleared", "Conversation cleared")
    elif cmd == "/tokens":
        yield _sse("tokens", json.dumps(total_tokens))
    elif cmd in ("/pro", "/flash"):
        global current_slot
        current_slot = "pro_provider" if cmd == "/pro" else "provider"
        yield _sse("status", f"Provider: {current_slot}")
    else:
        yield _sse("error", f"Unknown: {cmd}")


def _sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


async def _sse_error(msg: str):
    yield _sse("error", msg)


def run():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=10080, log_level="info")


if __name__ == "__main__":
    run()
