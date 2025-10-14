from __future__ import annotations

from app.services.security import sanitize_for_log


def test_sanitize_for_log_masks_tokens():
    original = "Authorization: Bearer abcdef123456 token=XYZ987 apiKey='secret-key'"
    sanitized = sanitize_for_log(original)
    assert "Authorization: ***" in sanitized
    assert "token=***" in sanitized
    assert "apiKey='***'" in sanitized
    assert "abcdef123456" not in sanitized
