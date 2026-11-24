"""שאלה מהטרמינל, בלי שרת. שימושי לבדיקות ולפיתוח.

    python -m app.ask "תוך כמה ימים צריך לשלם לספק?"
    python -m app.ask --debug "..."     # מציג גם אילו קטעים נמצאו בחיפוש
"""
import argparse
import logging
import sys

from app.knowledge_base import get_kb
from app.llm.answerer import AnswerError, Answerer


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="+")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.debug else logging.WARNING, format="  [log] %(message)s")
    for noisy in ("httpx", "chromadb"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    question = " ".join(args.question)
    answerer = Answerer(get_kb())
    if args.debug:
        print("קטעים שנמצאו בחיפוש:")
        for r in answerer.retriever.search(question):
            sim = f"{r.similarity:.2f}" if r.similarity is not None else " -- "
            print(f"  score={r.score:.4f} sim={sim}  {r.chunk.chunk_id}  ({r.chunk.section_label})")
        print()
    try:
        response = answerer.answer(question)
    except AnswerError as e:
        print(f"שגיאה: {e}")
        sys.exit(1)

    print(f"שאלה: {question}\n")
    print(response.answer)
    if response.sources:
        print("\nמקורות:")
        for i, s in enumerate(response.sources, start=1):
            print(f"  [{i}] {s.doc_name} | {s.section_label} | {s.edition_label}")
            print(f"      {s.url}")
            for link in s.links:
                print(f"      קישור: {link.text} -> {link.url}")


if __name__ == "__main__":
    main()
