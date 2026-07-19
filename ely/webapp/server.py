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


def _get_index_html() -> str:
    """Find index.html relative to this file or in common locations."""
    candidates = [
        Path(__file__).parent / "index.html",
        Path(__file__).parent / "static" / "index.html",
        Path.cwd() / "webapp" / "index.html",
        Path.cwd() / "ely" / "webapp" / "index.html",
    ]
    for p in candidates:
        if p.is_file():
            return p.read_text()
    # Fallback: embedded minimal HTML
    return _FALLBACK_HTML


@app.get("/", response_class=HTMLResponse)
async def index():
    return _get_index_html()


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


_FALLBACK_HTML = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Ely WebApp</title><style>
:root{--bg:#0d1117;--fg:#c9d1d9;--dim:#6e7681;--cyan:#58a6ff;--green:#3fb950;--red:#f85149;--panel:#161b22;--border:#30363d}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,BlinkMacSystemFont,sans-serif;height:100vh;display:flex;flex-direction:column}
header{padding:8px 16px;border-bottom:1px solid var(--border);font-size:13px;display:flex;gap:16px}
header .dot{color:var(--green)}
#conv{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:8px}
.msg{max-width:85%;padding:10px 14px;border-radius:8px;line-height:1.6}
.user{align-self:flex-end;background:var(--cyan);color:#fff}
.agent{align-self:flex-start;background:var(--panel);border:1px solid var(--border)}
.tool{align-self:flex-start;background:var(--panel);border-left:3px solid var(--cyan);font-size:12px;color:var(--dim)}
.reasoning{align-self:flex-start;background:var(--panel);border-left:3px solid var(--dim);font-size:12px;color:var(--dim);font-style:italic}
.error{align-self:center;color:var(--red);font-size:12px}
.status{align-self:center;color:var(--dim);font-size:12px}
#input-area{padding:12px 16px;border-top:1px solid var(--border);display:flex;gap:8px}
#input-area input{flex:1;background:var(--panel);border:1px solid var(--border);color:var(--fg);padding:10px 14px;border-radius:6px;font-size:14px;outline:none}
#input-area input:focus{border-color:var(--cyan)}
#input-area button{background:var(--cyan);color:#fff;border:none;padding:10px 18px;border-radius:6px;cursor:pointer;font-weight:600}
footer{padding:6px 16px;border-top:1px solid var(--border);font-size:11px;color:var(--dim);display:flex;gap:16px}
pre{background:#0d1117;padding:8px;border-radius:4px;overflow-x:auto;margin:4px 0}
</style></head><body>
<header><span><span class="dot">●</span> <b>Ely</b></span><span id="model">...</span><span id="ctx">ctx: default</span><span id="tokens">🪙 0</span></header>
<div id="conv"></div>
<div id="input-area"><input id="input" placeholder="Message, ?question, #cmd, ou /help..." autofocus onkeydown="if(event.key==='Enter')send()"><button onclick="send()">Send</button></div>
<footer><span>? = quick LLM</span> <span># = bash</span> <span>/ = commands</span></footer>
<script>
const conv=document.getElementById('conv'),input=document.getElementById('input');
let currentMsg=null,streamBuffer='';
function addMsg(role,content){const d=document.createElement('div');d.className='msg '+role;d.innerHTML=content;conv.appendChild(d);conv.scrollTop=conv.scrollHeight;return d}
function addStatus(text){const d=document.createElement('div');d.className='status';d.textContent=text;conv.appendChild(d);conv.scrollTop=conv.scrollHeight;return d}
async function send(){const msg=input.value.trim();if(!msg)return;input.value='';addMsg('user',msg);
const statusEl=addStatus('⏳ ...');const resp=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg})});
const reader=resp.body.getReader();const decoder=new TextDecoder();let buf='',agentEl=null,reasoningEl=null;
while(true){const{ done, value}=await reader.read();if(done)break;buf+=decoder.decode(value,{stream:true});
const parts=buf.split('\\n\\n');buf=parts.pop();
for(const part of parts){const lines=part.split('\\n');let event='',data='';
for(const line of lines){if(line.startsWith('event: '))event=line.slice(7);else if(line.startsWith('data: '))data=line.slice(6)}
if(!event)continue;statusEl?.remove();
if(event==='stream'){if(!agentEl)agentEl=addMsg('agent','');streamBuffer+=data;agentEl.textContent=streamBuffer}
else if(event==='reasoning'){if(!reasoningEl)reasoningEl=addMsg('reasoning','');if(reasoningEl.textContent.length<400)reasoningEl.textContent+=data}
else if(event==='tool_calls'){const tcs=JSON.parse(data);for(const tc of tcs)addMsg('tool','🔧 <b>'+tc.name+'</b> '+tc.args.slice(0,100))}
else if(event==='tool_result'){addMsg('tool','⮡ '+data.slice(0,200))}
else if(event==='done'){const d=JSON.parse(data);if(agentEl)agentEl.textContent=d.reply||'';streamBuffer='';agentEl=null;reasoningEl=null;document.getElementById('tokens').textContent='🪙 '+(d.total_tokens||0).toLocaleString()}
else if(event==='error'){addMsg('error',data)}
else if(event==='bash'){addMsg('agent','<pre>'+data+'</pre>')}
else if(event==='cleared'){conv.innerHTML='';addStatus('✨ Cleared')}
else if(event==='help'){addMsg('agent',data)}
}}}
</script></body></html>"""


if __name__ == "__main__":
    run()
