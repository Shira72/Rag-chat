"""מנוע התשובות: שאלה -> תשובה עם מקורות מאומתים.

הזרימה:
  1. שאלת המשך ("ומה לגבי עבודות בנייה?") מנוסחת מחדש כשאלה עצמאית.
  2. חיפוש. אם אין מקורות רלוונטיים, עונים "לא מצאתי" בלי לקרוא למודל.
  3. המודל מקבל את המקורות ומחזיר JSON מובנה: found, answer, citations.
  4. אימות בקוד:
       * ציטוט של מקור שלא נשלח למודל נזרק.
       * מספר סעיף שלא קיים בקטע שצוטט נזרק, והקישור מפנה לתחילת הקטע.
       * אם המודל אמר שמצא תשובה אבל אין אף ציטוט תקין, התשובה הופכת ל"לא מצאתי".
  5. המזהים [S1], [S2] בטקסט מוחלפים במספרים [1], [2] לפי רשימת המקורות שמוצגת.
"""
from __future__ import annotations

import logging
import re

import openai
from pydantic import BaseModel, Field

from app.config import settings
from app.knowledge_base import KnowledgeBase
from app.llm.prompts import (
    ANSWER_SYSTEM_PROMPT,
    NOT_FOUND_ANSWER,
    REWRITE_SYSTEM_PROMPT,
    answer_user_message,
    format_sources,
)
from app.models import ChatMessage, ChatResponse, Link, RetrievedChunk, Source
from app.retrieval.embedder import MissingApiKeyError, get_openai_client
from app.retrieval.retriever import Retriever

log = logging.getLogger(__name__)

MAX_LINKS_PER_SOURCE = 5


class AnswerError(RuntimeError):
    """שגיאה שאפשר להציג למשתמש כמו שהיא."""


# ---------- המבנה שהמודל מחזיר ----------

class _Citation(BaseModel):
    source_id: str = Field(description="מזהה המקור, למשל S1")
    clause: str | None = Field(description="מספר הסעיף המדויק במקור, למשל 2.2.13, או null")


class _LlmAnswer(BaseModel):
    found: bool = Field(description="האם המקורות מכילים תשובה לשאלה")
    answer: str = Field(description="התשובה בעברית, עם מזהי מקורות בסוגריים מרובעים")
    citations: list[_Citation]


# ---------- המנוע ----------

