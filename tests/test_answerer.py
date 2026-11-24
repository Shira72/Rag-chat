"""בדיקות לאימות הציטוטים: המנגנון שמונע מקורות מומצאים. בלי קריאה ל-OpenAI."""
from app.llm.answerer import Answerer, _Citation, _LlmAnswer
from app.llm.prompts import NOT_FOUND_ANSWER
from app.models import Chunk, Clause, DocumentInfo, RetrievedChunk


class FakeKB:
    """מאגר מינימלי עם מסמך אחד ושני קטעים."""

    def __init__(self):
        self.info_ = DocumentInfo(doc_id="D", filename="D.docx", file_hash="x", doc_type="instruction",
                                  number="1.4.3", title="מועדי תשלום", edition="6", edition_date="19.09.2024",
                                  group_key="g")
        self.clauses = {n: Clause(number=n, level=n.count(".") + 1, anchor="sec-" + n.replace(".", "-"), text="...")
                        for n in ["2.1", "2.2", "2.6"]}

    def info(self, doc_id):
        return self.info_

    def clause(self, doc_id, number):
        return self.clauses.get(number)


def _retrieved(anchor: str, numbers: list[str]) -> RetrievedChunk:
    chunk = Chunk(chunk_id=f"D::{anchor}::0", doc_id="D", anchor=anchor, section_label="x",
                  clause_numbers=numbers, text="...", is_current=True)
    return RetrievedChunk(chunk=chunk, score=1.0, similarity=0.5)


RETRIEVED = [_retrieved("sec-2", ["2", "2.1", "2.2"]), _retrieved("sec-2-6", ["2.6"])]


def _build(llm: _LlmAnswer):
    return Answerer(FakeKB())._build_response(llm, RETRIEVED)


def test_valid_citation_links_to_exact_clause():
    r = _build(_LlmAnswer(found=True, answer="45 ימים [S1].", citations=[_Citation(source_id="S1", clause="2.1")]))
    assert r.found
    assert r.sources[0].url == "/doc/D#sec-2-1"
    assert r.answer == "45 ימים [1]."


def test_citation_of_unknown_source_is_dropped():
    r = _build(_LlmAnswer(found=True, answer="משהו [S9].", citations=[_Citation(source_id="S9", clause="2.1")]))
    assert not r.found
    assert r.answer == NOT_FOUND_ANSWER


def test_found_without_citations_becomes_not_found():
    r = _build(_LlmAnswer(found=True, answer="תשובה בלי מקור", citations=[]))
    assert not r.found


def test_clause_attributed_to_wrong_source_is_fixed():
    # 2.6 נמצא במקור S2, אבל המודל כתב S1
    r = _build(_LlmAnswer(found=True, answer="שני בודקים [S1].", citations=[_Citation(source_id="S1", clause="2.6")]))
    assert r.sources[0].url == "/doc/D#sec-2-6"


def test_clause_not_in_any_source_falls_back_to_chunk_start():
    r = _build(_LlmAnswer(found=True, answer="x [S1].", citations=[_Citation(source_id="S1", clause="7.7")]))
    assert r.sources[0].url == "/doc/D#sec-2"


def test_multiple_clauses_in_one_citation_are_split():
    r = _build(_LlmAnswer(found=True, answer="x [S1].", citations=[_Citation(source_id="S1", clause="2.1, 2.2")]))
    assert [s.url for s in r.sources] == ["/doc/D#sec-2-1", "/doc/D#sec-2-2"]


def test_found_false_returns_fixed_message():
    r = _build(_LlmAnswer(found=False, answer="", citations=[]))
    assert r.answer == NOT_FOUND_ANSWER and r.sources == []
