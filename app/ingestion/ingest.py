"""קליטת המסמכים ובניית האינדקס.

הרצה:
    python -m app.ingestion.ingest            # עדכון: מעבד רק קבצים חדשים או ששונו
    python -m app.ingestion.ingest --rebuild  # בנייה מחדש מאפס

השלבים:
  1. קריאת כל קובץ DOCX ב-data/docs. קובץ שלא השתנה (לפי hash) לא נקרא שוב.
  2. סימון המהדורה הנוכחית בכל קבוצת מהדורות של אותה הוראה.
  3. שמירת כל מסמך מפורסר ב-data/processed (משמש את דף הצפייה במסמך).
  4. חלוקה לקטעים ושמירתם ב-data/index/chunks.json (משמש את החיפוש לפי מילים).
  5. אמבדינג רק לקטעים חדשים או ששונו, ועדכון מאגר הווקטורים.
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import datetime

from app.config import settings
from app.ingestion.chunker import chunk_document
from app.ingestion.docx_parser import file_sha256, make_doc_id, parse_docx
from app.models import Chunk, ParsedDocument
from app.retrieval.embedder import Embedder
from app.retrieval.vector_store import ChromaStore

log = logging.getLogger("ingest")

CHUNKS_FILE = "chunks.json"


# ---------- קריאה וכתיבה של הקבצים שנוצרים ----------

def load_processed() -> dict[str, ParsedDocument]:
    docs = {}
    for f in settings.processed_dir.glob("*.json"):
        try:
            doc = ParsedDocument.model_validate_json(f.read_text(encoding="utf-8"))
            docs[doc.info.doc_id] = doc
        except ValueError:
            log.warning("קובץ מעובד פגום, יעובד מחדש: %s", f.name)
    return docs


def save_processed(doc: ParsedDocument) -> None:
    settings.processed_dir.mkdir(parents=True, exist_ok=True)
    path = settings.processed_dir / f"{doc.info.doc_id}.json"
    path.write_text(doc.model_dump_json(indent=1), encoding="utf-8")


def load_chunks() -> list[Chunk]:
    path = settings.index_dir / CHUNKS_FILE
    if not path.exists():
        return []
    return [Chunk.model_validate(c) for c in json.loads(path.read_text(encoding="utf-8"))]


def save_chunks(chunks: list[Chunk]) -> None:
    settings.index_dir.mkdir(parents=True, exist_ok=True)
    data = [c.model_dump() for c in chunks]
    (settings.index_dir / CHUNKS_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- מהדורה נוכחית ----------

def _edition_sort_key(doc: ParsedDocument) -> tuple[int, datetime]:
    info = doc.info
    edition = int(info.edition) if info.edition and info.edition.isdigit() else 0
    try:
        date = datetime.strptime(info.edition_date or "", "%d.%m.%Y")
    except ValueError:
        date = datetime.min
    return edition, date


def mark_current_editions(docs: list[ParsedDocument]) -> None:
    """בכל קבוצה (אותה הוראה), המהדורה הגבוהה ביותר היא הנוכחית, ושאר המהדורות קודמות."""
    groups: dict[str, list[ParsedDocument]] = {}
    for d in docs:
        groups.setdefault(d.info.group_key, []).append(d)
    for members in groups.values():
        newest = max(members, key=_edition_sort_key)
        for d in members:
            d.info.is_current = d is newest
        if len(members) > 1:
            log.info("  %s: נוכחית = %s, קודמות = %s", newest.info.title, newest.info.filename,
                     [d.info.filename for d in members if d is not newest])


# ---------- התהליך ----------

def collect_documents(previous: dict[str, ParsedDocument]) -> list[ParsedDocument]:
    docs_dir = settings.docs_dir
    docs_dir.mkdir(parents=True, exist_ok=True)
    docs: list[ParsedDocument] = []
    seen_ids: set[str] = set()

    for path in sorted(docs_dir.iterdir()):
        if path.name.startswith("~$") or path.is_dir():
            continue                                   # קובץ זמני של Word פתוח
        if path.suffix.lower() == ".doc":
            log.warning("דילוג על %s: פורמט Word ישן (.doc). שמרי אותו מחדש כ-.docx", path.name)
            continue
        if path.suffix.lower() != ".docx":
            continue

        doc_id = make_doc_id(path)
        if doc_id in seen_ids:
            log.warning("דילוג על %s: שם קובץ כפול (המזהה %s כבר בשימוש)", path.name, doc_id)
            continue
        seen_ids.add(doc_id)

        file_hash = file_sha256(path)
        old = previous.get(doc_id)
        if old and old.info.file_hash == file_hash:
            docs.append(old)
            continue
        try:
            doc = parse_docx(path)
        except Exception as e:  # קובץ פגום לא עוצר את הקליטה של כל השאר
            log.error("לא ניתן לקרוא את %s: %s", path.name, e)
            continue
        if not doc.clauses:
            log.warning("דילוג על %s: לא נמצא בו טקסט", path.name)
            continue
        log.info("נקרא: %s -> %s (%d פסקאות)", path.name, doc.info.display_name, len(doc.clauses))
        docs.append(doc)
    return docs


def run(rebuild: bool = False) -> None:
    if rebuild:
        log.info("בנייה מחדש: מוחק את התוצרים הקודמים")
        shutil.rmtree(settings.processed_dir, ignore_errors=True)
        shutil.rmtree(settings.index_dir, ignore_errors=True)

    previous = load_processed()
    docs = collect_documents(previous)
    if not docs:
        log.warning("לא נמצאו מסמכים ב-%s", settings.docs_dir)

    mark_current_editions(docs)

    # שמירת המסמכים, ומחיקת מסמכים שהקובץ שלהם הוסר מהתיקייה
    current_ids = {d.info.doc_id for d in docs}
    for d in docs:
        save_processed(d)
    for doc_id in set(previous) - current_ids:
        (settings.processed_dir / f"{doc_id}.json").unlink(missing_ok=True)
        log.info("הוסר מהאינדקס: %s", doc_id)

    chunks = [c for d in docs for c in chunk_document(d)]
    save_chunks(chunks)

    # אמבדינג רק למה שחדש או השתנה
    store = ChromaStore(settings.index_dir / "chroma", settings.embed_model)
    stored = store.stored_texts()
    wanted = {c.chunk_id: c.text for c in chunks}
    to_embed = [cid for cid, text in wanted.items() if stored.get(cid) != text]
    to_delete = [cid for cid in stored if cid not in wanted]

    if to_embed:
        log.info("מחשב אמבדינג ל-%d קטעים (מתוך %d)...", len(to_embed), len(chunks))
        vectors = Embedder().embed([wanted[cid] for cid in to_embed])
        store.upsert(to_embed, vectors, [wanted[cid] for cid in to_embed])
    store.delete(to_delete)

    log.info("סיום: %d מסמכים, %d קטעים. חושבו %d, נמחקו %d, ללא שינוי %d.",
             len(docs), len(chunks), len(to_embed), len(to_delete), len(chunks) - len(to_embed))


def main() -> None:
    parser = argparse.ArgumentParser(description="קליטת מסמכי Word ובניית אינדקס החיפוש")
    parser.add_argument("--rebuild", action="store_true", help="מחיקת האינדקס ובנייה מחדש מאפס")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # עברית תקינה בטרמינל של Windows
    run(rebuild=args.rebuild)


if __name__ == "__main__":
    main()
