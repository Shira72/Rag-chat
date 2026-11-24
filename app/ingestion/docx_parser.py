"""קריאת קובץ DOCX והפיכתו ל-ParsedDocument: פרטי המסמך ורשימת סעיפים לפי הסדר.

מה נקרא מהקובץ:
  * פרטי המסמך (סוג, מספר, שם, מהדורה) מהכותרת העליונה של העמוד.
  * תאריך המהדורה מהשורה האחרונה בטבלת השינויים.
  * הסעיפים, עם המספור ש-Word מציג (מחושב ב-word_numbering).
  * טבלאות, שמומרות לטקסט בפורמט markdown.
  * קישורים חיצוניים בכל פסקה.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import docx
from docx.document import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.ingestion.word_numbering import WordNumbering
from app.models import Clause, DocumentInfo, Link, ParsedDocument

# ---------- ניקוי טקסט ----------

_INVISIBLE = re.compile(r"[\u200E\u200F\u202A-\u202E\u2066-\u2069﻿]")


def clean(text: str) -> str:
    """מסיר תווי כיווניות נסתרים, מאחד גרשיים כפולים ורווחים."""
    text = _INVISIBLE.sub("", text or "")
    text = text.replace("''", '"').replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_doc_id(path: Path) -> str:
    """מזהה לכתובת URL: אותיות באנגלית, ספרות, נקודה, מקף."""
    return re.sub(r"[^A-Za-z0-9.\-]+", "-", path.stem).strip("-") or "doc"


# ---------- פרטי המסמך מהכותרת העליונה ----------

_NUMBER_LABEL = re.compile(r"^(מס['׳]?\s*הוראה|מספר הוראה|מס['׳]?)\s*:?$")
_EDITION_LABEL = re.compile(r"^מהדורה\s*:?$")
_CHAPTER_LABEL = re.compile(r"^פרק (ראשי|משני)\s*:?$")
_INSTRUCTION_NUMBER = re.compile(r"(?:ט\.)?\d+(?:\.\d+)+")
_DATE = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{4}\b")
_BOILERPLATE = re.compile(r'^(הוראת\s*תכ"ם\s*:?|טופס|משרד האוצר.*|אגף החשב הכללי.*|תכ"ם\s*–.*)$')


def _header_tokens(doc: Document) -> list[str]:
    """כל הפסקאות בכותרות העליונות, לפי הסדר ובלי כפילויות.

    עוברים ישירות על ה-XML, כי הפרטים יושבים לפעמים בתוך תיבות טקסט
    או טבלאות מקוננות, ש-python-docx לא חושף.
    """
    w_p, w_t = qn("w:p"), qn("w:t")
    tokens: list[str] = []
    for section in doc.sections:
        headers = [section.header]
        if section.different_first_page_header_footer:
            headers.append(section.first_page_header)
        for header in headers:
            try:
                root = header._element
            except (AttributeError, KeyError):
                continue
            for p in root.iter(w_p):
                # רק טקסט שהפסקה הקרובה אליו היא p, כדי לא לספור פעמיים תיבת טקסט שבתוך פסקה
                parts = [t.text or "" for t in p.iter(w_t) if next(t.iterancestors(w_p)) is p]
                tokens.append(clean("".join(parts)))
    seen, unique = set(), []
    for t in tokens:
        if t and t not in seen:
            seen.add(t)
            unique.append(t)
    return unique


def _edition_date(doc: Document) -> str | None:
    """התאריך בשורה האחרונה של טבלת השינויים (המהדורה העדכנית)."""
    for table in reversed(doc.tables):
        text = clean(" ".join(c.text for row in table.rows for c in row.cells))
        if "טבלת שינויים" in text or ("מהדורה" in text and "תאריך" in text):
            dates = _DATE.findall(text)
            if dates:
                return dates[-1]
    return None


def parse_document_info(doc: Document, path: Path, file_hash: str) -> DocumentInfo:
    tokens = _header_tokens(doc)
    joined = " | ".join(tokens)

    number = edition = None
    consumed: set[int] = set()
    for i, tok in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if _NUMBER_LABEL.match(tok) and _INSTRUCTION_NUMBER.search(nxt):
            number = number or _INSTRUCTION_NUMBER.search(nxt).group(0)
            consumed |= {i, i + 1}
        elif _EDITION_LABEL.match(tok) and re.search(r"\d+", nxt):
            edition = edition or str(int(re.search(r"\d+", nxt).group(0)))
            consumed |= {i, i + 1}
        elif _CHAPTER_LABEL.match(tok):
            consumed |= {i, i + 1}

    # גיבוי: חיפוש בכל הטקסט, ואחר כך בשם הקובץ (למשל H.1.4.3)
    if edition is None and (m := re.search(r"מהדורה\s*:?\s*(\d+)", joined)):
        edition = str(int(m.group(1)))
    if number is None and (m := _INSTRUCTION_NUMBER.search(joined) or _INSTRUCTION_NUMBER.search(path.stem)):
        number = m.group(0)

    if "טופס" in tokens or (number or "").startswith("ט."):
        doc_type = "form"
    elif re.search(r'הוראת\s*תכ"ם', joined) or path.stem.startswith("H."):
        doc_type = "instruction"
    else:
        doc_type = "other"

    # השם: הטקסט בכותרת שאינו תווית, ערך של תווית או טקסט קבוע
    candidates = [
        t for i, t in enumerate(tokens)
        if i not in consumed
        and not _BOILERPLATE.match(t)
        and not _INSTRUCTION_NUMBER.fullmatch(t)
        and "מהדורה" not in t
    ]
    title = candidates[-1] if candidates else path.stem

    return DocumentInfo(
        doc_id=make_doc_id(path),
        filename=path.name,
        file_hash=file_hash,
        doc_type=doc_type,
        number=number,
        title=title,
        edition=edition,
        edition_date=_edition_date(doc),
        group_key=f"{doc_type}:{title}",
    )


# ---------- גוף המסמך ----------

def _links(p: Paragraph) -> list[Link]:
    out = []
    for h in getattr(p, "hyperlinks", []):
        url = (h.address or "").strip()
        if url.startswith(("http://", "https://")):
            out.append(Link(text=clean(h.text) or url, url=url))
    return out


def _table_to_markdown(table: Table, numbering: WordNumbering) -> str:
    rows: list[list[str]] = []
    for row in table.rows:
        cells, seen = [], set()
        for cell in row.cells:
            if id(cell._tc) in seen:          # תא ממוזג מופיע כמה פעמים. לוקחים פעם אחת
                continue
            seen.add(id(cell._tc))
            for p in cell.paragraphs:         # מקדם את המונים, כמו ש-Word סופר גם בתוך טבלאות
                numbering.number_for(p._p)
            cells.append(clean(cell.text).replace("|", "/"))
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _anchor_for(number: str) -> str:
    return "sec-" + re.sub(r"[^0-9A-Za-z]+", "-", number).strip("-")


def parse_body(doc: Document) -> list[Clause]:
    numbering = WordNumbering(doc)
    clauses: list[Clause] = []
    used_anchors: set[str] = set()
    main_list: str | None = None     # הרשימה של סעיפי ההוראה. רשימות אחרות הן רשימות פנימיות
    in_appendix = False
    appendix_count = 0

    def unique(anchor: str) -> str:
        base, n = anchor, 2
        while anchor in used_anchors:
            anchor, n = f"{base}-{n}", n + 1
        used_anchors.add(anchor)
        return anchor

    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]

        if tag == "tbl":
            md = _table_to_markdown(Table(child, doc), numbering)
            if md:
                clauses.append(Clause(number=None, level=0, anchor=unique(f"p-{len(clauses)}"), text=md))
            continue
        if tag != "p":
            continue

        p = Paragraph(child, doc)
        result = numbering.number_for(child)   # תמיד, גם לפסקה ריקה, כדי שהמונים יהיו נכונים
        text = clean(p.text)
        if not text:
            continue
        style = p.style.name if p.style is not None else ""

        # נספחים: "נספח א" ואחריו התיאור ("הגדרות") הופכים לכותרת אחת
        if style.startswith("נספח - כותרת") or re.fullmatch(r"נספח [א-ת]['׳]?", text):
            in_appendix = True
            appendix_count += 1
            clauses.append(Clause(number=None, level=1, anchor=unique(f"appendix-{appendix_count}"),
                                  text=text, is_heading=True))
            continue
        if style.startswith("נספח - תיאור") and clauses and clauses[-1].is_heading and in_appendix:
            clauses[-1].text = f"{clauses[-1].text} – {text}"
            continue

        links = _links(p)
        if result and result.label and main_list is None and not in_appendix:
            main_list = result.abstract_id

        if result and result.label and result.abstract_id == main_list and not in_appendix:
            clauses.append(Clause(
                number=result.label,
                level=result.level + 1,
                anchor=unique(_anchor_for(result.label)),
                text=text,
                is_heading=result.level == 0,
                links=links,
            ))
        else:
            # פסקה לא ממוספרת, או פריט ברשימה פנימית (למשל הגדרה בנספח)
            if result and result.label:
                text = f"{'  ' * result.level}{result.label}. {text}"
            elif result:                       # תבליט
                text = f"{'  ' * result.level}• {text}"
            clauses.append(Clause(number=None, level=0, anchor=unique(f"p-{len(clauses)}"),
                                  text=text, links=links))
    return clauses


def parse_docx(path: Path) -> ParsedDocument:
    """נקודת הכניסה: קובץ DOCX -> ParsedDocument. זורקת שגיאה אם הקובץ פגום."""
    doc = docx.Document(str(path))
    info = parse_document_info(doc, path, file_sha256(path))
    return ParsedDocument(info=info, clauses=parse_body(doc))
