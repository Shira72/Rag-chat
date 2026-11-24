"""בדיקות לעיבוד טקסט: פורמט מספור, חיפוש מילים בעברית, ניקוי טקסט."""
from app.ingestion.docx_parser import clean
from app.ingestion.word_numbering import format_number
from app.models import Chunk
from app.retrieval.keyword_index import KeywordIndex, tokenize


def test_format_number_decimal_and_letters():
    assert format_number(7, "decimal") == "7"
    assert format_number(3, "decimalZero") == "03"
    assert format_number(1, "lowerLetter") == "a"
    assert format_number(27, "lowerLetter") == "aa"
    assert format_number(4, "lowerRoman") == "iv"


def test_format_number_hebrew_gematria():
    assert format_number(1, "hebrew1") == "א"
    assert format_number(10, "hebrew1") == "י"
    assert format_number(11, "hebrew1") == "יא"
    assert format_number(15, "hebrew1") == "טו"   # לא י"ה
    assert format_number(16, "hebrew1") == "טז"   # לא י"ו
    assert format_number(2, "hebrew2") == "ב"


def test_clean_removes_direction_marks_and_spaces():
    assert clean("\u200Eסעיף\u200F  2.12 ") == "סעיף 2.12"
    assert clean("משרדי הממשלה''") == 'משרדי הממשלה"'


def test_tokenize_hebrew_prefixes_and_suffixes():
    assert "ספק" in tokenize("לספק")
    assert "ספק" in tokenize("ספקי")
    assert "תשלומים" in tokenize("והתשלומים")
    assert "חשבון" in tokenize("החשבון")


def test_tokenize_keeps_section_numbers():
    assert "2.12" in tokenize("מה כתוב בסעיף 2.12?")
    assert "1.4.6.1" in tokenize("טופס ט.1.4.6.1")


def _chunk(cid: str, text: str) -> Chunk:
    return Chunk(chunk_id=cid, doc_id="d", anchor=cid, section_label="", clause_numbers=[], text=text, is_current=True)


def test_keyword_index_matches_different_word_forms():
    # BM25 נותן משקל למילים לפי נדירותן, ולכן צריך מאגר עם יותר מקטע או שניים
    filler = [_chunk(f"f{i}", f"נספח {i} טבלת שינויים מהדורה") for i in range(6)]
    index = KeywordIndex([_chunk("a", "מועד התשלום לכלל ספקי הממשלה יהיה 45 ימים")] + filler)
    results = index.search("תוך כמה ימים לשלם לספק", k=2)
    assert results[0][0] == "a"


def test_keyword_index_empty_query():
    assert KeywordIndex([_chunk("a", "טקסט")]).search("???", k=3) == []
