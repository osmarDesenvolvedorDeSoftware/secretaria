from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import requests
import structlog
import yaml
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.metrics import (
    intention_distribution_total,
    satisfaction_ratio_gauge,
    sentiment_average_gauge,
)
from app.models import Conversation, CustomerContext, PersonalizationConfig
from app.services.tenancy import TenantContext

LOGGER = structlog.get_logger().bind(service="context_engine")

STOPWORDS = {
    "que",
    "para",
    "qual",
    "quais",
    "como",
    "onde",
    "quanto",
    "quando",
    "com",
    "dos",
    "das",
    "uma",
    "umas",
    "numa",
    "num",
    "esse",
    "essa",
    "isso",
    "esta",
    "este",
    "estou",
    "gostaria",
    "sobre",
    "pois",
    "pela",
    "pelo",
    "para",
    "perto",
    "aqui",
    "hoje",
    "amanha",
    "amanhã",
    "ontem",
    "favor",
}

POSITIVE_MARKERS = {
    "obrigado",
    "obrigada",
    "perfeito",
    "ótimo",
    "otimo",
    "excelente",
    "maravilha",
    "show",
    "🙏",
    "😄",
    "😊",
    "😀",
    "👍",
}

NEGATIVE_MARKERS = {
    "triste",
    "chateado",
    "chateada",
    "péssimo",
    "pessimo",
    "horrível",
    "horrivel",
    "raiva",
    "problema",
    "erro",
    "reclamação",
    "reclamacao",
    "😡",
    "😢",
    "😭",
    "👎",
    "urgente",
}

GREETING_WORDS = {"oi", "olá", "ola", "bom dia", "boa tarde", "boa noite", "eai", "ei"}
CLOSING_WORDS = {"obrigado", "obrigada", "valeu", "até mais", "ate mais", "até logo"}
URGENCY_WORDS = {"urgente", "agora", "imediato", "imediatamente", "socorro", "rápido", "rapido"}


@dataclass
class RuntimeContext:
    history: list[dict[str, str]]
    system_prompt: str
    template_vars: dict[str, str]
    profile: dict[str, Any]
    personalization: dict[str, Any]
    ai_enabled: bool
    sentiment: str = "neutral"
    sentiment_score: float = 0.0
    intention: str = "follow_up"
    template_name: str = "default"
    tone_profile: dict[str, Any] = field(default_factory=dict)
    feedback: str | None = None


