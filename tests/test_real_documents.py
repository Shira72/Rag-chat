"""בדיקות על קבצי ה-Word האמיתיים שב-data/docs.

הבדיקות משוות את המספור שהמערכת מחשבת להפניות שכתובות בתוך המסמכים עצמם
(למשל "סעיף 2.12 להלן (בהוראות המעבר)"). אם הקבצים לא נמצאים, הבדיקות מדולגות.
"""
import pytest

from app.config import settings
from app.ingestion.docx_parser import parse_docx
from app.ingestion.ingest import mark_current_editions


def _parse(name: str):
    path = settings.docs_dir / name
    if not path.exists():
        pytest.skip(f"{name} לא נמצא ב-data/docs")
    return parse_docx(path)


def _clause(doc, number):
    return next(c for c in doc.clauses if c.number == number)


def test_payment_dates_numbering_matches_internal_references():
    doc = _parse("H.1.4.3.docx")
    assert _clause(doc, "2.12").text == "הוראות מעבר"
    assert _clause(doc, "2.7").text.startswith("נאמני חוק מוסר תשלומים")
    assert _clause(doc, "2.1").text.startswith("מועד התשלום הממשלתי לכלל ספקי הממשלה")
    assert "45" in _clause(doc, "2.1").text


def test_invoice_check_numbering_matches_change_table():
    doc = _parse("H.1.4.2.docx")
    assert "חתימה אלקטרונית" in _clause(doc, "2.2.13").text   # לפי טבלת השינויים
    assert _clause(doc, "2.6").text.startswith("החשבון ייבדק לכל הפחות על ידי 2 עובדים")


def test_long_document_cross_references():
    doc = _parse("H.1.4.6.docx")
    assert "סעיף 2.2.2.1" in _clause(doc, "2.2.2.2").text
    assert "סעיף 2.4.1" in _clause(doc, "2.4.2").text
    assert "חרבות ברזל" in _clause(doc, "2.1.4.4").text


def test_header_info_instruction():
    info = _parse("H.1.4.3.docx").info
    assert (info.doc_type, info.number, info.title, info.edition, info.edition_date) == \
        ("instruction", "1.4.3", "מועדי תשלום", "6", "19.09.2024")


def test_header_info_form():
    info = _parse("0683X00000mI846QAC.docx").info
    assert (info.doc_type, info.number, info.title) == ("form", "ט.1.4.6.1", "התחייבות להקדמת תשלומים")


def test_current_edition_is_highest():
    docs = [_parse("H.1.4.3.docx"), _parse("0683X00000UDxfkQAD.docx"), _parse("0683X00000dFsm3QAC.docx")]
    mark_current_editions(docs)
    assert [d.info.is_current for d in docs] == [True, False, False]


def test_appendices_and_links():
    doc = _parse("H.1.4.3.docx")
    headings = [c.text for c in doc.clauses if c.is_heading and not c.number]
    assert "נספח א – הגדרות" in headings
    assert any(link.url.startswith("https://") for c in doc.clauses for link in c.links)
