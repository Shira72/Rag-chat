"""בדיקות לחלוקה לקטעים."""
from app.ingestion.chunker import _split_long, chunk_document
from app.models import Clause, DocumentInfo, ParsedDocument


def _doc(clauses: list[Clause]) -> ParsedDocument:
    info = DocumentInfo(doc_id="H.9.9.9", filename="H.9.9.9.docx", file_hash="x", doc_type="instruction",
                        number="9.9.9", title="הוראת בדיקה", edition="2", edition_date="01.01.2024",
                        group_key="instruction:הוראת בדיקה")
    return ParsedDocument(info=info, clauses=clauses)


def _c(number: str | None, text: str, heading: bool = False) -> Clause:
    level = number.count(".") + 1 if number else 0
    anchor = "sec-" + number.replace(".", "-") if number else f"p-{abs(hash(text)) % 10000}"
    return Clause(number=number, level=level, anchor=anchor, text=text, is_heading=heading)


def test_section_with_subsections_is_one_chunk():
    long_text = "טקסט ארוך " * 40   # מעל סף האיחוד של סעיפים קצרים
    doc = _doc([
        _c("1", "מבוא", heading=True),
        _c("1.1", long_text),
        _c("1.2", "סעיף עם תתי-סעיפים:"),
        _c("1.2.1", "תת-סעיף ראשון " * 20),
        _c("1.2.2", "תת-סעיף שני " * 20),
    ])
    chunks = chunk_document(doc)
    section = next(c for c in chunks if "1.2" in c.clause_numbers)
    assert {"1.2", "1.2.1", "1.2.2"} <= set(section.clause_numbers)
    assert section.anchor == "sec-1-2"


def test_chunk_text_includes_document_edition_and_chapter():
    doc = _doc([_c("1", "מבוא", heading=True), _c("1.1", "תוכן")])
    text = chunk_document(doc)[0].text
    assert "הוראת תכ\"ם 9.9.9 – הוראת בדיקה" in text
    assert "מהדורה 2" in text
    assert "פרק: 1 מבוא" in text


def test_short_consecutive_sections_are_merged():
    doc = _doc([_c("3", "מסמכים ישימים", heading=True)] + [_c(f"3.{i}", f"חוק מספר {i}") for i in range(1, 6)])
    chunks = chunk_document(doc)
    assert len(chunks) == 1
    assert chunks[0].section_label == "סעיפים 3.1–3.5"


def test_split_long_keeps_overlap():
    lines = [f"שורה {i} " + "x" * 90 for i in range(10)]
    parts = _split_long(lines, max_chars=300, overlap_chars=120)
    assert len(parts) > 1
    assert parts[1][0] == parts[0][-1]   # השורה האחרונה של חלק חוזרת בתחילת החלק הבא
