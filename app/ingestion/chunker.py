"""חלוקת מסמך לקטעים (chunks) לחיפוש.

הכללים:
  * כל סעיף ברמה 2 (למשל 2.2) הוא קטע, יחד עם כל תתי-הסעיפים שלו (2.2.1, 2.2.2...)
    ופסקאות לא ממוספרות שאחריו.
  * כותרת פרק (רמה 1) וכותרת נספח פותחות קטע חדש.
  * כמה סעיפים קצרים ברצף באותו פרק (למשל רשימת "מסמכים ישימים") מאוחדים לקטע אחד.
  * קטע ארוך מדי מפוצל לכמה חלקים עם חפיפה, ושורת הכותרת חוזרת בכל חלק.
  * בתחילת כל קטע מופיעים שם המסמך, המהדורה והפרק. זה משפר את החיפוש,
    ומאפשר למודל לדעת מאיזה מסמך ומאיזו מהדורה הקטע.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import settings
from app.models import Chunk, Clause, ParsedDocument

SMALL_CHUNK_CHARS = 300     # קטע קצר מזה יכול להתאחד עם הסמוך לו
MERGE_TARGET_CHARS = 1200   # גודל מקסימלי לקטע מאוחד


@dataclass
class _Group:
    chapter: str                       # "2 הנחיות לביצוע" או "נספח א – הגדרות"
    clauses: list[Clause] = field(default_factory=list)

    @property
    def body(self) -> str:
        return "\n".join(_clause_line(c) for c in self.clauses)


def _clause_line(c: Clause) -> str:
    if c.number:
        indent = "  " * max(c.level - 2, 0)
        return f"{indent}{c.number} {c.text}"
    return c.text


def _label(group: _Group) -> str:
    numbers = [c.number for c in group.clauses if c.number and not c.is_heading]
    if not numbers:
        return group.chapter
    first = numbers[0]
    top_level = [n for n in numbers if n.count(".") == first.count(".")]
    if len(top_level) > 1:
        return f"סעיפים {first}–{top_level[-1]}"
    return f"סעיף {first}"


def _group_clauses(clauses: list[Clause]) -> list[_Group]:
    groups: list[_Group] = []
    chapter = "פתיח"
    current: _Group | None = None
    for c in clauses:
        starts_chapter = c.is_heading
        starts_section = c.number is not None and c.level == 2
        if starts_chapter:
            chapter = f"{c.number} {c.text}" if c.number else c.text
            current = _Group(chapter=chapter, clauses=[c])
            groups.append(current)
        elif starts_section or current is None:
            current = _Group(chapter=chapter, clauses=[c])
            groups.append(current)
        else:
            current.clauses.append(c)
    # כותרת פרק לבד, בלי תוכן, מצטרפת לקטע שאחריה
    merged: list[_Group] = []
    pending_heading: list[Clause] = []
    for g in groups:
        if len(g.clauses) == 1 and g.clauses[0].is_heading and g.clauses[0].number:
            pending_heading = [g.clauses[0]]
            continue
        if pending_heading and g.chapter == (f"{pending_heading[0].number} {pending_heading[0].text}"):
            g.clauses = pending_heading + g.clauses
        pending_heading = []
        merged.append(g)
    return merged


def _merge_small(groups: list[_Group]) -> list[_Group]:
    out: list[_Group] = []
    for g in groups:
        prev = out[-1] if out else None
        if (
            prev is not None
            and prev.chapter == g.chapter
            and len(g.body) < SMALL_CHUNK_CHARS
            and len(prev.body) + len(g.body) < MERGE_TARGET_CHARS
            and all(len(_clause_line(c)) < SMALL_CHUNK_CHARS for c in prev.clauses if not c.is_heading)
        ):
            prev.clauses.extend(g.clauses)
        else:
            out.append(g)
    return out


def _split_long(lines: list[str], max_chars: int, overlap_chars: int) -> list[list[str]]:
    """מפצל רשימת שורות לחלקים של עד max_chars, עם חפיפה של שורות בסוף כל חלק."""
    parts, current, size = [], [], 0
    for line in lines:
        if current and size + len(line) > max_chars:
            parts.append(current)
            overlap, osize = [], 0
            for prev in reversed(current):
                if osize + len(prev) > overlap_chars:
                    break
                overlap.insert(0, prev)
                osize += len(prev)
            current, size = overlap, osize
        current.append(line)
        size += len(line)
    if current:
        parts.append(current)
    return parts


def chunk_document(doc: ParsedDocument) -> list[Chunk]:
    info = doc.info
    header = f"{info.display_name} | {info.edition_label}"
    chunks: list[Chunk] = []
    for group in _merge_small(_group_clauses(doc.clauses)):
        lines = [_clause_line(c) for c in group.clauses]
        parts = _split_long(lines, settings.chunk_max_chars, settings.chunk_overlap_chars)
        anchor = group.clauses[0].anchor
        label = _label(group)
        numbers = [c.number for c in group.clauses if c.number]
        for i, part in enumerate(parts):
            part_label = label if len(parts) == 1 else f"{label} (חלק {i + 1})"
            chunks.append(Chunk(
                chunk_id=f"{info.doc_id}::{anchor}::{i}",
                doc_id=info.doc_id,
                anchor=anchor,
                section_label=part_label,
                clause_numbers=numbers,
                text=f"{header}\nפרק: {group.chapter}\n" + "\n".join(part),
                is_current=info.is_current,
            ))
    return chunks
