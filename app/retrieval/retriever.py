"""חיפוש היברידי: מוצא את הקטעים הרלוונטיים לשאלה.

שלוש רשימות דירוג מתמזגות לרשימה אחת בשיטת RRF (Reciprocal Rank Fusion):
  1. חיפוש וקטורי: קרבה במשמעות, גם כשהמילים שונות.
  2. חיפוש מילים (BM25): מונחים מדויקים, שמות חוקים, מספרים.
  3. הפניה ישירה: אם השאלה מזכירה מספר הוראה ("1.4.3") או סעיף ("2.12"),
     הקטעים המתאימים מקבלים עדיפות.

אחר כך:
  * סף רלוונטיות: אם שום קטע לא קרוב מספיק לשאלה, מחזירים רשימה ריקה.
    כך המערכת עונה "לא מצאתי" ולא מנסה לענות מתוך מקורות לא קשורים.
  * מהדורות: קטע ממהדורה קודמת תמיד מגיע יחד עם הקטע המקביל מהמהדורה
    הנוכחית, כדי שהמודל יוכל להשוות ביניהן ולא יציג נוסח ישן כאילו הוא בתוקף.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.config import settings
from app.knowledge_base import KnowledgeBase
from app.models import Chunk, RetrievedChunk

RRF_K = 60                  # קבוע מקובל בשיטת RRF
CURRENT_EDITION_BOOST = 1.15
_NUMBER = re.compile(r"(?<![\d.])(?:ט\.)?\d+(?:\.\d+)+(?![\d.])")


@dataclass
class _Candidate:
    chunk: Chunk
    score: float = 0.0
    similarity: float | None = None
    direct_hit: bool = False    # נמצא דרך הפניה ישירה למספר הוראה או סעיף


def _rrf(ranked_ids: list[str], weight: float = 1.0) -> dict[str, float]:
    return {cid: weight / (RRF_K + rank) for rank, cid in enumerate(ranked_ids, start=1)}


class Retriever:
    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    # ---------- הפניה ישירה למספרים ----------

    def _direct_matches(self, question: str) -> list[str]:
        """קטעים שהשאלה מפנה אליהם במפורש: מספר הוראה, מספר סעיף, או שניהם."""
        numbers = set(_NUMBER.findall(question))
        if not numbers:
            return []
        doc_ids = [d for d, doc in self.kb.docs.items() if doc.info.number in numbers]
        clause_numbers = numbers - {self.kb.info(d).number for d in doc_ids}
        hits = []
        for chunk in self.kb.chunks.values():
            if doc_ids and chunk.doc_id not in doc_ids:
                continue
            if clause_numbers:
                if clause_numbers & set(chunk.clause_numbers):
                    hits.append(chunk.chunk_id)
            elif doc_ids:
                hits.append(chunk.chunk_id)
        # הפניה כללית מדי (למשל רק מספר הוראה) לא צריכה להציף את התוצאות
        return hits if len(hits) <= settings.final_k else []

    # ---------- החיפוש ----------

    def search(self, question: str) -> list[RetrievedChunk]:
        if self.kb.is_empty:
            return []

        vector_hits = self.kb.vector_store.search(self.kb.embedder.embed_one(question), settings.top_k_vector)
        keyword_hits = self.kb.keyword_index.search(question, settings.top_k_keyword)
        direct_hits = self._direct_matches(question)

        candidates: dict[str, _Candidate] = {}

        def add(scores: dict[str, float]) -> None:
            for cid, s in scores.items():
                if cid in self.kb.chunks:
                    candidates.setdefault(cid, _Candidate(self.kb.chunks[cid])).score += s

        add(_rrf([cid for cid, _ in vector_hits]))
        add(_rrf([cid for cid, _ in keyword_hits]))
        add(_rrf(direct_hits, weight=2.0))
        for cid, sim in vector_hits:
            if cid in candidates:
                candidates[cid].similarity = sim
        for cid in direct_hits:
            candidates[cid].direct_hit = True

        # סף רלוונטיות: נדרש קטע אחד לפחות שקרוב מספיק במשמעות, או הפניה ישירה
        best_similarity = max((sim for _, sim in vector_hits), default=0.0)
        if best_similarity < settings.min_similarity and not direct_hits:
            return []

        self._share_scores_with_current_edition(candidates)
        for c in candidates.values():
            if c.chunk.is_current:
                c.score *= CURRENT_EDITION_BOOST

        ranked = sorted(candidates.values(), key=lambda c: c.score, reverse=True)
        selected = self._select(ranked)
        selected = self._add_current_counterparts(selected, ranked)
        return [RetrievedChunk(chunk=c.chunk, score=c.score, similarity=c.similarity) for c in selected]

    def _share_scores_with_current_edition(self, candidates: dict[str, _Candidate]) -> None:
        """אם סעיף ממהדורה קודמת נמצא רלוונטי, גם אותו סעיף במהדורה הנוכחית רלוונטי.

        מהדורות של אותה הוראה דומות מאוד, ולפעמים דווקא הנוסח הישן מקבל ציון גבוה יותר.
        כאן הקטע המקביל במהדורה הנוכחית (אותו עוגן) מקבל לפחות את אותו ציון, וגם נוסף
        לרשימת המועמדים אם לא היה בה.
        """
        for cand in list(candidates.values()):
            if cand.chunk.is_current:
                continue
            current_doc = self.kb.current_doc_in_group(self.kb.info(cand.chunk.doc_id).group_key)
            counterpart_id = f"{current_doc}::{cand.chunk.anchor}::0" if current_doc else None
            if counterpart_id not in self.kb.chunks:
                continue
            counterpart = candidates.setdefault(counterpart_id, _Candidate(self.kb.chunks[counterpart_id]))
            counterpart.score = max(counterpart.score, cand.score)
            if cand.similarity is not None:
                counterpart.similarity = max(counterpart.similarity or 0.0, cand.similarity)
            counterpart.direct_hit = counterpart.direct_hit or cand.direct_hit

    def _select(self, ranked: list[_Candidate]) -> list[_Candidate]:
        """הקטעים המובילים, עם הגבלה על קטעים ממהדורות קודמות.

        מהדורות קודמות דומות מאוד לנוכחית, ובלי הגבלה הן תופסות את רוב המקומות.
        הפניה ישירה (למשל "מהדורה 3" או מספר ההוראה הישן) לא מוגבלת.
        """
        selected, old_count = [], 0
        for c in ranked:
            if len(selected) >= settings.final_k:
                break
            if not c.chunk.is_current and not c.direct_hit:
                if old_count >= settings.max_old_edition_chunks:
                    continue
                old_count += 1
            selected.append(c)
        return selected

    def _add_current_counterparts(self, selected: list[_Candidate], ranked: list[_Candidate]) -> list[_Candidate]:
        """לכל קטע ממהדורה קודמת מוסיפים את הקטע המקביל מהמהדורה הנוכחית, אם הוא חסר."""
        selected_ids = {c.chunk.chunk_id for c in selected}
        extra: list[_Candidate] = []
        for cand in selected:
            if cand.chunk.is_current:
                continue
            group = self.kb.info(cand.chunk.doc_id).group_key
            current_doc = self.kb.current_doc_in_group(group)
            if not current_doc:
                continue
            # עדיפות: אותו סעיף במהדורה הנוכחית. אחרת, אם אין שום קטע מהמהדורה
            # הנוכחית ברשימה: הקטע הכי מדורג ממנה
            same_anchor = f"{current_doc}::{cand.chunk.anchor}::0"
            match = next((c for c in ranked if c.chunk.chunk_id == same_anchor), None)
            if match is None and same_anchor in self.kb.chunks:
                match = _Candidate(self.kb.chunks[same_anchor])
            if match is None and not any(c.chunk.doc_id == current_doc for c in selected + extra):
                match = next((c for c in ranked if c.chunk.doc_id == current_doc), None)
            if match and match.chunk.chunk_id not in selected_ids:
                extra.append(match)
                selected_ids.add(match.chunk.chunk_id)
        return selected + extra
