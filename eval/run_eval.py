"""מדידת איכות המערכת על סט שאלות קבוע (eval/questions.json).

הרצה:
    python eval/run_eval.py

כל הרצה קוראת ל-OpenAI (כמה סנטים בסך הכול). מומלץ להריץ לפני ואחרי כל שינוי
בחיפוש, בפרומפט, במודל או בהגדרות, ולהשוות את התוצאות.

מה נמדד:
  * חיפוש:  האם הסעיף הנכון היה בין הקטעים שנשלחו למודל.
  * ציטוט:  האם הסעיף הנכון מופיע ברשימת המקורות של התשובה.
  * סירוב:  בשאלות שאין להן תשובה במסמכים, האם המערכת ענתה "לא מצאתי".
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.knowledge_base import KnowledgeBase, get_kb  # noqa: E402
from app.llm.answerer import Answerer  # noqa: E402

QUESTIONS = Path(__file__).with_name("questions.json")


def _matches(kb: KnowledgeBase, doc_id: str, anchor: str, expected: list[dict]) -> bool:
    """האם (מסמך, עוגן) מכסה את אחד הסעיפים הצפויים (או תת-סעיף שלו)."""
    numbers = {c.number for c in kb.docs[doc_id].clauses if c.anchor == anchor and c.number}
    numbers |= {n for ch in kb.chunks.values() if ch.doc_id == doc_id and ch.anchor == anchor for n in ch.clause_numbers}
    for exp in expected:
        if exp["doc"] != doc_id:
            continue
        if not exp["clauses"]:
            return True
        if any(n == c or n.startswith(c + ".") for n in numbers for c in exp["clauses"]):
            return True
    return False


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    kb = get_kb()
    answerer = Answerer(kb)
    cases = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    stats = {"answerable": 0, "retrieval_hit": 0, "citation_hit": 0, "unanswerable": 0, "refused": 0}
    failures = []

    for case in cases:
        q, expected = case["question"], case["expected"]
        retrieved = answerer.retriever.search(q)
        response = answerer.answer(q)
        if expected:
            stats["answerable"] += 1
            r_hit = any(_matches(kb, r.chunk.doc_id, r.chunk.anchor, expected) for r in retrieved)
            c_hit = response.found and any(
                _matches(kb, *s.url.removeprefix("/doc/").split("#", 1), expected) for s in response.sources
            )
            stats["retrieval_hit"] += r_hit
            stats["citation_hit"] += c_hit
            mark = "✅" if c_hit else ("⚠️ " if r_hit else "❌")
            if not c_hit:
                failures.append((case, response))
        else:
            stats["unanswerable"] += 1
            refused = not response.found
            stats["refused"] += refused
            mark = "✅" if refused else "❌"
            if not refused:
                failures.append((case, response))
        print(f"{mark} [{case['type']}] {q}")

    a, u = stats["answerable"] or 1, stats["unanswerable"] or 1
    print("\n" + "=" * 60)
    print(f"חיפוש  – הסעיף הנכון נשלח למודל:     {stats['retrieval_hit']}/{stats['answerable']} ({stats['retrieval_hit'] / a:.0%})")
    print(f"ציטוט  – הסעיף הנכון הופיע במקורות:  {stats['citation_hit']}/{stats['answerable']} ({stats['citation_hit'] / a:.0%})")
    print(f"סירוב  – 'לא מצאתי' כשאין תשובה:     {stats['refused']}/{stats['unanswerable']} ({stats['refused'] / u:.0%})")
    print("מקרא: ✅ תקין  ⚠️  החיפוש מצא אבל התשובה לא ציטטה  ❌ נכשל")

    if failures:
        print("\nפירוט הכישלונות:")
        for case, resp in failures:
            print(f"\n• {case['question']}\n  צפוי: {case['expected'] or 'לא מצאתי'}")
            print(f"  התשובה: {resp.answer[:300]}")
            for s in resp.sources:
                print(f"    מקור: {s.doc_name} | {s.section_label} | {s.url}")


if __name__ == "__main__":
    main()
