"""מאגר הווקטורים.

`VectorStore` מגדיר מה המערכת צריכה ממאגר וקטורים. `ChromaStore` הוא המימוש
הנוכחי: קבצים מקומיים, בלי שרת. כשהמערכת תגדל, אפשר לכתוב מימוש אחר
(Qdrant, pgvector) עם אותן פעולות, ושאר הקוד לא ישתנה.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import chromadb

COLLECTION = "chunks"


class VectorStore(ABC):
    @abstractmethod
    def stored_texts(self) -> dict[str, str]:
        """chunk_id -> הטקסט שנשמר. מאפשר לדלג על קטעים שלא השתנו."""

    @abstractmethod
    def upsert(self, ids: list[str], vectors: list[list[float]], texts: list[str]) -> None: ...

    @abstractmethod
    def delete(self, ids: list[str]) -> None: ...

    @abstractmethod
    def search(self, vector: list[float], k: int) -> list[tuple[str, float]]:
        """הקטעים הקרובים ביותר: רשימה של (chunk_id, דמיון בין 0 ל-1)."""


class ChromaStore(VectorStore):
    def __init__(self, path: Path, embed_model: str):
        path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(path))
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION,
            metadata={"hnsw:space": "cosine", "embed_model": embed_model},
        )
        # וקטורים ממודל אמבדינג אחר לא ניתנים להשוואה, אז מתחילים מחדש
        if (self._collection.metadata or {}).get("embed_model") != embed_model:
            self.reset(embed_model)

    def reset(self, embed_model: str) -> None:
        self._client.delete_collection(COLLECTION)
        self._collection = self._client.create_collection(
            name=COLLECTION,
            metadata={"hnsw:space": "cosine", "embed_model": embed_model},
        )

    def stored_texts(self) -> dict[str, str]:
        data = self._collection.get(include=["documents"])
        return dict(zip(data["ids"], data["documents"] or []))

    def upsert(self, ids, vectors, texts) -> None:
        if ids:
            self._collection.upsert(ids=ids, embeddings=vectors, documents=texts)

    def delete(self, ids) -> None:
        if ids:
            self._collection.delete(ids=ids)

    def search(self, vector, k) -> list[tuple[str, float]]:
        count = self._collection.count()
        if count == 0:
            return []
        res = self._collection.query(query_embeddings=[vector], n_results=min(k, count), include=["distances"])
        # ב-Chroma, מרחק קוסינוס = 1 פחות הדמיון
        return [(cid, 1.0 - dist) for cid, dist in zip(res["ids"][0], res["distances"][0])]
