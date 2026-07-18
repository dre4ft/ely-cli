"""Web tools — search, fetch, HTTP requests, raw sockets."""
import json
import requests
from ._core import action


@action("web_search", "Search the web for information.",
         {"query": {"type": "string", "description": "Search query."}})
def tool_web_search(query: str) -> str:
    try:
        import re as _re
        from html import unescape
        resp = requests.post("https://html.duckduckgo.com/html/", data={"q": query}, timeout=15,
                             headers={"User-Agent": "Ely-CLI/1.0"})
        resp.raise_for_status()
        results = []

        # Try DDG HTML results first
        for m in _re.finditer(
            r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
            resp.text, _re.DOTALL | _re.IGNORECASE
        ):
            link = m.group(1)
            title = unescape(_re.sub(r'<.*?>', '', m.group(2)).strip())
            snippet = unescape(_re.sub(r'<.*?>', '', m.group(3)).strip())
            if title and link and "duckduckgo.com" not in link:
                results.append(f"- [{title}]({link})\n  {snippet[:200]}")
            if len(results) >= 5: break

        # Fallback: extract any links from the page
        if not results:
            for m in _re.finditer(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', resp.text, _re.DOTALL | _re.IGNORECASE):
                link = m.group(1)
                text = unescape(_re.sub(r'<.*?>', ' ', m.group(2)).strip())[:200]
                if text and link and not any(d in link for d in ("duckduckgo.com", "facebook.com", "twitter.com", "apple.com")):
                    results.append(f"- {text}\n  {link}")
                if len(results) >= 5: break

        return "\n".join(results) if results else f"No results for: {query}\nTry web_fetch with a search engine URL, or use browser_navigate to search directly."
    except Exception as e: return f"Search error: {e}\nTry browser_navigate to search on Google, Bing, or DuckDuckGo directly."


@action("web_fetch", "Fetch and extract text content from a URL.",
         {"url": {"type": "string", "description": "URL to fetch."}})
def tool_web_fetch(url: str) -> str:
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Ely-CLI/1.0"})
        resp.raise_for_status()
        ct = resp.headers.get("Content-Type", "").lower()
        from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
        import warnings
        if "xml" in ct or "rss" in ct or "atom" in ct:
            soup = BeautifulSoup(resp.text, "xml")
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
                soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]): tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        lines = [l for l in text.splitlines() if l.strip()]
        return "\n".join(lines)[:3000]
    except ImportError as e: return f"Error: missing package — {e}"
    except Exception as e: return f"Error fetching {url}: {e}"


@action("http_request", "Make an HTTP request with full control over method, headers, and body.",
         {"url": {"type": "string", "description": "Target URL."},
          "method": {"type": "string", "description": "HTTP method: GET, POST, PUT, DELETE, PATCH, OPTIONS, HEAD."},
          "headers": {"type": "string", "description": "JSON object of headers."},
          "body": {"type": "string", "description": "Request body (for POST/PUT/PATCH)."},
          "follow_redirects": {"type": "boolean", "description": "Follow redirects? Default true."}},
         optional=["headers", "body", "follow_redirects"])
def tool_http_request(url: str, method: str = "GET", headers: str = "{}", body: str = "", follow_redirects: bool = True) -> str:
    try:
        hdrs = json.loads(headers) if isinstance(headers, str) else headers
        if not isinstance(hdrs, dict): hdrs = {}
    except (json.JSONDecodeError, ValueError): hdrs = {}
    hdrs.setdefault("User-Agent", "Ely-CLI/1.0")
    try:
        kwargs = {"method": method.upper(), "url": url, "headers": hdrs, "timeout": 30, "allow_redirects": follow_redirects}
        if body and method.upper() in ("POST", "PUT", "PATCH"): kwargs["data"] = body
        resp = requests.request(**kwargs)
        out = [f"HTTP {resp.status_code} {resp.reason}", "\n--- Response Headers ---"]
        for k, v in resp.headers.items(): out.append(f"  {k}: {v}")
        out.append(f"\n--- Response Body ({len(resp.text)} chars) ---")
        out.append(resp.text[:2000])
        return "\n".join(out)
    except Exception as e: return f"HTTP request error: {e}"


@action("http_batch", "Execute multiple HTTP requests in PARALLEL.",
         {"requests": {"type": "string", "description": "JSON array: [{\"url\": \"...\", \"method\": \"GET\", \"headers\": {}, \"body\": \"\"}]."}})
def tool_http_batch(requests: str) -> str:
    try:
        reqs = json.loads(requests)
        if not isinstance(reqs, list): return "Error: requests must be a JSON array"
    except json.JSONDecodeError: return "Error: invalid JSON"
    from ._core import run_parallel
    def _one(req):
        if not isinstance(req, dict): return "Error: invalid request"
        hdrs = req.get("headers", {})
        return tool_http_request(url=req.get("url", ""), method=req.get("method", "GET"),
                                 headers=json.dumps(hdrs) if isinstance(hdrs, dict) else str(hdrs),
                                 body=str(req.get("body", "")))
    results = run_parallel(reqs, _one)
    return "\n\n".join(f"--- [{i}] {req.get('method', 'GET')} {req.get('url', '?')} ---\n{output}"
                       for i, (req, output) in enumerate(zip(reqs, results)))


@action("socket_raw", "Open a raw TCP socket to a host:port, send data, and read the response.",
         {"host": {"type": "string", "description": "Target hostname or IP."},
          "port": {"type": "integer", "description": "Target port."},
          "data": {"type": "string", "description": "Data to send. Use \\r\\n for line breaks."},
          "timeout": {"type": "integer", "description": "Read timeout in seconds (default 10)."},
          "use_tls": {"type": "boolean", "description": "Wrap socket with TLS/SSL? Default false."}},
         optional=["timeout", "use_tls"])
def tool_socket_raw(host: str, port: int, data: str, timeout: int = 10, use_tls: bool = False) -> str:
    try:
        import socket, ssl
        payload = data.replace("\\r\\n", "\r\n").replace("\\n", "\n").replace("\\t", "\t")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        if use_tls:
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(sock, server_hostname=host)
        sock.connect((host, port))
        sock.sendall(payload.encode())
        response = b""
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk: break
                response += chunk
            except socket.timeout: break
        sock.close()
        out = [f"Connected to {host}:{port}" + (" (TLS)" if use_tls else ""), f"Sent {len(payload)} bytes",
               f"\n--- Response ({len(response)} bytes) ---"]
        try: out.append(response.decode(errors="replace")[:2000])
        except Exception: out.append(response.hex()[:2000])
        return "\n".join(out)
    except Exception as e: return f"Socket error: {e}"
