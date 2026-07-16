"""
Prompt injection guard — detects and filters known attack patterns from LLM inputs.
"""

import re

_INJECTION_PATTERNS = [
    # ── Zero-width / hidden characters ──
    (r'[​‌‍‎‏﻿]', '', 'hidden chars'),
    # ANSI escape sequences
    (r'\x1b\[[0-9;]*[a-zA-Z]', '', 'ANSI escapes'),

    # ── Instruction override ──
    (r'(?i)(ignore|forget|disregard|override|discard)\s+(all\s+)?(previous|prior|above|your|earlier)\s+(instructions?|prompts?|rules?|directives?|context)',
     '[FILTERED:instruction-override]', 'instruction override'),
    (r'(?i)(from now on|starting now|new rule|new instruction)s?\s*[:;]',
     '[FILTERED:new-rule]', 'new rule injection'),
    (r'(?i)(you must|you should|you will|you are required to)\s+(ignore|forget|disobey)',
     '[FILTERED:must-ignore]', 'must ignore'),

    # ── System prompt extraction ──
    (r'(?i)(system\s*prompt|developer\s*(message|prompt|instruction))',
     '[FILTERED:system-ref]', 'system reference'),
    (r'(?i)(print|echo|repeat|output|show|display|tell me|what is|reveal)\s+(your\s+)?(exact\s+)?(system\s+)?(prompt|instructions?|rules?|message|config)',
     '[FILTERED:prompt-leak]', 'prompt leak attempt'),
    (r'(?i)(what\s+(does|do|are)\s+your\s+)?(initial|first|original|system|base)\s+(prompt|instruction|message|rule)s?\s*(say|tell|contain|include|look like)',
     '[FILTERED:prompt-leak]', 'prompt leak attempt'),

    # ── Persona / jailbreak ──
    (r'(?i)\bDAN\b.*\b(do|now|anything)\b', '[FILTERED:jailbreak]', 'DAN jailbreak'),
    (r'(?i)(you\s+are\s+now|from\s+now\s+on\s+you\s+are|act\s+as\s+a|pretend\s+to\s+be)\s+(DAN|jailbreak|evil|unethical|unfiltered|unhinged|unrestricted)',
     '[FILTERED:persona-override]', 'persona override'),
    (r'(?i)(you are|act as|pretend|imagine you are)\s+(a\s+)?(different|another|new)\s+(AI|assistant|model|agent| persona)',
     '[FILTERED:role-switch]', 'role switch attempt'),
    (r'(?i)(switch|change|swap|flip)\s+(your|the)\s+(role|persona|identity|behaviour)',
     '[FILTERED:role-switch]', 'role switch attempt'),

    # ── Token / delimiter smuggling ──
    (r'(?i)<\|im_start\|>|<\|im_end\|>', '[FILTERED:token-marker]', 'token marker'),
    (r'(?i)\[INST\]|\[/INST\]|<<SYS>>|<<\/SYS>>', '[FILTERED:chat-template]', 'chat template'),
    (r'(?i)<\|assistant\|>|<\|user\|>|<\|system\|>', '[FILTERED:delimiter]', 'protocol delimiter'),

    # ── Fake tool results / message injection ──
    (r'(?i)\{"role"\s*:\s*"(system|assistant|tool)"\s*,', '[FILTERED:fake-message]', 'fake message injection'),
    (r'(?i)\{"tool_call_id"\s*:', '[FILTERED:fake-tool]', 'fake tool result'),
    (r'(?i)```json\s*\n?\s*\{\s*"role"', '[FILTERED:fake-message]', 'fake message block'),

    # ── Multi-language attacks ──
    (r'(?i)(ignorieren|ignoriere|vergessen|vergiss|überschreiben|ignorer|oublier|ignorar|olvidar|無視|忽略)\s+(alle\s+)?(vorherigen|vorherige|précédentes|anteriores|以前の)\s+(Anweisungen|instructions|instrucciones|指示)',
     '[FILTERED:multi-lang-override]', 'multi-language override'),

    # ── Encoded content (base64, hex) ──
    (r'(?i)(base64\s*(-d|--decode|decode|encoded|:)|from\s+base64|atob\s*\()', '[FILTERED:base64-attempt]', 'base64 encoding'),
    (r'(?i)(hex\s*(decode|encoded|:)|from\s+hex|fromhex\s*\()', '[FILTERED:hex-attempt]', 'hex encoding'),
]

# Patterns that are suspicious but not blocked — just flagged
_SUSPICIOUS_PATTERNS = [
    (r'(?i)(password|secret|token|api.key|credential)\s*(is|:|=)\s*[\w\-]{8,}', 'credentials in input'),
    (r'(?i)(rm\s+-rf|sudo\s+rm|shutdown|reboot|mkfs\.)', 'destructive command'),
    (r'(?i)(curl|wget)\s+.*\|\s*(sh|bash|python)', 'pipe to shell'),
]


def sanitize(text: str) -> tuple[str, bool]:
    """Sanitize user input. Returns (clean_text, was_flagged)."""
    if not text:
        return text, False

    flagged = False
    result = text

    for pattern, replacement, _tag in _INJECTION_PATTERNS:
        if re.search(pattern, result):
            result = re.sub(pattern, replacement, result)
            flagged = True

    # Check suspicious patterns (flag but don't filter content)
    for pattern, _desc in _SUSPICIOUS_PATTERNS:
        if re.search(pattern, result):
            flagged = True

    return result, flagged
