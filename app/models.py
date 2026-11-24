"""מבני הנתונים של המערכת.

הנתונים עוברים כך:
    DOCX -> ParsedDocument (פרטי המסמך + רשימת Clause) -> Chunk -> RetrievedChunk -> ChatResponse
"""
from typing import Literal

from pydantic import BaseModel, Field


# ---------- קליטת מסמכים ----------

class Link(BaseModel):
    """קישור חיצוני שמופיע בתוך מסמך."""
    text: str
    url: str


class Clause(BaseModel):
    """פסקה אחת במסמך, בדרך כלל סעיף ממוספר כמו 2.2.13.

    פסקה בלי מספר (למשל המשך הסבר) נשמרת עם number=None, והיא שייכת
    לסעיף הממוספר שלפניה.
    """
    number: str | None          # "2.2.13". המספר מחושב כמו ש-Word מציג אותו
    level: int                  # 2.2.13 היא רמה 3. נספח או פסקה לא ממוספרת: 0
    anchor: str                 # מזהה ייחודי בתוך המסמך, למשל "sec-2-2-13"
    text: str                   # הטקסט, בלי המספר. טבלה מומרת לטקסט בפורמט markdown
    is_heading: bool = False    # כותרת פרק (רמה 1) או כותרת נספח
    links: list[Link] = Field(default_factory=list)


DocType = Literal["instruction", "form", "other"]


class DocumentInfo(BaseModel):
    """פרטי המסמך, כפי שהם מופיעים בכותרת העליונה של העמוד."""
    doc_id: str                 # מזהה יציב שנגזר משם הקובץ ומשמש בכתובות
    filename: str
    file_hash: str              # מאפשר לדלג על קבצים שלא השתנו
    doc_type: DocType
    number: str | None          # "1.4.3", "1.4.0.3" (מספור ישן) או "ט.1.4.6.1" (טופס)
    title: str                  # "מועדי תשלום"
    edition: str | None         # "6"
    edition_date: str | None    # "19.09.2024", התאריך האחרון בטבלת השינויים
    group_key: str              # מאחד מהדורות של אותה הוראה (לפי סוג ושם)
    is_current: bool = True     # נקבע בקליטה: המהדורה הגבוהה בכל קבוצה היא הנוכחית

    @property
    def display_name(self) -> str:
        """השם שמוצג למשתמש, למשל 'הוראת תכ"ם 1.4.3 – מועדי תשלום'."""
        kind = {"instruction": 'הוראת תכ"ם', "form": "טופס"}.get(self.doc_type, "מסמך")
        num = f" {self.number}" if self.number else ""
        return f"{kind}{num} – {self.title}"

    @property
    def edition_label(self) -> str:
        """למשל 'מהדורה 6 (19.09.2024) – נוכחית'."""
        parts = []
        if self.edition:
            parts.append(f"מהדורה {self.edition}")
        if self.edition_date:
            parts.append(f"({self.edition_date})")
        base = " ".join(parts)
        status = "נוכחית" if self.is_current else "מהדורה קודמת – לא בתוקף"
        return f"{base} – {status}" if base else status


class ParsedDocument(BaseModel):
    info: DocumentInfo
    clauses: list[Clause]


class Chunk(BaseModel):
    """קטע שנשמר באינדקס: סעיף ברמה 2 (למשל 2.2) עם כל תתי-הסעיפים שלו."""
    chunk_id: str               # "{doc_id}::{anchor}::{part}"
    doc_id: str
    anchor: str                 # העוגן של הסעיף הראשי בקטע
    section_label: str          # "2.2 חשב המשרד יוודא כי החשבון כולל..."
    clause_numbers: list[str]   # כל מספרי הסעיפים שבקטע, לאימות ציטוטים
    text: str                   # מה שעובר לאמבדינג ולמודל, כולל שם המסמך והפרק
    is_current: bool            # מאפשר להעדיף את המהדורה הנוכחית בחיפוש


# ---------- חיפוש ----------

class RetrievedChunk(BaseModel):
    chunk: Chunk
    score: float                # ציון מיזוג (RRF), משמש לסדר התוצאות
    similarity: float | None    # דמיון וקטורי, משמש לבדיקת הסף. None אם נמצא רק בחיפוש מילים


# ---------- API ----------

class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    question: str
    history: list[ChatMessage] = Field(default_factory=list)


class Source(BaseModel):
    """מקור שמוצג מתחת לתשובה, עם קישור לסעיף."""
    doc_name: str               # 'הוראת תכ"ם 1.4.3 – מועדי תשלום'
    edition_label: str          # 'מהדורה 6 (19.09.2024) – נוכחית'
    is_current: bool
    section_label: str          # "סעיף 2.2.13"
    url: str                    # "/doc/{doc_id}#{anchor}"
    links: list[Link] = Field(default_factory=list)


class ChatResponse(BaseModel):
    found: bool
    answer: str
    sources: list[Source] = Field(default_factory=list)
