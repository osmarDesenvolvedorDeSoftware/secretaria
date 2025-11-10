from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal


Sentiment = Literal["positivo", "negativo", "neutro"]


@dataclass(frozen=True)
class FeedbackAnalysis:
    """Structured representation for follow-up feedback summaries."""

    sentiment: Sentiment
    summary: str

    def format(self) -> str:
        return f"Sentimento: {self.sentiment}. Resumo: {self.summary}".strip()


_POSITIVE_KEYWORDS = {"bom", "boa", "ótimo", "excelente", "maravilhoso", "gostei", "amei", "positivo", "perfeito"}
_NEGATIVE_KEYWORDS = {"ruim", "péssimo", "horrível", "terrível", "negativo", "odiei", "péssima", "pior", "insuportável"}


def _detect_sentiment(text: str) -> Sentiment:
    normalized = re.sub(r"[^\w\s]", "", text.lower())
    tokens = set(normalized.split())
    if tokens & _POSITIVE_KEYWORDS:
        return "positivo"
    if tokens & _NEGATIVE_KEYWORDS or "não" in tokens:
        return "negativo"
    return "neutro"


def analyze_feedback(feedback: str) -> str:
    """Analyze free-form follow-up feedback and return a concise summary string.

    In production this module would leverage Gemini to classify and summarize
    customer remarks. For local validation we rely on lightweight keyword
    heuristics so the rest of the follow-up pipeline can be exercised without
    external dependencies.
    """

    feedback = feedback.strip()
    sentiment = _detect_sentiment(feedback)
    analysis = FeedbackAnalysis(sentiment=sentiment, summary=feedback)
    return analysis.format()
