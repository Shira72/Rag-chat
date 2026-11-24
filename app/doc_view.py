"""דף צפייה במסמך: HTML שנבנה מהמסמך המפורסר.

מכיוון שהדף נבנה מאותם נתונים שמהם נבנו הקטעים, העוגנים (#sec-2-2-13) תמיד
תואמים לקישורים שבתשובות. הסעיף שהקישור מפנה אליו מודגש (CSS :target).
"""
from __future__ import annotations

import html
import re

from app.knowledge_base import KnowledgeBase
from app.models import Clause, ParsedDocument


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _with_links(clause: Clause) -> str:
    """טקסט הסעיף, כשטקסט של קישור חיצוני הופך לקישור לחיץ."""
    out = _esc(clause.text)
    for link in clause.links:
        label = _esc(link.text)
        if label and label in out:
            anchor = f'<a href="{_esc(link.url)}" target="_blank" rel="noopener noreferrer">{label}</a>'
            out = out.replace(label, anchor, 1)
    return out


def _table_html(markdown: str) -> str:
    rows = [line for line in markdown.splitlines() if line.startswith("|") and not re.fullmatch(r"\|(---\|)+", line)]
    cells = [[_esc(c.strip()) for c in row.strip("|").split("|")] for row in rows]
    if not cells:
        return ""
    head = "".join(f"<th>{c}</th>" for c in cells[0])
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in cells[1:])
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _clause_html(c: Clause) -> str:
    if c.text.startswith("|"):
        return f'<div id="{c.anchor}" class="clause">{_table_html(c.text)}</div>'
    if c.is_heading:
        num = f'<span class="num">{_esc(c.number)}</span> ' if c.number else ""
        return f'<h2 id="{c.anchor}" class="clause heading">{num}{_esc(c.text)}</h2>'
    if c.number:
        return (f'<p id="{c.anchor}" class="clause level-{min(c.level, 5)}">'
                f'<span class="num">{_esc(c.number)}</span> {_with_links(c)}</p>')
    return f'<p id="{c.anchor}" class="clause plain">{_with_links(c)}</p>'


def render_document(doc: ParsedDocument, kb: KnowledgeBase) -> str:
    info = doc.info
    banner = ""
    if not info.is_current:
        current_id = kb.current_doc_in_group(info.group_key)
        link = (f' <a href="/doc/{_esc(current_id)}">למהדורה הנוכחית ←</a>' if current_id else "")
        banner = (f'<div class="banner old">שימו לב: זו מהדורה קודמת, והיא אינה בתוקף.{link}</div>')
    body = "\n".join(_clause_html(c) for c in doc.clauses)
    return f"""<!doctype html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(info.display_name)}</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body class="doc-page">
<header class="doc-header">
  <a class="back" href="/">→ חזרה לצ'אט</a>
  <h1>{_esc(info.display_name)}</h1>
  <div class="edition {'current' if info.is_current else 'old'}">{_esc(info.edition_label)}</div>
</header>
{banner}
<main class="doc-body">
{body}
</main>
</body>
</html>"""
