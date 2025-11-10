from __future__ import annotations

import json
import logging
import re
from typing import Any, Tuple

import structlog


_MESSAGE_KINDS = {
    "conversation": "text",
    "extendedTextMessage": "text",
    "buttonsResponseMessage": "interactive",
    "listResponseMessage": "interactive",
    "templateMessage": "template",
    "interactiveResponseMessage": "interactive",
    "imageMessage": "media",
    "videoMessage": "media",
    "documentMessage": "media",
}

logger = logging.getLogger(__name__)

_KNOWN_SUFFIXES = (
    "@s.whatsapp.net",
    "@lid",
    "@g.us",
    "@broadcast",
)
_DISALLOWED_SUFFIXES = {"@g.us", "@broadcast"}


def extract_number(payload: dict[str, Any]) -> str | None:
    """Extract and normalize the WhatsApp number from the payload.

    The function inspects Whaticket webhook payloads and tries to identify the
    correct WhatsApp number regardless of the transport format (lid, pn, group,
    broadcast, proto payloads, etc.).
    """

    struct_logger = structlog.get_logger("extract_number")

    key_value = payload.get("key")
    key_payload = key_value if isinstance(key_value, dict) else {}
    fields_to_check = ["remoteJid", "remoteJidAlt", "participant"]

    def _normalize(field_name: str, raw_value: Any) -> tuple[str | None, str | None]:
        if raw_value is None:
            return None, None

        value = str(raw_value)
        if not any(suffix in value for suffix in _KNOWN_SUFFIXES):
            struct_logger.debug(
                "extract_number_skip",
                field=field_name,
                raw_value=value,
                reason="missing_suffix",
                valid=False,
            )
            return None, value

        main_part = value.split("@", 1)[0]
        number = re.sub(r"\D", "", main_part)

        if not number:
            struct_logger.debug(
                "extract_number_invalid",
                field=field_name,
                raw_value=value,
                normalized=number,
                reason="no_digits",
                valid=False,
            )
            return None, value

        suffix = next((s for s in _KNOWN_SUFFIXES if value.endswith(s)), None)
        if suffix in _DISALLOWED_SUFFIXES:
            struct_logger.debug(
                "extract_number_invalid",
                field=field_name,
                raw_value=value,
                normalized=number,
                reason="disallowed_suffix",
                valid=False,
            )
            return None, value

        if suffix != "@s.whatsapp.net":
            struct_logger.debug(
                "extract_number_invalid",
                field=field_name,
                raw_value=value,
                normalized=number,
                reason="unsupported_suffix",
                valid=False,
            )
            return None, value

        if len(number) < 11:
            struct_logger.debug(
                "extract_number_invalid",
                field=field_name,
                raw_value=value,
                normalized=number,
                reason="too_short",
                valid=False,
            )
            return None, value

        struct_logger.debug(
            "extract_number_success",
            field=field_name,
            raw_value=value,
            normalized=number,
            valid=True,
        )
        return number, value

    for field in fields_to_check:
        raw_value = key_payload.get(field)
        number, _ = _normalize(field, raw_value)
        if number:
            return number

    contact_value = payload.get("contact") if isinstance(payload.get("contact"), dict) else None
    ticket_value = payload.get("ticket") if isinstance(payload.get("ticket"), dict) else None
    ticket_contact = (
        ticket_value.get("contact") if isinstance(ticket_value.get("contact"), dict) else None
    ) if ticket_value else None

    fallback_candidates: list[tuple[str, Any]] = [
        ("number", payload.get("number")),
        ("from", payload.get("from")),
        ("contact.number", contact_value.get("number") if contact_value else None),
        ("contact.phone", contact_value.get("phone") if contact_value else None),
        (
            "ticket.contact.number",
            ticket_contact.get("number") if ticket_contact else None,
        ),
        (
            "ticket.contact.phone",
            ticket_contact.get("phone") if ticket_contact else None,
        ),
    ]

    for field_name, raw_value in fallback_candidates:
        if raw_value is None:
            continue
        value = str(raw_value)
        digits = re.sub(r"\D", "", value)
        if not digits:
            struct_logger.debug(
                "extract_number_invalid",
                field=field_name,
                raw_value=value,
                normalized=digits,
                reason="no_digits",
                valid=False,
            )
            continue
        if len(digits) < 11:
            struct_logger.debug(
                "extract_number_invalid",
                field=field_name,
                raw_value=value,
                normalized=digits,
                reason="too_short",
                valid=False,
            )
            continue
        struct_logger.debug(
            "extract_number_success",
            field=field_name,
            raw_value=value,
            normalized=digits,
            valid=True,
        )
        return digits

    # Fallback: search through the entire payload for a valid identifier.
    payload_text = json.dumps(payload, ensure_ascii=False)
    for match in re.finditer(r"(\d{11,})@(s\.whatsapp\.net|lid|g\.us|broadcast)", payload_text):
        digits, suffix = match.groups()
        raw_value = f"{digits}@{suffix}"
        number, _ = _normalize("regex_fallback", raw_value)
        if number:
            return number

    struct_logger.debug(
        "extract_number_failed",
        payload_preview=str(payload)[:500],
    )
    return None


