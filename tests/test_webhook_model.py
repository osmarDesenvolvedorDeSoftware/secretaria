from __future__ import annotations

import json

import pytest

from app.routes.webhook import IncomingWebhook


def test_incoming_webhook_parse_raw_body():
    payload = {
        "message": {"conversation": "Oi"},
        "ticket": {"contact": {"number": "11999998888"}},
    }
    model = IncomingWebhook.parse_raw_body(json.dumps(payload).encode())
    assert model.number == "5511999998888"
    assert model.text == "Oi"
    assert model.kind == "text"


def test_incoming_webhook_validates_number_prefix():
    data = {"number": "551188887777", "text": "Olá", "kind": "text"}
    model = IncomingWebhook.from_payload(data)
    assert model.number == "551188887777"


@pytest.mark.parametrize(
    "raw_number",
    ["", "abc", "++"],
)
def test_incoming_webhook_invalid_number(raw_number):
    with pytest.raises(Exception):
        IncomingWebhook.from_payload({"number": raw_number, "text": "oi", "kind": "text"})
