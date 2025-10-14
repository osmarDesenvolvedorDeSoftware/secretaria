from __future__ import annotations

from typing import Any, Tuple


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


def extract_number(payload: dict[str, Any]) -> str:
    """Extract and normalize the WhatsApp number from the payload."""
    candidates = [
        payload.get("from"),
        payload.get("number"),
        payload.get("remoteJid"),
        payload.get("key", {}).get("remoteJid"),
        payload.get("contact", {}).get("number"),
        payload.get("contact", {}).get("phone"),
        payload.get("ticket", {}).get("contact", {}).get("number"),
        payload.get("ticket", {}).get("contact", {}).get("phone"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        digits = "".join(ch for ch in str(candidate) if ch.isdigit())
        if not digits:
            continue
        if not digits.startswith("55"):
            digits = "55" + digits
        return digits
    raise ValueError("could not extract whatsapp number")


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
