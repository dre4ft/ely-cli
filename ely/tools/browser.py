"""Playwright browser tools — real browser automation for the agent."""

import threading
from . import _action

_browser = None
_browser_lock = threading.Lock()


def _get_browser():
    """Get or create a shared browser instance."""
    global _browser
    if _browser is None:
        from playwright.sync_api import sync_playwright
        _browser = sync_playwright().start().chromium.launch(headless=True)
    return _browser


@_action("browser_navigate", "Navigate to a URL and return the page text content.",
         {"url": {"type": "string", "description": "URL to navigate to."},
          "wait_until": {"type": "string", "description": "Wait until: load, domcontentloaded, networkidle (default: domcontentloaded)."}},
         optional=["wait_until"])
def tool_browser_navigate(url: str, wait_until: str = "domcontentloaded") -> str:
    try:
        with _browser_lock:
            browser = _get_browser()
            ctx = browser.new_context(viewport={"width": 1280, "height": 800})
            page = ctx.new_page()
            page.goto(url, wait_until=wait_until, timeout=30000)
            text = page.inner_text("body")
            title = page.title()
            url_final = page.url
            ctx.close()
            return f"**{title}**\nURL: {url_final}\n\n{text[:5000]}"
    except Exception as e:
        return f"Browser navigate error: {e}"


@_action("browser_snapshot", "Get an accessibility snapshot of the current page. Shows interactive elements with refs for clicking/filling.",
         {"url": {"type": "string", "description": "URL to snapshot (reuses existing page if same URL)."}})
def tool_browser_snapshot(url: str) -> str:
    try:
        with _browser_lock:
            browser = _get_browser()
            ctx = browser.new_context(viewport={"width": 1280, "height": 800})
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Extract interactive elements
            elements = page.evaluate("""() => {
                const result = [];
                const interactives = document.querySelectorAll('a, button, input, select, textarea, [role="button"], [onclick]');
                interactives.forEach((el, i) => {
                    const tag = el.tagName.toLowerCase();
                    const text = (el.textContent || '').trim().substring(0, 80);
                    const id = el.id || '';
                    const name = el.getAttribute('name') || '';
                    const type = el.getAttribute('type') || '';
                    const href = el.getAttribute('href') || '';
                    const placeholder = el.getAttribute('placeholder') || '';
                    result.push({
                        ref: i,
                        tag: tag,
                        text: text,
                        id: id,
                        name: name,
                        type: type,
                        href: href,
                        placeholder: placeholder
                    });
                });
                return result;
            }""")

            lines = [f"**Page: {page.title()}**", f"URL: {page.url}", f"\n{len(elements)} interactive elements:"]
            for el in elements:
                desc = f"<{el['tag']}>"
                if el['text']: desc += f" \"{el['text']}\""
                if el['id']: desc += f" #{el['id']}"
                if el['name']: desc += f" name={el['name']}"
                if el['type']: desc += f" type={el['type']}"
                if el['href']: desc += f" href={el['href'][:60]}"
                if el['placeholder']: desc += f" placeholder=\"{el['placeholder']}\""
                lines.append(f"  [{el['ref']}] {desc}")

            text = page.inner_text("body")[:3000]
            lines.append(f"\n--- Page Text ---\n{text}")
            ctx.close()
            return "\n".join(lines)
    except Exception as e:
        return f"Browser snapshot error: {e}"


@_action("browser_click", "Click an element on the page by its snapshot ref number.",
         {"url": {"type": "string", "description": "Current page URL."},
          "ref": {"type": "integer", "description": "Element ref number from browser_snapshot."}})
def tool_browser_click(url: str, ref: int) -> str:
    try:
        with _browser_lock:
            browser = _get_browser()
            ctx = browser.new_context(viewport={"width": 1280, "height": 800})
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Click the element by its ref
            result = page.evaluate(f"""(ref) => {{
                const interactives = document.querySelectorAll('a, button, input, select, textarea, [role="button"], [onclick]');
                const el = interactives[ref];
                if (!el) return {{error: 'Element ' + ref + ' not found'}};
                el.click();
                return {{clicked: true, tag: el.tagName.toLowerCase(), text: (el.textContent || '').trim().substring(0, 60)}};
            }}""", ref)

            page.wait_for_timeout(1000)  # Wait for any navigation/update
            text = page.inner_text("body")
            title = page.title()
            url_final = page.url
            ctx.close()
            return f"Clicked [{ref}]: {result}\n\n**{title}**\nURL: {url_final}\n\n{text[:4000]}"
    except Exception as e:
        return f"Browser click error: {e}"


@_action("browser_fill", "Fill a form field identified by its snapshot ref.",
         {"url": {"type": "string", "description": "Current page URL."},
          "ref": {"type": "integer", "description": "Element ref number from browser_snapshot."},
          "value": {"type": "string", "description": "Text to fill into the field."}})
def tool_browser_fill(url: str, ref: int, value: str) -> str:
    try:
        with _browser_lock:
            browser = _get_browser()
            ctx = browser.new_context(viewport={"width": 1280, "height": 800})
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

            result = page.evaluate(f"""(data) => {{
                const interactives = document.querySelectorAll('a, button, input, select, textarea, [role="button"], [onclick]');
                const el = interactives[data.ref];
                if (!el) return {{error: 'Element ' + data.ref + ' not found'}};
                el.value = data.value;
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                return {{filled: true, tag: el.tagName.toLowerCase()}};
            }}""", {"ref": ref, "value": value})

            ctx.close()
            return f"Filled [{ref}] with '{value}': {result}"
    except Exception as e:
        return f"Browser fill error: {e}"


@_action("browser_screenshot", "Take a screenshot of a page. Use to see visual layout, CAPTCHAs, or rendered content.",
         {"url": {"type": "string", "description": "URL to screenshot."},
          "full_page": {"type": "boolean", "description": "Capture full scrollable page? Default false."}},
         optional=["full_page"])
def tool_browser_screenshot(url: str, full_page: bool = False) -> str:
    try:
        import base64
        with _browser_lock:
            browser = _get_browser()
            ctx = browser.new_context(viewport={"width": 1280, "height": 800})
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            screenshot = page.screenshot(full_page=full_page)
            ctx.close()
            b64 = base64.b64encode(screenshot).decode()
            return f"Screenshot of {page.url} ({len(screenshot)} bytes)\nBase64: {b64[:200]}...\n(Full image available, use browser_navigate for text extraction)"
    except Exception as e:
        return f"Browser screenshot error: {e}"


@_action("browser_exec", "Execute JavaScript in the browser page and return the result.",
         {"url": {"type": "string", "description": "Current page URL."},
          "script": {"type": "string", "description": "JavaScript code to execute. Use return to get values back."}})
def tool_browser_exec(url: str, script: str) -> str:
    try:
        with _browser_lock:
            browser = _get_browser()
            ctx = browser.new_context(viewport={"width": 1280, "height": 800})
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            result = page.evaluate(f"(function() {{ {script} }})()")
            ctx.close()
            return f"Result: {str(result)[:3000]}"
    except Exception as e:
        return f"Browser exec error: {e}"
