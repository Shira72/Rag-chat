"""טעינת כל מה שהקליטה יצרה: מסמכים, קטעים, אינדקס מילים ומאגר וקטורים.

הטעינה מתבצעת פעם אחת, בהפעלת השרת. אחרי קליטה חדשה קוראים ל-`reload()`.
"""
from __future__ import annotations

import logging
from functools import cached_property

from app.config import settings
from app.ingestion.ingest import load_chunks, load_processed
from app.models import Chunk, Clause, DocumentInfo, ParsedDocument
from app.retrieval.embedder import Embedder
from app.retrieval.keyword_index import KeywordIndex
from app.retrieval.vector_store import ChromaStore, VectorStore

log = logging.getLogger(__name__)


class KnowledgeBase:
    def __init__(self) -> None:
        self.docs: dict[str, ParsedDocument] = load_processed()
        self.chunks: dict[str, Chunk] = {c.chunk_id: c for c in load_chunks()}
        self.keyword_index = KeywordIndex(list(self.chunks.values()))
        # מספר סעיף -> הפסקה, לכל מסמך. משמש לקישור מדויק ולאימות ציטוטים
        self._clauses: dict[str, dict[str, Clause]] = {
            doc_id: {c.number: c for c in doc.clauses if c.number}
            for doc_id, doc in self.docs.items()
        }
        log.info("נטענו %d מסמכים ו-%d קטעים", len(self.docs), len(self.chunks))

    @cached_property
    def vector_store(self) -> VectorStore:
        return ChromaStore(settings.index_dir / "chroma", settings.embed_model)

    @cached_property
    def embedder(self) -> Embedder:
        return Embedder()

    @property
    def is_empty(self) -> bool:
        return not self.chunks

    def info(self, doc_id: str) -> DocumentInfo:
        return self.docs[doc_id].info

    def clause(self, doc_id: str, number: str) -> Clause | None:
        return self._clauses.get(doc_id, {}).get(number)

    def current_doc_in_group(self, group_key: str) -> str | None:
        for doc_id, doc in self.docs.items():
            if doc.info.group_key == group_key and doc.info.is_current:
                return doc_id
        return None


_kb: KnowledgeBase | None = None


def get_kb() -> KnowledgeBase:
    global _kb
    if _kb is None:
        _kb = KnowledgeBase()
    return _kb


def reload() -> KnowledgeBase:
    global _kb
    _kb = KnowledgeBase()
    return _kb
