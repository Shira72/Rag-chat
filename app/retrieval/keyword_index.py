"""חיפוש לפי מילים (BM25).

משלים את החיפוש הווקטורי. הוא טוב במיוחד במילים מדויקות: מספרי סעיפים
והוראות ("2.12", "1.4.3"), שמות חוקים ומונחים מקצועיים.
האינדקס קטן ונבנה בזיכרון בכל הפעלה של השרת.
"""
from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

from app.models import Chunk

# מילה עברית או לועזית, או מספר (כולל מספרי סעיפים עם נקודות, וגם ט.1.4.6.1)
_TOKEN = re.compile(r"[א-תa-zA-Z]+(?:[\"'׳״][א-ת]+)?|(?:ט\.)?\d+(?:\.\d+)*")
_HEB_PREFIXES = "והבלמשכ"
_STOPWORDS = {"של", "את", "על", "או", "גם", "כי", "אם", "לא", "זו", "זה", "היא", "הוא", "אשר", "עם", "כל", "מה"}


_HEB_SUFFIXES = ("ים", "ות", "י")


def _variants(tok: str) -> set[str]:
    """צורות נוספות של מילה עברית, בלי אותיות שימוש בהתחלה ובלי סיומת ריבוי או סמיכות.

    לדוגמה: 'לספק' -> 'ספק', 'ספקי' -> 'ספק', 'והתשלומים' -> 'תשלומים', 'תשלום'.
    זה לא ניתוח מורפולוגי מלא, אבל משפר מאוד את ההתאמה בין שאלה לנוסח של הוראה.
    """
    if not tok or tok[0].isdigit() or not ("א" <= tok[0] <= "ת"):
        return set()
    stems = {tok}
    for _ in range(2):  # עד שתי אותיות שימוש, למשל 'וה', 'של'
        stems |= {s[1:] for s in stems if len(s) > 3 and s[0] in _HEB_PREFIXES}
    for s in list(stems):
        for suffix in _HEB_SUFFIXES:
            if s.endswith(suffix) and len(s) - len(suffix) >= 3:
                stems.add(s[: -len(suffix)])
    stems.discard(tok)
    return stems


def tokenize(text: str) -> list[str]:
    """מילים לחיפוש: כל מילה, ועוד הצורות שלה בלי אותיות שימוש וסיומות."""
    tokens: list[str] = []
    for raw in _TOKEN.findall(text.lower()):
        tok = re.sub(r"[\"'׳״]", "", raw)
        if tok in _STOPWORDS:
            continue
        tokens.append(tok)
        tokens.extend(sorted(_variants(tok)))
    return tokens


class KeywordIndex:
    def __init__(self, chunks: list[Chunk]):
        self._ids = [c.chunk_id for c in chunks]
        self._bm25 = BM25Okapi([tokenize(c.text) for c in chunks]) if chunks else None

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        if self._bm25 is None:
            return []
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(zip(self._ids, scores), key=lambda x: x[1], reverse=True)
        return [(cid, float(s)) for cid, s in ranked[:k] if s > 0]
