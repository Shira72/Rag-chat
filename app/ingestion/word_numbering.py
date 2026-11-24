"""חישוב המספור האוטומטי של Word ("2.2.13").

בקבצים, המספרים של הסעיפים לא כתובים בטקסט. Word מחשב אותם בזמן התצוגה לפי:
  1. ה-numPr של הפסקה, או של הסגנון שלה (כולל סגנונות שהוא יורש מהם),
     שקובע איזו רשימה (numId) ובאיזו רמה (ilvl).
  2. ההגדרה של הרשימה ב-numbering.xml: ערך התחלה, פורמט (1, א, a) ותבנית ("%1.%2.").
  3. מונה לכל רמה. כשרמה עולה, כל הרמות שמתחתיה מתאפסות.

המחלקה כאן עושה את אותו החישוב, פסקה אחרי פסקה, לפי סדר המסמך.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from docx.document import Document
from docx.oxml.ns import qn

MAX_LEVELS = 9


@dataclass
class NumberingResult:
    abstract_id: str     # מזהה הרשימה. סעיפים מאותה רשימה שייכים לאותו מספור
    level: int           # 0 = רמה ראשונה
    label: str           # הטקסט שמוצג, למשל "2.2.13" או "א"


@dataclass
class _Level:
    start: int
    fmt: str
    text: str            # תבנית, למשל "%1.%2."
    is_legal: bool       # isLgl: כל הרמות מוצגות כמספרים רגילים
    pstyle: str | None   # סגנון שמקושר לרמה הזו


def _val(el, tag: str) -> str | None:
    child = el.find(qn(tag)) if el is not None else None
    return child.get(qn("w:val")) if child is not None else None


# ---------- פורמטים של מספרים ----------

_HEB_ONES = "אבגדהוזחט"
_HEB_TENS = "יכלמנסעפצ"
_HEB_HUNDREDS = "קרשת"


def _hebrew_gematria(n: int) -> str:
    """1->א, 10->י, 11->יא, 15->טו, 16->טז (כמו hebrew1 ב-Word)."""
    out = ""
    while n >= 400:
        out += "ת"
        n -= 400
    if n >= 100:
        out += _HEB_HUNDREDS[n // 100 - 1]
        n %= 100
    if n in (15, 16):
        return out + ("טו" if n == 15 else "טז")
    if n >= 10:
        out += _HEB_TENS[n // 10 - 1]
        n %= 10
    if n:
        out += _HEB_ONES[n - 1]
    return out


_HEB_ALPHABET = "אבגדהוזחטיכלמנסעפצקרשת"


def _letters(n: int, alphabet: str) -> str:
    """1->a, 26->z, 27->aa (כמו ש-Word סופר אותיות)."""
    if n <= 0:
        return str(n)
    size = len(alphabet)
    return alphabet[(n - 1) % size] * ((n - 1) // size + 1)


def _roman(n: int) -> str:
    vals = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
            (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
    out = ""
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out


def format_number(n: int, fmt: str) -> str:
    if fmt in ("decimal", "decimalZero", "decimalEnclosedCircle", "decimalFullWidth"):
        return f"{n:02d}" if fmt == "decimalZero" else str(n)
    if fmt == "hebrew1":
        return _hebrew_gematria(n)
    if fmt == "hebrew2":
        return _letters(n, _HEB_ALPHABET)
    if fmt == "lowerLetter":
        return _letters(n, "abcdefghijklmnopqrstuvwxyz")
    if fmt == "upperLetter":
        return _letters(n, "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    if fmt == "lowerRoman":
        return _roman(n)
    if fmt == "upperRoman":
        return _roman(n).upper()
    return str(n)


# ---------- המנוע ----------

class WordNumbering:
    """מחשב את המספור של כל פסקה. צריך לקרוא ל-`number_for` לפי סדר המסמך."""

    def __init__(self, doc: Document):
        self._abstract_levels: dict[str, dict[int, _Level]] = {}
        self._num_to_abstract: dict[str, str] = {}
        self._start_overrides: dict[str, dict[int, int]] = {}
        self._styles: dict[str, object] = {}        # styleId -> אלמנט הסגנון
        self._default_style: str | None = None
        self._counters: dict[str, list[int | None]] = {}
        self._used_overrides: set[tuple[str, int]] = set()
        self._load_styles(doc)
        self._load_numbering(doc)

    # --- טעינה ---

    def _load_styles(self, doc: Document) -> None:
        for st in doc.styles.element.findall(qn("w:style")):
            sid = st.get(qn("w:styleId"))
            self._styles[sid] = st
            if st.get(qn("w:type")) == "paragraph" and st.get(qn("w:default")) in ("1", "true"):
                self._default_style = sid

    def _load_numbering(self, doc: Document) -> None:
        try:
            root = doc.part.numbering_part.element
        except (KeyError, NotImplementedError, AttributeError):
            return  # אין במסמך רשימות ממוספרות

        style_links: dict[str, str] = {}  # abstractId -> שם סגנון רשימה (numStyleLink)
        for absn in root.findall(qn("w:abstractNum")):
            aid = absn.get(qn("w:abstractNumId"))
            link = _val(absn, "w:numStyleLink")
            if link:
                style_links[aid] = link
            levels = {}
            for lvl in absn.findall(qn("w:lvl")):
                ilvl = int(lvl.get(qn("w:ilvl")))
                levels[ilvl] = _Level(
                    start=int(_val(lvl, "w:start") or 1),
                    fmt=_val(lvl, "w:numFmt") or "decimal",
                    text=_val(lvl, "w:lvlText") or "",
                    is_legal=lvl.find(qn("w:isLgl")) is not None,
                    pstyle=_val(lvl, "w:pStyle"),
                )
            self._abstract_levels[aid] = levels

        for num in root.findall(qn("w:num")):
            nid = num.get(qn("w:numId"))
            self._num_to_abstract[nid] = _val(num, "w:abstractNumId")
            overrides = {}
            for ov in num.findall(qn("w:lvlOverride")):
                start = _val(ov, "w:startOverride")
                if start is not None:
                    overrides[int(ov.get(qn("w:ilvl")))] = int(start)
            self._start_overrides[nid] = overrides

        # רשימה שמפנה לסגנון רשימה (numStyleLink): משתמשים בהגדרות של הרשימה שהסגנון מצביע אליה
        for aid, style_id in style_links.items():
            st = self._styles.get(style_id)
            ppr = st.find(qn("w:pPr")) if st is not None else None
            numpr = ppr.find(qn("w:numPr")) if ppr is not None else None
            target_num = _val(numpr, "w:numId") if numpr is not None else None
            target_abs = self._num_to_abstract.get(target_num or "")
            if target_abs and target_abs != aid:
                for nid, a in list(self._num_to_abstract.items()):
                    if a == aid:
                        self._num_to_abstract[nid] = target_abs

    # --- חישוב ---

    def _style_chain(self, style_id: str | None) -> list[tuple[str, object]]:
        """הסגנון של הפסקה ואחריו כל הסגנונות שהוא יורש מהם (basedOn)."""
        chain, seen = [], set()
        sid = style_id or self._default_style
        while sid and sid not in seen and sid in self._styles:
            seen.add(sid)
            st = self._styles[sid]
            chain.append((sid, st))
            sid = _val(st, "w:basedOn")
        return chain

    def _resolve_num_pr(self, p_el) -> tuple[str | None, int | None, list[str]]:
        """מחזיר (numId, ilvl, שרשרת סגנונות). ערך שחסר בפסקה נלקח מהסגנון."""
        ppr = p_el.pPr
        num_id = ilvl = None
        style_id = None
        if ppr is not None:
            style_id = _val(ppr, "w:pStyle")
            numpr = ppr.find(qn("w:numPr"))
            if numpr is not None:
                num_id = _val(numpr, "w:numId")
                lv = _val(numpr, "w:ilvl")
                ilvl = int(lv) if lv is not None else None
        chain = self._style_chain(style_id)
        for _, st in chain:
            if num_id is not None and ilvl is not None:
                break
            sppr = st.find(qn("w:pPr"))
            snumpr = sppr.find(qn("w:numPr")) if sppr is not None else None
            if snumpr is None:
                continue
            if num_id is None:
                num_id = _val(snumpr, "w:numId")
            if ilvl is None:
                lv = _val(snumpr, "w:ilvl")
                ilvl = int(lv) if lv is not None else None
        return num_id, ilvl, [sid for sid, _ in chain]

    def number_for(self, p_el) -> NumberingResult | None:
        """המספור של פסקה (אלמנט w:p), או None אם היא לא ממוספרת. מקדם את המונים."""
        num_id, ilvl, style_ids = self._resolve_num_pr(p_el)
        if not num_id or num_id == "0":
            return None
        abs_id = self._num_to_abstract.get(num_id)
        levels = self._abstract_levels.get(abs_id or "")
        if not levels:
            return None

        if ilvl is None:
            # הסגנון לא ציין רמה: מחפשים רמה שמקושרת לסגנון הזה, אחרת רמה 0
            ilvl = next((i for i, lv in levels.items() if lv.pstyle in style_ids), 0)
        level = levels.get(ilvl)
        if level is None:
            return None

        counters = self._counters.setdefault(abs_id, [None] * MAX_LEVELS)
        override = self._start_overrides.get(num_id, {}).get(ilvl)
        if override is not None and (num_id, ilvl) not in self._used_overrides:
            self._used_overrides.add((num_id, ilvl))
            counters[ilvl] = override
        elif counters[ilvl] is None:
            counters[ilvl] = level.start
        else:
            counters[ilvl] += 1
        for deeper in range(ilvl + 1, MAX_LEVELS):
            counters[deeper] = None

        if level.fmt in ("bullet", "none"):
            return NumberingResult(abs_id, ilvl, "")

        def fill(match: re.Match) -> str:
            k = int(match.group(1)) - 1
            lv = levels.get(k)
            value = counters[k] if counters[k] is not None else (lv.start if lv else 1)
            fmt = "decimal" if (level.is_legal and k < ilvl) else (lv.fmt if lv else "decimal")
            return format_number(value, fmt)

        label = re.sub(r"%(\d)", fill, level.text)
        label = label.strip().strip(".)(").strip()
        return NumberingResult(abs_id, ilvl, label)
