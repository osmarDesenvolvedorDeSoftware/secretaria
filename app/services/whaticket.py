from __future__ import annotations

import json
from typing import Optional

import requests
from redis import Redis
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from app.config import settings


class WhaticketError(Exception):
    pass


class WhaticketClient:
    def __init__(self, redis_client: Redis) -> None:
        self.redis = redis_client

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
            response = requests.post(
                "{}/auth/login".format(settings.whatsapp_api_url.rsplit("/api/messages/send", 1)[0]),
                json=data,
                timeout=settings.request_timeout_seconds,
            )
            if response.status_code != 200:
                raise WhaticketError("Failed to authenticate via JWT")
            payload = response.json()
            token = payload.get("token")
            expires_in = payload.get("expiresIn", 3600)
            if not token:
                raise WhaticketError("Token ausente na resposta de login")
            ttl = max(int(expires_in) - 60, 300)
            self.redis.setex("whaticket:jwt", ttl, token)
            return token
        return settings.whatsapp_bearer_token

    @retry(
        stop=stop_after_attempt(settings.whaticket_retry_attempts),
        wait=wait_random_exponential(multiplier=settings.whaticket_retry_backoff_seconds, max=60),
        retry=retry_if_exception_type(WhaticketError),
        reraise=True,
    )
    def send_message(self, number: str, body: str) -> Optional[str]:
        payload = {
            "number": number,
            "body": body,
        }
        response = requests.post(
            settings.whatsapp_api_url,
            headers=self._get_headers(),
            json=payload,
            timeout=settings.request_timeout_seconds,
        )
        if response.status_code >= 400:
            raise WhaticketError(f"Whaticket request failed: {response.status_code} {response.text}")
        try:
            data = response.json()
        except json.JSONDecodeError:
            data = {}
        return data.get("id")
