"""
MCP (Model Context Protocol) client and manager.
Supports both stdio (local subprocess) and SSE (remote) transports.

Config in ely.yaml:
  mcp:
    servers:
      - name: filesystem
        command: npx
        args: ["-y", "@modelcontextprotocol/server-filesystem", "/path"]
        env: {KEY: value}
      - name: remote-api
        url: https://mcp.example.com/sse
        transport: sse
        headers: {Authorization: "Bearer xxx"}
"""

import json
import os
import subprocess
import threading
import time
import uuid
from typing import Any


# ═══════════════════════════════════════════════════════════════
# JSON-RPC helpers
# ═══════════════════════════════════════════════════════════════

def _rpc_request(method: str, params: dict = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": method,
        "params": params or {},
    }


def _rpc_notification(method: str, params: dict = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
    }


# ═══════════════════════════════════════════════════════════════
# Stdio Transport
# ═══════════════════════════════════════════════════════════════

class StdioTransport:
    """MCP transport over a subprocess stdin/stdout."""

    def __init__(self, command: str, args: list[str] = None, env: dict = None):
        self.command = command
        self.args = args or []
        self.env = {**os.environ, **(env or {})}
        self.process = None
        self._lock = threading.Lock()
        self._buf = b""

    def start(self):
        self.process = subprocess.Popen(
            [self.command] + self.args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.env,
        )
        # Drain stderr in background to prevent pipe buffer from blocking the server
        self._stderr_lines = []
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def _drain_stderr(self):
        """Read stderr continuously to prevent pipe blocking."""
        try:
            while self.process and self.process.poll() is None:
                line = self.process.stderr.readline()
                if not line:
                    break
                self._stderr_lines.append(line.decode(errors="replace").rstrip())
        except Exception:
            pass

    def send(self, message: dict) -> dict | None:
        """Send a JSON-RPC request and wait for the response.
        Uses newline-delimited JSON (compatible with both FastMCP and spec servers).
        """
        if not self.process or self.process.poll() is not None:
            return None

        payload = json.dumps(message) + "\n"

        with self._lock:
            try:
                self.process.stdin.write(payload.encode())
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                return None
            return self._read_response()

    def send_notification(self, message: dict):
        """Send a JSON-RPC notification (no response expected)."""
        if not self.process or self.process.poll() is not None:
            return
        try:
            payload = json.dumps(message) + "\n"
            with self._lock:
                self.process.stdin.write(payload.encode())
                self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def _read_response(self, timeout: float = 30.0) -> dict | None:
        """Read a single JSON-RPC message from stdout.
        Handles both Content-Length framed (spec) and newline-delimited (FastMCP) formats.
        """
        import select
        try:
            readable, _, _ = select.select([self.process.stdout], [], [], timeout)
            if not readable:
                return None

            # Peek at first bytes to detect format
            first = self.process.stdout.read(1)
            if not first:
                return None

            first_line = first + self.process.stdout.readline()
            first_str = first_line.decode().strip()

            # Format 1: newline-delimited JSON (FastMCP, some servers)
            if first_str.startswith("{"):
                return json.loads(first_str)

            # Format 2: Content-Length framed (MCP spec)
            headers = {}
            if ":" in first_str:
                key, val = first_str.split(":", 1)
                headers[key.strip().lower()] = val.strip()

            deadline = time.time() + timeout
            while time.time() < deadline:
                readable, _, _ = select.select([self.process.stdout], [], [], deadline - time.time())
                if not readable:
                    return None
                line = self.process.stdout.readline().decode().strip()
                if not line:
                    break
                if ":" in line:
                    key, val = line.split(":", 1)
                    headers[key.strip().lower()] = val.strip()
            else:
                return None

            content_length = int(headers.get("content-length", 0))
            if content_length <= 0:
                return None

            body = self.process.stdout.read(content_length).decode()
            return json.loads(body)
        except Exception:
            return None

    def close(self):
        if self.process:
            try:
                self.process.stdin.close()
            except Exception:
                pass
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None


# ═══════════════════════════════════════════════════════════════
# SSE Transport
# ═══════════════════════════════════════════════════════════════

