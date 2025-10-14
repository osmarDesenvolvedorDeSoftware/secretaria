from __future__ import annotations

import json
from typing import Optional

import requests
import structlog
from redis import Redis
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from app.config import settings
from app.services.security import sanitize_for_log


class WhaticketError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self._raw_message = message
        self._sanitized = sanitize_for_log(message)

    def __str__(self) -> str:  # pragma: no cover - exercised via logging
        return self._sanitized

    @property
    def raw_message(self) -> str:
        return self._raw_message


class WhaticketClient:
    def __init__(self, redis_client: Redis) -> None:
        self.redis = redis_client
        self.logger = structlog.get_logger().bind(service="whaticket")

    def _get_headers(self) -> dict[str, str]:
        token = self._get_auth_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _get_auth_token(self) -> str:
        if settings.enable_jwt_login:
            cached = self.redis.get("whaticket:jwt")
            if cached:
                return cached
            data = {
                "email": settings.whaticket_jwt_email,
                "password": settings.whaticket_jwt_password,
            }
            try:
                response = requests.post(
                    "{}/auth/login".format(settings.whatsapp_api_url.rsplit("/api/messages/send", 1)[0]),
                    json=data,
                    timeout=settings.request_timeout_seconds,
                )
            except requests.RequestException as exc:  # pragma: no cover - network safety
                raise WhaticketError("Failed to authenticate via JWT", retryable=True) from exc
            if response.status_code != 200:
                raise WhaticketError("Failed to authenticate via JWT", retryable=False, status=response.status_code)
            payload = response.json()
            token = payload.get("token")
            expires_in = payload.get("expiresIn", 3600)
            if not token:
                raise WhaticketError("Token ausente na resposta de login", retryable=False)
            ttl = max(int(expires_in) - 60, 300)
            self.redis.setex("whaticket:jwt", ttl, token)
            return token
        return settings.whatsapp_bearer_token

    def _post(self, payload: dict[str, object]) -> requests.Response:
        try:
            response = requests.post(
                settings.whatsapp_api_url,
                headers=self._get_headers(),
                json=payload,
                timeout=settings.request_timeout_seconds,
            )
        except requests.RequestException as exc:
            error = sanitize_for_log(str(exc))
            self.logger.exception("whaticket_request_failed", error=error, retryable=True)
            raise WhaticketError("Network error", retryable=True) from exc

        if response.status_code >= 500:
            body_preview = sanitize_for_log(response.text[:256])
            self.logger.error(
                "whaticket_server_error",
                status=response.status_code,
                body=body_preview,
            )
            raise WhaticketError(
                f"Server error: {response.status_code}",
                retryable=True,
                status=response.status_code,
            )
        if response.status_code >= 400:
            body_preview = sanitize_for_log(response.text[:256])
            self.logger.warning(
                "whaticket_client_error",
                status=response.status_code,
                body=body_preview,
            )
            raise WhaticketError(
                f"Client error: {response.status_code}",
                retryable=False,
                status=response.status_code,
            )

        return response

    def _parse_response(self, response: requests.Response) -> Optional[str]:
        try:
            data = response.json()
        except json.JSONDecodeError:
            return response.text or None
        return data.get("id")

    @retry(
        stop=stop_after_attempt(settings.whaticket_retry_attempts),
        wait=wait_random_exponential(multiplier=settings.whaticket_retry_backoff_seconds, max=60),
        retry=retry_if_exception_type(WhaticketError),
        reraise=True,
    )
    def send_text(self, number: str, body: str) -> Optional[str]:
        payload = {
            "number": number,
            "body": body,
        }
        response = self._post(payload)
        message_id = self._parse_response(response)
        self.logger.info("whaticket_text_sent", number=number, has_id=bool(message_id))
        return message_id

    @retry(
        stop=stop_after_attempt(settings.whaticket_retry_attempts),
        wait=wait_random_exponential(multiplier=settings.whaticket_retry_backoff_seconds, max=60),
        retry=retry_if_exception_type(WhaticketError),
        reraise=True,
    )
    def send_media(
        self,
        number: str,
        media_url: str,
        *,
        caption: str | None = None,
        media_type: str = "image",
    ) -> Optional[str]:
        payload = {
            "number": number,
            "body": caption or "",
            "mediaUrl": media_url,
            "mediaType": media_type,
        }
        response = self._post(payload)
        message_id = self._parse_response(response)
        self.logger.info("whaticket_media_sent", number=number, media_type=media_type, has_id=bool(message_id))
        return message_id
