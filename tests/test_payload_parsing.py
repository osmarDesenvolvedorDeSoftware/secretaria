from __future__ import annotations

import pytest

from app.services.payload import extract_number, extract_text_and_kind


@pytest.mark.parametrize(
    "payload,expected_text,expected_kind",
    [
        (
            {
                "message": {"conversation": "Olá"},
                "key": {"remoteJid": "5511999999999@s.whatsapp.net"},
            },
            "Olá",
            "text",
        ),
        (
            {
                "message": {"extendedTextMessage": {"text": "Detalhado"}},
                "from": "5511888877777",
            },
            "Detalhado",
            "text",
        ),
        (
            {
                "message": {
                    "ephemeralMessage": {"message": {"conversation": "Segredo"}}
                },
                "number": "5511987654321",
            },
            "Segredo",
            "text",
        ),
        (
            {
                "message": {
                    "buttonsResponseMessage": {
                        "selectedDisplayText": "Botão 1",
                        "selectedButtonId": "BTN1",
                    }
                },
                "number": "5511777888999",
            },
            "Botão 1",
            "interactive",
        ),
        (
            {
                "message": {
                    "listResponseMessage": {
                        "title": "Lista",
                        "singleSelectReply": {"selectedRowId": "row1"},
                    }
                },
                "ticket": {"contact": {"number": "551166665555"}},
            },
            "Lista",
            "interactive",
        ),
        (
            {
                "message": {
                    "templateMessage": {
                        "hydratedTemplate": {"hydratedContentText": "Template"}
                    }
                },
                "contact": {"phone": "551155554444"},
            },
            "Template",
            "template",
        ),
        (
            {
                "message": {
                    "interactiveResponseMessage": {
                        "result": {"paramsJson": {"id": "123", "title": "Escolha"}}
                    }
                },
                "number": "551144443333",
            },
            "123",
            "interactive",
        ),
        (
            {
                "message": {"imageMessage": {"caption": "Foto"}},
                "from": "551133332222",
            },
            "Foto",
            "media",
        ),
    ],
)
def test_extract_text_and_kind(payload, expected_text, expected_kind):
    text, kind = extract_text_and_kind(payload)
    assert text == expected_text
    assert kind == expected_kind
    assert extract_number(payload).startswith("55")


def test_extract_text_from_messages_array():
    payload = {
        "messages": [
            {
                "message": {
                    "videoMessage": {
                        "fileName": "demo.mp4",
                    }
                }
            }
        ],
        "number": "5511999988888",
    }

    text, kind = extract_text_and_kind(payload)

    assert text == "demo.mp4"
    assert kind == "media"


def test_extract_text_from_raw_message_string():
    payload = {
        "messages": [
            {
                "message": "Mensagem direta",
            }
        ],
        "number": "5511888877776",
    }

    text, kind = extract_text_and_kind(payload)

    assert text == "Mensagem direta"
    assert kind == "text"


def test_extract_text_from_fallback_keys():
    payload = {
        "caption": "Legenda prioritária",
        "contact": {"phone": "1188776655"},
    }

    text, kind = extract_text_and_kind(payload)

    assert text == "Legenda prioritária"
    assert kind == "text"


def test_extract_text_from_native_flow_interactive():
    payload = {
        "message": {
            "interactiveResponseMessage": {
                "result": {"paramsJson": None},
                "nativeFlowResponseMessage": {
                    "messageParamsJson": {"id": "flow-123"}
                },
            }
        },
        "ticket": {"contact": {"number": "551177665544"}},
    }

    text, kind = extract_text_and_kind(payload)

    assert text == "flow-123"
    assert kind == "interactive"


def test_extract_text_from_template_buttons():
    payload = {
        "message": {
            "templateMessage": {
                "hydratedTemplate": {
                    "buttons": [
                        {"buttonId": "BTN-42", "displayText": "Escolher"},
                    ]
                }
            }
        },
        "from": "551155554433",
    }

    text, kind = extract_text_and_kind(payload)

    assert text == "BTN-42"
    assert kind == "template"


def test_extract_number_returns_none_on_missing_data():
    assert extract_number({}) is None


def test_extract_number_prefers_remote_jid_alt_over_lid():
    payload = {
        "key": {
            "remoteJid": "122582745514119@lid",
            "remoteJidAlt": "5516988648203@s.whatsapp.net",
        }
    }

    assert extract_number(payload) == "5516988648203"


def test_extract_number_uses_participant_for_groups():
    payload = {
        "key": {
            "remoteJid": "5516999999999-123@g.us",
            "participant": "5516998888888@s.whatsapp.net",
        }
    }

    assert extract_number(payload) == "5516998888888"


def test_extract_number_uses_participant_for_broadcast():
    payload = {
        "key": {
            "remoteJid": "status@broadcast",
            "participant": "5516997777777@s.whatsapp.net",
        }
    }

    assert extract_number(payload) == "5516997777777"


def test_extract_number_uses_regex_fallback():
    payload = {
        "message": {"conversation": "Oi"},
        "meta": {"jid": "5516988888888@s.whatsapp.net"},
    }

    assert extract_number(payload) == "5516988888888"
