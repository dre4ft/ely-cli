"""Vision tool — analyze images using OpenAI-compatible vision endpoints."""
import base64, os
from ._core import action, resolve_path


def _encode_image(path: str) -> str:
    """Read image file and return base64 data URI."""
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    ext = os.path.splitext(path)[1].lower()
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/png")
    return f"data:{mime};base64,{data}"


@action("vision", "Analyze an image using vision capabilities. Describe content, extract text (OCR), or answer questions about the image.",
        {"image_path": {"type": "string", "description": "Path to image file in workspace (png, jpg, gif, webp)."},
         "prompt": {"type": "string", "description": "What to analyze. E.g. 'Describe this image', 'Extract all text', 'What colors are used?'."}})
def tool_vision(image_path: str, prompt: str) -> str:
    try:
        path = resolve_path(image_path)
    except ValueError as e:
        return f"Error: {e}"

    if not os.path.isfile(path):
        return f"Error: file not found: {image_path}"

    try:
        data_uri = _encode_image(path)
    except Exception as e:
        return f"Error reading image: {e}"

    # Build vision message
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_uri, "detail": "auto"}},
        ],
    }]

    try:
        from ..providers import create_provider
        from ..config import get_provider_config

        # Use pro provider for vision (more likely to support it)
        cfg = get_provider_config("pro_provider")
        provider = create_provider(cfg)

        resp = provider.chat(messages=messages, tools=None)
        if resp.get("content", "").startswith("Error:"):
            # Fallback to regular provider
            cfg = get_provider_config("provider")
            provider = create_provider(cfg)
            resp = provider.chat(messages=messages, tools=None)

        content = resp.get("content", "")
        if content.startswith("Error:"):
            return f"Vision not supported by current provider. {content}"
        return content
    except Exception as e:
        return f"Vision error: {e}"
