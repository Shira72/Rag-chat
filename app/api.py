"""השרת: מגיש את מסך הצ'אט, עונה על שאלות ומציג מסמכים.

הרצה:
    uvicorn app.api:app --reload
ואז פותחים בדפדפן: http://localhost:8000
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import PROJECT_ROOT, settings
from app.doc_view import render_document
from app.knowledge_base import get_kb, reload
from app.llm.answerer import AnswerError, Answerer
from app.models import ChatRequest, ChatResponse

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("api")

WEB_DIR = PROJECT_ROOT / "web"

app = FastAPI(title="צ'אט הוראות תכ\"ם")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
def health() -> dict:
    kb = get_kb()
    return {"documents": len(kb.docs), "chunks": len(kb.chunks), "chat_model": settings.chat_model}


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    # פונקציה רגילה (לא async): FastAPI מריץ אותה ב-thread נפרד, כך שהמתנה ל-OpenAI לא חוסמת בקשות אחרות
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="יש לכתוב שאלה.")
    if len(question) > settings.max_question_chars:
        raise HTTPException(status_code=400, detail=f"השאלה ארוכה מדי (עד {settings.max_question_chars} תווים).")
    history = [m for m in request.history if m.content.strip()][-settings.max_history_messages:]
    try:
        return Answerer(get_kb()).answer(question, history)
    except AnswerError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.post("/api/reload")
def reload_index() -> dict:
    """טעינה מחדש של האינדקס אחרי הרצת קליטה, בלי לעצור את השרת."""
    kb = reload()
    return {"documents": len(kb.docs), "chunks": len(kb.chunks)}


@app.get("/doc/{doc_id}", response_class=HTMLResponse)
def view_document(doc_id: str) -> HTMLResponse:
    kb = get_kb()
    doc = kb.docs.get(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="המסמך לא נמצא.")
    return HTMLResponse(render_document(doc, kb))


@app.exception_handler(Exception)
def unexpected_error(_request, exc: Exception) -> JSONResponse:
    log.exception("שגיאה לא צפויה", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "אירעה שגיאה לא צפויה. נסו שוב."})