class SSETransport:
    """MCP transport over Server-Sent Events + HTTP POST."""

    def __init__(self, url: str, headers: dict = None):
        self.url = url.rstrip("/")
        self.headers = headers or {}
        self._session_id = None
        self._events = []
        self._lock = threading.Lock()
        self._listening = False
        self._thread = None

    def start(self):
        """Open the SSE connection and start listening."""
        import requests
        self._listening = True
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()
        # Wait for initial connection
        time.sleep(0.5)

    def _listen(self):
        """Listen for SSE events in a background thread."""
        import requests
        try:
            resp = requests.get(
                self.url,
                headers={**self.headers, "Accept": "text/event-stream"},
                stream=True,
                timeout=30,
            )
            if resp.status_code == 200:
                # Check for session ID in headers
                self._session_id = resp.headers.get("Mcp-Session-Id", resp.headers.get("mcp-session-id"))

            current_event = {}
            for line in resp.iter_lines(decode_unicode=True):
                if not self._listening:
                    break
                if line is None:
                    continue
                if line == "":
                    # End of event
                    if current_event.get("data"):
                        try:
                            with self._lock:
                                self._events.append((current_event.get("event", "message"), current_event["data"]))
                        except Exception:
                            pass
                    current_event = {}
                elif line.startswith("event: "):
                    current_event["event"] = line[7:]
                elif line.startswith("data: "):
                    current_event["data"] = line[6:]
                elif line.startswith("id: "):
                    current_event["id"] = line[4:]
        except Exception:
            pass

    def send(self, message: dict) -> dict | None:
        """Send a JSON-RPC request via HTTP POST and return the response."""
        import requests
        try:
            headers = {**self.headers, "Content-Type": "application/json"}
            if self._session_id:
                headers["Mcp-Session-Id"] = self._session_id

            resp = requests.post(
                self.url,
                json=message,
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            if resp.headers.get("content-type", "").startswith("application/json"):
                return resp.json()
            # Response might come via SSE
            return None
        except Exception:
            return None

    def send_notification(self, message: dict):
        """Send a JSON-RPC notification via HTTP POST."""
        import requests
        try:
            headers = {**self.headers, "Content-Type": "application/json"}
            if self._session_id:
                headers["Mcp-Session-Id"] = self._session_id
            requests.post(self.url, json=message, headers=headers, timeout=10)
        except Exception:
            pass

    def pop_events(self) -> list[tuple[str, str]]:
        """Get and clear accumulated SSE events."""
        with self._lock:
            events = self._events[:]
            self._events.clear()
        return events

    def close(self):
        self._listening = False
        if self._thread:
            self._thread.join(timeout=2)


# ═══════════════════════════════════════════════════════════════
# MCP Client
# ═══════════════════════════════════════════════════════════════

class MCPClient:
    """Wraps a single MCP server connection."""

    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config
        self.transport = None
        self._tools = []
        self._resources = []
        self._connected = False
        self._error = ""

    def connect(self):
        """Start transport and perform MCP handshake. Returns (ok, error_message)."""
        import shutil as _shutil

        transport_type = self.config.get("transport", "stdio")

        if transport_type == "sse":
            url = self.config.get("url", "")
            if not url:
                return False, f"'{self.name}': url required for SSE transport"
            self.transport = SSETransport(url, self.config.get("headers"))
        else:
            command = self.config.get("command", "")
            if not command:
                return False, f"'{self.name}': command required for stdio transport"

            # Pre-flight: check if command exists
            if not _shutil.which(command.split()[0]):
                self._error = f"command not found: {command}"
                return False, self._error

            # If npx, allow extra startup time for package download
            startup_timeout = 30 if "npx" in command else 10

            self.transport = StdioTransport(
                command,
                self.config.get("args", []),
                self.config.get("env"),
            )

        try:
            self.transport.start()
        except Exception as e:
            return False, f"'{self.name}': failed to start: {e}"

        # Initialize handshake
        init_resp = self.transport.send(_rpc_request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "Ely", "version": "1.0"},
        }))

        if init_resp is None:
            stderr_tail = ""
            if hasattr(self.transport, '_stderr_lines') and self.transport._stderr_lines:
                tail = self.transport._stderr_lines[-3:]
                stderr_tail = " | stderr: " + "; ".join(tail)[:150]
            self._error = f"no response{stderr_tail}"
            self.transport.close()
            return False, self._error

        if "error" in init_resp:
            err = init_resp["error"].get("message", str(init_resp["error"]))
            self.transport.close()
            return False, f"'{self.name}': {err}"

        # Send initialized notification
        self.transport.send_notification(_rpc_notification("notifications/initialized"))
        time.sleep(0.1)

        self._connected = True
        self._discover()
        return True, f"{len(self._tools)} tools, {len(self._resources)} resources"

    def _discover(self):
        """Discover available tools and resources."""
        # List tools
        try:
            tools_resp = self.transport.send(_rpc_request("tools/list"))
            if tools_resp and "result" in tools_resp:
                self._tools = tools_resp["result"].get("tools", [])
        except Exception:
            pass

        # List resources (optional — some servers don't support this)
        try:
            res_resp = self.transport.send(_rpc_request("resources/list"))
            if res_resp and "result" in res_resp:
                self._resources = res_resp["result"].get("resources", [])
        except Exception:
            pass

    @property
    def tools(self) -> list[dict]:
        return self._tools

    @property
    def resources(self) -> list[dict]:
        return self._resources

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Call an MCP tool and return the result as a string."""
        if not self._connected:
            return f"Error: MCP server '{self.name}' not connected"

        resp = self.transport.send(_rpc_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        }))

        if resp is None:
            return f"Error: no response from MCP server '{self.name}'"

        if "error" in resp:
            return f"MCP error: {resp['error'].get('message', str(resp['error']))}"

        result = resp.get("result", {})
        content = result.get("content", [])

        # Extract text from content array
        if isinstance(content, list):
            texts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    texts.append(item.get("text", ""))
                elif isinstance(item, dict) and item.get("type") == "resource":
                    texts.append(f"[Resource: {item.get('resource', {}).get('uri', '')}]")
                elif isinstance(item, str):
                    texts.append(item)
            return "\n".join(texts) if texts else json.dumps(result)
        elif isinstance(content, str):
            return content

        return json.dumps(result)

    def read_resource(self, uri: str) -> str | None:
        """Read an MCP resource by URI."""
        if not self._connected:
            return None
        resp = self.transport.send(_rpc_request("resources/read", {"uri": uri}))
        if resp and "result" in resp:
            contents = resp["result"].get("contents", [])
            if contents:
                return contents[0].get("text", json.dumps(contents[0]))
        return None

    def close(self):
        if self.transport:
            self.transport.close()
        self._connected = False


# ═══════════════════════════════════════════════════════════════
# MCP Manager
# ═══════════════════════════════════════════════════════════════

class MCPManager:
    """Manages multiple MCP server connections."""

    def __init__(self):
        self.clients: dict[str, MCPClient] = {}
        self._initialized = False

    def load_from_config(self) -> list[MCPClient]:
        """Create MCP clients from ely.yaml config."""
        from .config import get
        import yaml

        servers_str = get("mcp", "servers", "")
        if not servers_str:
            return []

        try:
            servers = yaml.safe_load(servers_str) if isinstance(servers_str, str) else servers_str
        except Exception:
            return []

        if not isinstance(servers, list):
            return []

        for srv in servers:
            name = srv.get("name", "")
            if name and name not in self.clients:
                self.clients[name] = MCPClient(name, srv)

        return list(self.clients.values())

    def connect_all(self, timeout: float = 15.0):
        """Connect to all configured MCP servers. Timeout per server."""
        import threading

        if self._initialized:
            return

        if not self.clients:
            self.load_from_config()

        for name, client in self.clients.items():
            result = [None]

            def _connect():
                try:
                    ok, msg = client.connect()
                    result[0] = (ok, msg)
                except Exception as e:
                    result[0] = (False, str(e))

            t = threading.Thread(target=_connect, daemon=True)
            t.start()
            t.join(timeout=timeout)

            if result[0] is None:
                try:
                    client.close()
                except Exception:
                    pass
                client._error = f"timeout after {timeout}s"
                result[0] = (False, client._error)

        self._initialized = True

    def get_all_tools(self) -> tuple[list[dict], dict[str, callable]]:
        """Get all MCP tool definitions and handlers.
        Returns (tool_defs, tool_handlers) compatible with get_tools().
        Tools are prefixed: mcp__<server>__<tool_name>
        """
        definitions = []
        handlers = {}

        for srv_name, client in self.clients.items():
            if not client._connected:
                continue
            for tool in client.tools:
                tool_name = tool.get("name", "")
                prefixed = f"mcp__{srv_name}__{tool_name}"

                # Build OpenAI-format tool definition
                defs = {
                    "type": "function",
                    "function": {
                        "name": prefixed,
                        "description": f"[MCP:{srv_name}] {tool.get('description', '')}",
                        "parameters": tool.get("inputSchema", {"type": "object", "properties": {}}),
                    },
                }
                definitions.append(defs)

                # Create handler closure
                def make_handler(c, t_name):
                    def handler(**kwargs):
                        return c.call_tool(t_name, kwargs)
                    return handler

                handlers[prefixed] = make_handler(client, tool_name)

        return definitions, handlers

    def get_resources_context(self) -> str:
        """Get MCP resources as a context string for the system prompt."""
        lines = []
        for srv_name, client in self.clients.items():
            if not client._connected or not client.resources:
                continue
            lines.append(f"\n**MCP Resources ({srv_name}) :**")
            for r in client.resources[:10]:
                uri = r.get("uri", "")
                desc = r.get("description", "") or r.get("name", "")
                lines.append(f"- `{uri}` — {desc}")
        return "\n".join(lines) if lines else ""

    def get_status(self) -> list[dict]:
        """Return status of all configured servers for display."""
        status = []
        for name, client in self.clients.items():
            status.append({
                "name": name,
                "connected": client._connected,
                "tools_count": len(client.tools),
                "resources_count": len(client.resources),
                "transport": client.config.get("transport", "stdio"),
                "command": client.config.get("command") or client.config.get("url", ""),
                "args": client.config.get("args", []),
                "error": client._error if not client._connected else "",
            })
        return status

    def close_all(self):
        for client in self.clients.values():
            try:
                client.close()
            except Exception:
                pass
        self.clients.clear()
        self._initialized = False


# Singleton
_manager = None


def get_mcp_manager() -> MCPManager:
    global _manager
    if _manager is None:
        _manager = MCPManager()
    return _manager