class EmbeddingClient:
    def __init__(self) -> None:
        self.provider = (settings.embedding_provider or "gemini").lower()
        self.logger = structlog.get_logger().bind(service="embedding_client")

    def embed_text(self, text: str) -> list[float]:
        sanitized = text.strip()
        if not sanitized:
            return []

        if self.provider == "openai" and settings.openai_api_key:
            return self._openai_embedding(sanitized)
        if self.provider == "gemini" and settings.gemini_api_key:
            return self._gemini_embedding(sanitized)

        self.logger.debug(
            "embedding_fallback_hash",
            provider=self.provider,
            reason="missing_credentials",
        )
        return self._hash_embedding(sanitized)

    def _gemini_embedding(self, text: str) -> list[float]:
        try:
            response = requests.post(
                "https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent",
                params={"key": settings.gemini_api_key},
                json={"model": "text-embedding-004", "content": {"parts": [{"text": text}]}},
                timeout=settings.llm_timeout_seconds,
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            embedding = data.get("embedding", {}).get("values")
            if isinstance(embedding, list):
                return [float(x) for x in embedding]
        except Exception as exc:  # pragma: no cover - network errors
            self.logger.warning("gemini_embedding_failed", error=str(exc))
        return self._hash_embedding(text)

    def _openai_embedding(self, text: str) -> list[float]:
        try:
            response = requests.post(
                "https://api.openai.com/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={"input": text, "model": "text-embedding-3-small"},
                timeout=settings.llm_timeout_seconds,
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            items = data.get("data")
            if isinstance(items, list) and items:
                embedding = items[0].get("embedding")
                if isinstance(embedding, list):
                    return [float(x) for x in embedding]
        except Exception as exc:  # pragma: no cover - network errors
            self.logger.warning("openai_embedding_failed", error=str(exc))
        return self._hash_embedding(text)

    def _hash_embedding(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [round(byte / 255.0, 6) for byte in digest[:32]]


class ContextEngine:
    def __init__(
        self,
        redis_client: Redis,
        session_factory,
        tenant: TenantContext,
    ) -> None:
        self.redis = redis_client
        self.session_factory = session_factory
        self.tenant = tenant
        self.company_id = tenant.company_id
        self.company_label = tenant.label
        self.embedding_client = EmbeddingClient()
        self.templates = self._load_templates()

    # Utility helpers ------------------------------------------------------------------
    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"[\wáàâãéèêíóôõúç]+", text.lower())

    def _analyze_sentiment(self, text: str) -> tuple[str, float]:
        tokens = self._tokenize(text)
        score = 0.0
        for token in tokens:
            if token in POSITIVE_MARKERS:
                score += 1
            if token in NEGATIVE_MARKERS:
                score -= 1
        if any(marker in text for marker in POSITIVE_MARKERS):
            score += 0.5
        if any(marker in text for marker in NEGATIVE_MARKERS):
            score -= 0.5
        if score > 0.5:
            return "positive", min(score, 5.0)
        if score < -0.5:
            return "negative", max(score, -5.0)
        return "neutral", score

    def _detect_feedback(self, text: str) -> str | None:
        if "👍" in text or ":)" in text or "obrigado" in text.lower() or "obrigada" in text.lower():
            return "positive"
        if "👎" in text or ":(" in text or "nao gostei" in text.lower() or "não gostei" in text.lower():
            return "negative"
        return None

    def _detect_intention(self, text: str, history: Sequence[dict[str, str]]) -> str:
        sanitized = text.lower().strip()
        if not sanitized:
            return "follow_up"
        tokens = self._tokenize(sanitized)
        if any(word in sanitized for word in GREETING_WORDS):
            return "greeting"
        if any(word in sanitized for word in CLOSING_WORDS):
            return "closing"
        if any(word in sanitized for word in URGENCY_WORDS):
            return "urgency"
        if "?" in sanitized or any(token in ("como", "quando", "onde", "qual", "quais", "pode") for token in tokens):
            return "doubt"
        if tokens and len(tokens) <= 2 and tokens[0] in {"sim", "ok", "claro", "beleza", "manda"}:
            return "acknowledgement"

        if history:
            last_user = next((item for item in reversed(history) if item.get("role") == "user"), None)
            if last_user is not None:
                last_body = str(last_user.get("body", "")).lower()
                if last_body and sanitized in {"sim", "isso", "certo"}:
                    return "confirmation"
        return "follow_up"

    def _build_dialogue_summary(self, history: Sequence[dict[str, str]]) -> str:
        if not history:
            return ""
        snippets: list[str] = []
        for item in history[-6:]:
            role = item.get("role", "")
            body = str(item.get("body", ""))[:100].strip()
            if not body:
                continue
            prefix = "Cliente" if role == "user" else "Assistente"
            snippets.append(f"{prefix}: {body}")
        return " | ".join(snippets[-4:])

    def _build_tone_profile(self, config: dict[str, Any], sentiment: str) -> dict[str, Any]:
        tone = str(config.get("tone_of_voice") or "amigavel").lower()
        formality = config.get("formality_level", 50)
        empathy = config.get("empathy_level", 70)
        adaptive_humor = bool(config.get("adaptive_humor", True))
        try:
            formality_value = int(formality)
        except (TypeError, ValueError):
            formality_value = 50
        try:
            empathy_value = int(empathy)
        except (TypeError, ValueError):
            empathy_value = 70
        formality_value = max(0, min(100, formality_value))
        empathy_value = max(0, min(100, empathy_value))
        humor_enabled = adaptive_humor and sentiment != "negative"
        return {
            "tone": tone,
            "formality_level": formality_value,
            "empathy_level": empathy_value,
            "humor_enabled": bool(humor_enabled),
        }

    def _select_template_name(self, intention: str, sentiment: str) -> str:
        candidates: list[str] = []
        normalized_intention = intention or "follow_up"
        normalized_sentiment = sentiment or "neutral"
        if normalized_intention and normalized_intention != "follow_up":
            if normalized_sentiment != "neutral":
                candidates.append(f"{normalized_intention}_{normalized_sentiment}")
            candidates.append(normalized_intention)
        if normalized_sentiment != "neutral":
            candidates.append(f"sentiment_{normalized_sentiment}")
        candidates.append("default")
        for candidate in candidates:
            if candidate in self.templates:
                return candidate
        return "default"

    def _update_sentiment_metrics(self, number: str, score: float) -> None:
        key = self.tenant.namespaced_key("ctx", "sentiment", number)
        try:
            pipeline = self.redis.pipeline()
            pipeline.hincrbyfloat(key, "total", score)
            pipeline.hincrby(key, "count", 1)
            pipeline.expire(key, settings.context_ttl)
            results = pipeline.execute()
            total = float(results[0]) if results and results[0] is not None else float(score)
            count = int(results[1]) if len(results) > 1 and results[1] is not None else 1
            if count <= 0:
                count = 1
            sentiment_average_gauge.labels(company=self.company_label, number=number).set(
                total / count
            )
        except Exception:
            LOGGER.debug("sentiment_metrics_update_failed", number=number)

    def _update_feedback_metrics(self, number: str, feedback: str | None) -> None:
        if not feedback:
            return
        key = self.tenant.namespaced_key("ctx", "satisfaction", number)
        try:
            if feedback == "positive":
                self.redis.hincrby(key, "positive", 1)
            elif feedback == "negative":
                self.redis.hincrby(key, "negative", 1)
            self.redis.expire(key, settings.context_ttl)
            data = self.redis.hgetall(key) or {}
            positive_raw = data.get(b"positive") if isinstance(data, dict) else None
            negative_raw = data.get(b"negative") if isinstance(data, dict) else None
            positive = int(positive_raw.decode()) if isinstance(positive_raw, (bytes, bytearray)) else int(positive_raw or 0)
            negative = int(negative_raw.decode()) if isinstance(negative_raw, (bytes, bytearray)) else int(negative_raw or 0)
            total = positive + negative
            if total > 0:
                satisfaction_ratio_gauge.labels(
                    company=self.company_label,
                    number=number,
                ).set(positive / total)
        except Exception:
            LOGGER.debug("feedback_metrics_update_failed", number=number)

    def _register_intention_metric(self, intention: str) -> None:
        try:
            intention_distribution_total.labels(
                company=self.company_label,
                intention=intention or "follow_up",
            ).inc()
        except Exception:
            LOGGER.debug("intention_metric_update_failed", intention=intention)

    # Template handling -----------------------------------------------------------------
    def _load_templates(self) -> dict[str, dict[str, Any]]:
        template_path = (
            Path(__file__).resolve().parents[2] / "templates" / "response_templates.yaml"
        )
        if not template_path.exists():
            LOGGER.warning("response_templates_missing", path=str(template_path))
            return {}
        try:
            with template_path.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
                if isinstance(data, dict):
                    return data
        except Exception as exc:  # pragma: no cover - configuration errors
            LOGGER.error("response_templates_load_failed", error=str(exc))
        return {}

    def render_template(self, name: str, variables: dict[str, Any]) -> str:
        template = self.templates.get(name) or {}
        body = template.get("template")
        defaults = template.get("defaults") or {}
        if not isinstance(defaults, dict):
            defaults = {}
        payload = {**defaults, **variables}
        text = str(body or "{{resposta}}")

        def replace(match: re.Match[str]) -> str:
            key = match.group(1).strip()
            if key in payload:
                value = payload[key]
            elif key.lower() in payload:
                value = payload[key.lower()]
            elif key.replace("ú", "u") in payload:
                value = payload[key.replace("ú", "u")]
            else:
                value = ""
            return str(value)

        return re.sub(r"{{\s*([^{}]+)\s*}}", replace, text)

    def template_exists(self, name: str) -> bool:
        return name in self.templates

    # Session helpers -------------------------------------------------------------------
    def _session(self) -> Session:
        return self.session_factory()  # type: ignore[call-arg]

    def _close_session(self, session: Session) -> None:
        session.close()
        remove = getattr(self.session_factory, "remove", None)
        if callable(remove):
            remove()

    # Redis helpers ---------------------------------------------------------------------
    def _history_key(self, number: str) -> str:
        return self.tenant.namespaced_key("ctx", number)

    def _profile_key(self, number: str) -> str:
        return self.tenant.namespaced_key("ctx", "profile", number)

    def _config_key(self) -> str:
        return self.tenant.namespaced_key("ctx", "personalization_config")

    def _load_history(self, number: str) -> list[dict[str, str]]:
        raw = self.redis.get(self._history_key(number))
        if raw:
            try:
                payload = json.loads(raw)
                if isinstance(payload, list):
                    return [
                        {"role": str(item.get("role", "")), "body": str(item.get("body", ""))}
                        for item in payload
                        if isinstance(item, dict)
                    ]
            except json.JSONDecodeError:
                LOGGER.warning("invalid_history_cache", number=number)
        session = self._session()
        try:
            statement = (
                select(Conversation)
                .where(
                    Conversation.company_id == self.company_id,
                    Conversation.number == number,
                )
                .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            )
            result = session.execute(statement).scalars().first()
            if result and isinstance(result.context_json, list):
                return [
                    {
                        "role": str(item.get("role", "")),
                        "body": str(item.get("body", "")),
                    }
                    for item in result.context_json
                    if isinstance(item, dict)
                ]
        except Exception:
            return []
        finally:
            self._close_session(session)
        return []

    def _store_history(self, number: str, messages: Sequence[dict[str, str]], ttl: int) -> None:
        serialized = json.dumps(list(messages))
        self.redis.setex(self._history_key(number), ttl, serialized)

    def _load_profile(self, number: str) -> dict[str, Any]:
        cached = self.redis.get(self._profile_key(number))
        if cached:
            try:
                data = json.loads(cached)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                LOGGER.warning("invalid_profile_cache", number=number)
        session = self._session()
        data: dict[str, Any] | None = None
        try:
            try:
                profile = (
                    session.query(CustomerContext)
                    .filter(
                        CustomerContext.company_id == self.company_id,
                        CustomerContext.number == number,
                    )
                    .order_by(CustomerContext.id.asc())
                    .first()
                )
            except Exception:
                profile = None
                data = self._default_profile(number)
            if data is None:
                if profile is None:
                    profile = CustomerContext(
                        company_id=self.company_id,
                        number=number,
                        frequent_topics=[],
                        product_mentions=[],
                        preferences={},
                    )
                    session.add(profile)
                    session.commit()
                data = profile.to_dict()
        except Exception:
            session.rollback()
            data = self._default_profile(number)
        finally:
            self._close_session(session)
        serialized = self._serialize_profile_dict(data)
        self._store_profile(number, serialized)
        return serialized

    def _store_profile(self, number: str, payload: dict[str, Any]) -> None:
        serialized = json.dumps(self._serialize_profile_dict(payload))
        self.redis.setex(self._profile_key(number), settings.context_ttl, serialized)

    def _load_config(self) -> dict[str, Any]:
        cached = self.redis.get(self._config_key())
        if cached:
            try:
                data = json.loads(cached)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                LOGGER.warning("invalid_config_cache")
        session = self._session()
        data: dict[str, Any] | None = None
        try:
            try:
                config = (
                    session.query(PersonalizationConfig)
                    .filter(PersonalizationConfig.company_id == self.company_id)
                    .order_by(PersonalizationConfig.updated_at.desc().nullslast(), PersonalizationConfig.id.asc())
                    .first()
                )
            except Exception:
                config = None
                data = self._default_config()
            if data is None:
                if config is None:
                    config = PersonalizationConfig(company_id=self.company_id)
                    session.add(config)
                    session.commit()
                data = config.to_dict()
        except Exception:
            session.rollback()
            data = self._default_config()
        finally:
            self._close_session(session)
        serialized = self._serialize_config_dict(data)
        try:
            self.redis.setex(self._config_key(), settings.context_ttl, json.dumps(serialized))
        except Exception:
            pass
        return serialized

    def _serialize_profile_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        payload = dict(data)
        for field_name in ("created_at", "updated_at"):
            value = payload.get(field_name)
            if value is not None:
                payload[field_name] = str(value)
        return payload

    def _serialize_config_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        payload = dict(data)
        for field_name in ("created_at", "updated_at"):
            value = payload.get(field_name)
            if value is not None:
                payload[field_name] = str(value)
        return payload

    def _default_profile(self, number: str) -> dict[str, Any]:
        return {
            "number": number,
            "frequent_topics": [],
            "product_mentions": [],
            "preferences": {},
            "embedding": None,
            "last_subject": None,
        }

    def _default_config(self) -> dict[str, Any]:
        return {
            "tone_of_voice": "amigavel",
            "message_limit": settings.context_max_messages,
            "opening_phrases": [],
            "ai_enabled": True,
            "formality_level": 50,
            "empathy_level": 70,
            "adaptive_humor": True,
        }

    # Public API ------------------------------------------------------------------------
    def get_history(self, number: str) -> list[dict[str, str]]:
        return self._load_history(number)

    def save_history(self, number: str, messages: Sequence[dict[str, str]]) -> list[dict[str, str]]:
        config = self._load_config()
        limit = int(config.get("message_limit") or settings.context_max_messages)
        if limit <= 0:
            limit = settings.context_max_messages
        trimmed = list(messages)[-limit:]
        self._store_history(number, trimmed, settings.context_ttl)
        return trimmed

    def prepare_runtime_context(self, number: str, user_message: str) -> RuntimeContext:
        profile = self._load_profile(number)
        config = self._load_config()
        history = self._load_history(number)

        limit = int(config.get("message_limit") or settings.context_max_messages)
        if limit <= 0:
            limit = settings.context_max_messages
        trimmed_history = history[-limit:]

        dialogue_summary = self._build_dialogue_summary(trimmed_history)
        sentiment_label, sentiment_score = self._analyze_sentiment(user_message)
        intention = self._detect_intention(user_message, trimmed_history)
        feedback = self._detect_feedback(user_message)

        self._update_sentiment_metrics(number, sentiment_score)
        self._update_feedback_metrics(number, feedback)
        self._register_intention_metric(intention)

        last_subject = profile.get("last_subject") or user_message[:80]
        preferences = profile.get("preferences") or {}
        nome = preferences.get("nome") or preferences.get("name") or "cliente"
        saudacao = None
        phrases = config.get("opening_phrases") or []
        if isinstance(phrases, list) and phrases:
            saudacao = phrases[0]
        tone_profile = self._build_tone_profile(config, sentiment_label)
        tone = tone_profile.get("tone", "amigavel")

        frequent_topics = profile.get("frequent_topics") or []
        if isinstance(frequent_topics, list):
            topics_text = ", ".join(str(topic) for topic in frequent_topics[:5])
        else:
            topics_text = ""
        product_mentions = profile.get("product_mentions") or []
        product = product_mentions[0] if isinstance(product_mentions, list) and product_mentions else ""
        product_phrase = f" Temos registrado interesse em {product}." if product else ""
        subject_phrase = f" Último assunto: {last_subject}." if last_subject else ""

        system_prompt_parts = [
            "Você é uma assistente virtual da Secretaria Virtual.",
            f"Use um tom {tone} ao responder.",
            f"O cliente se chama {nome}.",
            f"Nível de formalidade esperado: {tone_profile['formality_level']}/100.",
            f"Grau de empatia esperado: {tone_profile['empathy_level']}/100.",
        ]
        if topics_text:
            system_prompt_parts.append(
                f"Temas recorrentes deste cliente: {topics_text}."
            )
        if product:
            system_prompt_parts.append(f"Produtos de interesse recentes: {product}.")
        if last_subject:
            system_prompt_parts.append(
                f"Último assunto tratado: {last_subject}."
            )
        if dialogue_summary:
            system_prompt_parts.append(
                f"Resumo recente da conversa: {dialogue_summary}. Garanta continuidade do mesmo assunto."
            )
        if sentiment_label == "negative":
            system_prompt_parts.append(
                "O cliente demonstra frustração. Responda com empatia, valide o sentimento e ofereça próximos passos claros."
            )
        elif sentiment_label == "positive":
            system_prompt_parts.append(
                "O cliente está receptivo. Seja entusiasmada sem perder a clareza."
            )
        if tone_profile.get("humor_enabled"):
            system_prompt_parts.append(
                "Humor leve é permitido quando apropriado, mas jamais minimize problemas do cliente."
            )
        system_prompt_parts.append(
            "Se não compreender a mensagem, ofereça ajuda humana de forma empática."
        )
        system_prompt_parts.append(
            f"Intenção atual detectada: {intention}. Ajuste o fluxo da conversa a partir disso."
        )
        system_prompt = " ".join(system_prompt_parts)

        contexto_recente = f" Contexto recente: {dialogue_summary}." if dialogue_summary else ""
        empatia_texto = ""
        if sentiment_label == "negative":
            empatia_texto = "Sinto muito pelo transtorno. "
        elif sentiment_label == "positive":
            empatia_texto = "Que ótima notícia! "

        humor_extra = ""
        if tone_profile.get("humor_enabled") and sentiment_label == "positive":
            humor_extra = " 😄"

        template_vars: dict[str, str] = {
            "nome": str(nome),
            "produto": product_phrase,
            "ultimo_assunto": subject_phrase,
            "último_assunto": subject_phrase,
            "saudacao": saudacao or "Olá",
            "resposta": "",
            "transferencia": settings.transfer_to_human_message,
            "tom": str(tone),
            "contexto_recente": contexto_recente,
            "empatia_texto": empatia_texto,
            "humor_extra": humor_extra,
            "sentimento": sentiment_label,
            "intencao": intention,
            "grau_formalidade": str(tone_profile["formality_level"]),
            "grau_empatia": str(tone_profile["empathy_level"]),
            "humor_ativo": "sim" if tone_profile.get("humor_enabled") else "não",
        }
        if saudacao:
            template_vars["frase_inicial"] = saudacao
        template_vars["numero"] = number

        return RuntimeContext(
            history=trimmed_history,
            system_prompt=system_prompt,
            template_vars=template_vars,
            profile=profile,
            personalization=config,
            ai_enabled=bool(config.get("ai_enabled", True)),
            sentiment=sentiment_label,
            sentiment_score=sentiment_score,
            intention=intention,
            template_name=self._select_template_name(intention, sentiment_label),
            tone_profile=tone_profile,
            feedback=feedback,
        )

    def build_llm_context(self, runtime_context: RuntimeContext) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [{"role": "system", "body": runtime_context.system_prompt}]
        contexto = runtime_context.template_vars.get("contexto_recente") if runtime_context.template_vars else ""
        if contexto:
            messages.append({"role": "system", "body": contexto.strip()})
        messages.extend(runtime_context.history)
        return messages

    def record_history(
        self,
        number: str,
        history: list[dict[str, str]],
        user_message: str,
        assistant_message: str,
        personalization: dict[str, Any],
    ) -> list[dict[str, str]]:
        limit = int(personalization.get("message_limit") or settings.context_max_messages)
        if limit <= 0:
            limit = settings.context_max_messages
        new_history = list(history)
        new_history.append({"role": "user", "body": user_message})
        new_history.append({"role": "assistant", "body": assistant_message})
        trimmed = new_history[-limit:]
        self._store_history(number, trimmed, settings.context_ttl)
        return trimmed

    def update_profile_snapshot(self, number: str, user_message: str, profile: dict[str, Any]) -> dict[str, Any]:
        profile_data = dict(profile)
        profile_data["last_subject"] = user_message[:120]
        preferences = dict(profile_data.get("preferences") or {})
        preferences["ultimo_assunto"] = user_message[:120]
        profile_data["preferences"] = preferences

        session = self._session()
        refreshed: dict[str, Any] = profile_data
        try:
            try:
                record = (
                    session.query(CustomerContext)
                    .filter(
                        CustomerContext.company_id == self.company_id,
                        CustomerContext.number == number,
                    )
                    .first()
                )
            except Exception:
                record = None
            if record is None:
                try:
                    record = CustomerContext(company_id=self.company_id, number=number)
                    session.add(record)
                except Exception:
                    record = None
            if record is not None:
                record.company_id = self.company_id
                record.last_subject = profile_data["last_subject"]
                record.preferences = preferences
                session.add(record)
                session.commit()
                refreshed = record.to_dict()
        except Exception:
            session.rollback()
            refreshed = profile_data
        finally:
            self._close_session(session)
        self._store_profile(number, refreshed)
        return refreshed

    # Training --------------------------------------------------------------------------
    def retrain_profile(
        self,
        number: str,
        messages: Iterable[dict[str, Any]],
        user_name: str | None = None,
    ) -> dict[str, Any]:
        text_chunks: list[str] = []
        user_messages: list[str] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            body = str(item.get("body", "")).strip()
            role = str(item.get("role", ""))
            if not body:
                continue
            text_chunks.append(body)
            if role == "user":
                user_messages.append(body)

        topics = self._extract_topics(user_messages)
        products = self._extract_products(user_messages)
        preferences = self._extract_preferences(user_messages, user_name)
        embedding_text = "\n".join(text_chunks)
        embedding = self.embedding_client.embed_text(embedding_text)
        last_subject = user_messages[-1] if user_messages else ""
        if last_subject:
            preferences["ultimo_assunto"] = last_subject

        session = self._session()
        try:
            record = (
                session.query(CustomerContext)
                .filter(
                    CustomerContext.company_id == self.company_id,
                    CustomerContext.number == number,
                )
                .first()
            )
            if record is None:
                record = CustomerContext(company_id=self.company_id, number=number)
                session.add(record)
            record.frequent_topics = topics
            record.product_mentions = products
            record.preferences = preferences
            record.embedding = embedding
            record.last_subject = last_subject[:255] if last_subject else None
            session.add(record)
            session.commit()
            payload = record.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            self._close_session(session)
        self._store_profile(number, payload)
        return payload

    def _extract_topics(self, messages: Sequence[str]) -> list[str]:
        counter: Counter[str] = Counter()
        for message in messages:
            tokens = re.findall(r"[\wáàâãéèêíóôõúç]+", message.lower())
            for token in tokens:
                if len(token) < 4:
                    continue
                if token in STOPWORDS:
                    continue
                counter[token] += 1
        return [token for token, _ in counter.most_common(5)]

    def _extract_products(self, messages: Sequence[str]) -> list[str]:
        products: list[str] = []
        for message in messages:
            tokens = re.findall(r"[\wáàâãéèêíóôõúç]+", message.lower())
            for idx, token in enumerate(tokens):
                if token.startswith("produt") and idx + 1 < len(tokens):
                    candidate = tokens[idx + 1]
                    if candidate not in STOPWORDS and candidate not in products:
                        products.append(candidate)
        return products[:5]

    def _extract_preferences(self, messages: Sequence[str], user_name: str | None) -> dict[str, Any]:
        preferences: dict[str, Any] = {}
        if user_name:
            preferences["nome"] = user_name
        total_messages = len(messages)
        if total_messages:
            preferences["mensagens_usuario"] = total_messages
        if messages:
            preferences["ultimo_assunto"] = messages[-1]
        return preferences
