"""הפיכת טקסט לווקטור (אמבדינג) בעזרת OpenAI."""
from __future__ import annotations

from openai import OpenAI

from app.config import settings

BATCH_SIZE = 100


class MissingApiKeyError(RuntimeError):
    pass


def get_openai_client() -> OpenAI:
    if not settings.openai_api_key or settings.openai_api_key.startswith("sk-..."):
        raise MissingApiKeyError("חסר מפתח OpenAI. העתיקי את .env.example ל-.env והכניסי את OPENAI_API_KEY.")
    # max_retries: הספרייה מנסה שוב אוטומטית במקרה של עומס (429) או תקלה זמנית
    return OpenAI(api_key=settings.openai_api_key, max_retries=5, timeout=60)


class Embedder:
    def __init__(self, client: OpenAI | None = None, model: str | None = None):
        self.client = client or get_openai_client()
        self.model = model or settings.embed_model

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i:i + BATCH_SIZE]
            response = self.client.embeddings.create(model=self.model, input=batch)
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
        return vectors

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]
