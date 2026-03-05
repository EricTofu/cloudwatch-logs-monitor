"""Message fingerprinting for content-based deduplication."""

import hashlib
import re

# Masks for dynamic parts of log messages
MASKS = [
    # UUIDs
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<UUID>"),
    # IPv4 addresses
    (re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b"), "<IP>"),
    # Timestamps (ISO 8601-like, various formats including comma-separated milliseconds)
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:[,.]\d+)*(?:Z|[+-]\d{2}:\d{2})?\b"), "<TIMESTAMP>"),
    # Hex numbers often used for addresses/IDs
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<HEX>"),
    # Long sequences of digits (IDs, timings, etc)
    (re.compile(r"\b\d{5,}\b"), "<NUM>"),
]


def mask_message(message: str) -> str:
    """Mask dynamic content in a log message so it can be grouped with similar messages."""
    if not message:
        return ""
    
    masked = message
    for pattern, replacement in MASKS:
        masked = pattern.sub(replacement, masked)
    return masked


def generate_fingerprint(message: str) -> str:
    """Generate a short MD5 hash (fingerprint) of the masked message."""
    masked = mask_message(message)
    # Return first 12 chars of md5 hex digest
    return hashlib.md5(masked.encode("utf-8")).hexdigest()[:12]