def extract_text_and_kind(payload: dict[str, Any]) -> Tuple[str, str]:
    """Extract textual content and a normalized message kind from a webhook payload."""

    def _extract_from_message(message: Any) -> Tuple[str, str]:
        if not isinstance(message, dict):
            return str(message or ""), "text"

        if "ephemeralMessage" in message:
            inner = message.get("ephemeralMessage", {}).get("message", {})
            return _extract_from_message(inner)

        for media_key in ("imageMessage", "videoMessage", "documentMessage"):
            if media_key in message:
                media = message[media_key] or {}
                text = media.get("caption") or media.get("fileName") or ""
                return str(text or ""), _MESSAGE_KINDS[media_key]

        if "conversation" in message:
            return str(message.get("conversation", "")), _MESSAGE_KINDS["conversation"]

        if "extendedTextMessage" in message:
            extended = message.get("extendedTextMessage", {})
            text = extended.get("text") or extended.get("caption") or ""
            return str(text or ""), _MESSAGE_KINDS["extendedTextMessage"]

        if "buttonsResponseMessage" in message:
            buttons = message.get("buttonsResponseMessage", {})
            text = buttons.get("selectedDisplayText") or buttons.get("selectedButtonId") or ""
            return str(text or ""), _MESSAGE_KINDS["buttonsResponseMessage"]

        if "listResponseMessage" in message:
            list_msg = message.get("listResponseMessage", {})
            single = list_msg.get("singleSelectReply") or {}
            text = (
                list_msg.get("title")
                or list_msg.get("description")
                or single.get("selectedRowId")
                or single.get("selectedText")
                or ""
            )
            return str(text or ""), _MESSAGE_KINDS["listResponseMessage"]

        if "interactiveResponseMessage" in message:
            interactive = message.get("interactiveResponseMessage", {})
            result = interactive.get("result") or {}
            params = result.get("paramsJson")
            if isinstance(params, str):
                text = params
            elif isinstance(params, dict):
                text = params.get("id") or params.get("title") or params.get("description") or ""
            else:
                text = (
                    interactive.get("nativeFlowResponseMessage", {})
                    .get("messageParamsJson", {})
                    .get("id", "")
                )
            text = text or interactive.get("body") or interactive.get("id") or ""
            return str(text or ""), _MESSAGE_KINDS["interactiveResponseMessage"]

        if "templateMessage" in message:
            template = message.get("templateMessage", {})
            hydrated = template.get("hydratedTemplate") or {}
            text = (
                hydrated.get("hydratedContentText")
                or hydrated.get("contentText")
                or hydrated.get("bodyText")
                or template.get("contentText")
                or ""
            )
            if not text:
                buttons = hydrated.get("buttons") or []
                if buttons:
                    text = buttons[0].get("buttonId") or buttons[0].get("displayText") or ""
            return str(text or ""), _MESSAGE_KINDS["templateMessage"]

        if "message" in message and isinstance(message["message"], dict):
            return _extract_from_message(message["message"])

        # fall back to plain text in common body keys
        for key in ("text", "body", "caption", "content"):
            if key in message and message[key]:
                return str(message[key]), "text"

        return "", "text"

    message = payload.get("message")
    if isinstance(message, list) and message:
        message = message[0]
    if isinstance(message, dict):
        text, kind = _extract_from_message(message)
        if text or kind != "text":
            return text, kind
    if "messages" in payload and isinstance(payload["messages"], list) and payload["messages"]:
        nested_message = payload["messages"][0].get("message")
        text, kind = _extract_from_message(nested_message)
        if text or kind != "text":
            return text, kind

    fallback_keys = ["text", "body", "caption"]
    for key in fallback_keys:
        if payload.get(key):
            return str(payload[key]), "text"

    return "", "text"
