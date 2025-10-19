from __future__ import annotations

import re

PHONE_RE = re.compile(r"(\+?\d{2})(\d{2,5})(\d{2,4})")
EMAIL_RE = re.compile(r"([^@]{1,3})([^@]*)(@.*)")


def mask_phone(number: str | None) -> str | None:
    if not number:
        return number
    digits = re.sub(r"\D", "", number)
    match = PHONE_RE.match(digits)
    if not match:
        if len(digits) <= 4:
            return "*" * len(digits)
        return digits[:2] + "*" * max(len(digits) - 4, 0) + digits[-2:]
    prefix, middle, suffix = match.groups()
    masked_middle = "*" * len(middle)
    return f"{prefix}{masked_middle}{suffix}"


def mask_email(email: str | None) -> str | None:
    if not email:
        return email
    match = EMAIL_RE.match(email)
    if not match:
        return email
    start, middle, domain = match.groups()
    masked_middle = "*" * len(middle)
    if len(masked_middle) < 2:
        masked_middle = "***"
    return f"{start}{masked_middle}{domain}"


def mask_text(text: str) -> str:
    if not text:
        return text
    masked = PHONE_RE.sub(lambda m: f"{m.group(1)}{'*' * len(m.group(2))}{m.group(3)}", text)
    return masked


__all__ = ["mask_phone", "mask_email", "mask_text"]
