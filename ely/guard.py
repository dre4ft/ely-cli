"""
Prompt injection guard — strips known attack patterns from LLM inputs.
Extracted from ai_core/prompt_guard.py. No dependencies.
"""

import re

_INJECTION_PATTERNS = [
    # Zero-width characters
    (r'[​‌‍‎‏﻿]', ''),
    # ANSI escape sequences
    (r'\x1b\[[0-9;]*[a-zA-Z]', ''),
    # System prompt extraction
    (r'(?i)(ignore|forget|disregard|override)\s+(all\s+)?(previous|prior|above|your)\s+(instructions?|prompts?|rules?|directives?)', '[FILTERED: instruction override]'),
    (r'(?i)(system\s* prompt|developer\s* (message|prompt|instruction))', '[FILTERED: system reference]'),
    # DAN / jailbreak patterns
    (r'(?i)\bDAN\b.*\b(do|now|anything)\b', '[FILTERED: jailbreak]'),
    (r'(?i)(you\s+are\s+now|from\s+now\s+on\s+you\s+are|act\s+as\s+a)\s+(DAN|jailbreak|evil|unethical|unfiltered)', '[FILTERED: persona override]'),
    # Prompt leaking
    (r'(?i)(print|echo|repeat|output|show|display)\s+(your\s+)?(system\s+)?(prompt|instructions?|rules?|message)', '[FILTERED: prompt leak]'),
    (r'(?i)(what\s+(does|do)\s+your\s+)?(initial|first|system)\s+(prompt|instruction|message)\s*(say|tell|contain|include)', '[FILTERED: prompt leak]'),
    # Token smuggling
    (r'(?i)<\|im_start\|>|<\|im_end\|>', '[FILTERED: token marker]'),
    (r'(?i)\[INST\]|\[/INST\]|<<SYS>>|<<\/SYS>>', '[FILTERED: chat template]'),
]


def sanitize(text: str) -> tuple[str, bool]:
    """Sanitize user input. Returns (clean_text, was_flagged)."""
    if not text:
        return text, False
    flagged = False
    result = text
    for pattern, replacement in _INJECTION_PATTERNS:
        if re.search(pattern, result):
            result = re.sub(pattern, replacement, result)
            flagged = True
    return result, flagged