class Answerer:
    def __init__(self, kb: KnowledgeBase, client: openai.OpenAI | None = None):
        self.kb = kb
        self.retriever = Retriever(kb)
        self._client = client

    @property
    def client(self) -> openai.OpenAI:
        if self._client is None:
            self._client = get_openai_client()
        return self._client

    def _chat_kwargs(self) -> dict:
        kwargs = {"model": settings.chat_model}
        if settings.llm_temperature is not None:
            kwargs["temperature"] = settings.llm_temperature
        return kwargs

    # --- 1. שאלת המשך ---

    def standalone_question(self, question: str, history: list[ChatMessage]) -> str:
        recent = history[-settings.max_history_messages:]
        if not recent:
            return question
        transcript = "\n".join(
            f"{'משתמש' if m.role == 'user' else 'עוזר'}: {m.content[:1500]}" for m in recent
        )
        response = self.client.chat.completions.create(
            messages=[
                {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
                {"role": "user", "content": f"השיחה עד כה:\n{transcript}\n\nהשאלה האחרונה: {question}"},
            ],
            max_completion_tokens=300,
            **self._chat_kwargs(),
        )
        rewritten = (response.choices[0].message.content or "").strip()
        log.info("שאלה מנוסחת מחדש: %s", rewritten)
        return rewritten or question

    # --- 3. קריאה למודל ---

    def _ask_llm(self, question: str, retrieved: list[RetrievedChunk]) -> _LlmAnswer:
        sources = []
        for i, r in enumerate(retrieved, start=1):
            info = self.kb.info(r.chunk.doc_id)
            sources.append((f"S{i}", info.display_name, info.edition_label, r.chunk.text))
        completion = self.client.chat.completions.parse(
            messages=[
                {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
                {"role": "user", "content": answer_user_message(question, format_sources(sources))},
            ],
            response_format=_LlmAnswer,
            max_completion_tokens=2000,
            **self._chat_kwargs(),
        )
        message = completion.choices[0].message
        if message.parsed is None:
            log.warning("המודל לא החזיר תשובה מובנית: %s", message.refusal)
            return _LlmAnswer(found=False, answer="", citations=[])
        return message.parsed

    # --- 4-5. אימות ובניית התשובה ---

    def _links_for(self, retrieved: RetrievedChunk, clause_number: str | None) -> list[Link]:
        doc_id = retrieved.chunk.doc_id
        numbers = [clause_number] if clause_number else retrieved.chunk.clause_numbers
        links, seen = [], set()
        for n in numbers:
            clause = self.kb.clause(doc_id, n)
            for link in clause.links if clause else []:
                if link.url not in seen:
                    seen.add(link.url)
                    links.append(link)
        return links[:MAX_LINKS_PER_SOURCE]

    def _resolve_citation(
        self, cit: _Citation, retrieved: list[RetrievedChunk], r: RetrievedChunk
    ) -> list[tuple[RetrievedChunk, str | None]]:
        """ציטוט -> רשימה של (קטע, מספר סעיף מאומת או None).

        המודל לפעמים טועה בפרטים הקטנים, ומתקנים רק מה שאפשר לאמת:
          * "2.2, 2.3" מתפצל לשני סעיפים.
          * סעיף שקיים במקור אחר מאותו מסמך (המודל התבלבל במזהה) משויך למקור הנכון.
          * סעיף שלא קיים באף מקור שנשלח למודל נזרק, והקישור מפנה לתחילת הקטע.
        """
        numbers = re.findall(r"\d+(?:\.\d+)*", cit.clause or "")
        if not numbers:
            return [(r, None)]
        out = []
        for n in numbers:
            if n in r.chunk.clause_numbers:
                out.append((r, n))
                continue
            other = next((o for o in retrieved if o.chunk.doc_id == r.chunk.doc_id and n in o.chunk.clause_numbers), None)
            if other:
                out.append((other, n))
            else:
                log.warning("המודל ציטט סעיף %s שלא נמצא במקורות (%s)", n, cit.source_id)
                out.append((r, None))
        return out

    def _build_response(self, llm: _LlmAnswer, retrieved: list[RetrievedChunk]) -> ChatResponse:
        by_id = {f"S{i}": r for i, r in enumerate(retrieved, start=1)}
        sources: list[Source] = []
        display_number: dict[str, int] = {}     # S1 -> 1 (מיקום ברשימה המוצגת)
        seen_targets: dict[str, int] = {}       # כתובת -> מיקום, כדי לא להציג אותו סעיף פעמיים

        for cit in llm.citations:
            r = by_id.get(cit.source_id)
            if r is None:
                log.warning("המודל ציטט מקור שלא קיים: %s", cit.source_id)
                continue
            for chunk_ref, number in self._resolve_citation(cit, retrieved, r):
                doc_id = chunk_ref.chunk.doc_id
                clause = self.kb.clause(doc_id, number) if number else None
                anchor = clause.anchor if clause else chunk_ref.chunk.anchor
                url = f"/doc/{doc_id}#{anchor}"
                if url in seen_targets:
                    display_number.setdefault(cit.source_id, seen_targets[url])
                    continue
                info = self.kb.info(doc_id)
                sources.append(Source(
                    doc_name=info.display_name,
                    edition_label=info.edition_label,
                    is_current=info.is_current,
                    section_label=f"סעיף {clause.number}" if clause else chunk_ref.chunk.section_label,
                    url=url,
                    links=self._links_for(chunk_ref, number if clause else None),
                ))
                seen_targets[url] = len(sources)
                display_number.setdefault(cit.source_id, len(sources))

        if not llm.found or not sources or not llm.answer.strip():
            return ChatResponse(found=False, answer=NOT_FOUND_ANSWER)

        def renumber(match: re.Match) -> str:
            n = display_number.get(match.group(1))
            return f"[{n}]" if n else ""

        answer = re.sub(r"\[(S\d+)\]", renumber, llm.answer)
        answer = re.sub(r"(\[\d+\])(?:\1)+", r"\1", answer)     # [1][1] -> [1]
        return ChatResponse(found=True, answer=answer.strip(), sources=sources)

    # --- הכול יחד ---

    def answer(self, question: str, history: list[ChatMessage] | None = None) -> ChatResponse:
        if self.kb.is_empty:
            raise AnswerError("עדיין לא נקלטו מסמכים. יש להריץ קודם: python -m app.ingestion.ingest")
        try:
            standalone = self.standalone_question(question, history or [])
            retrieved = self.retriever.search(standalone)
            if not retrieved:
                log.info("לא נמצאו מקורות רלוונטיים לשאלה: %s", standalone)
                return ChatResponse(found=False, answer=NOT_FOUND_ANSWER)
            llm = self._ask_llm(standalone, retrieved)
        except MissingApiKeyError as e:
            raise AnswerError(str(e)) from e
        except openai.AuthenticationError as e:
            raise AnswerError("מפתח OpenAI לא תקין או שאין לו הרשאה. יש לבדוק את OPENAI_API_KEY בקובץ .env") from e
        except openai.PermissionDeniedError as e:
            raise AnswerError("למפתח OpenAI אין הרשאה לפעולה הזו. יש לבדוק את הרשאות המפתח (Embeddings, Chat completions).") from e
        except openai.RateLimitError as e:
            raise AnswerError("יש כרגע עומס, או שנגמרה המכסה בחשבון OpenAI. נסו שוב בעוד רגע.") from e
        except (openai.APIConnectionError, openai.APITimeoutError) as e:
            raise AnswerError("אין חיבור לשירות OpenAI. יש לבדוק את החיבור לאינטרנט ולנסות שוב.") from e
        except openai.APIError as e:
            log.exception("שגיאה מ-OpenAI")
            raise AnswerError("אירעה שגיאה בשירות OpenAI. נסו שוב בעוד רגע.") from e
        return self._build_response(llm, retrieved)
