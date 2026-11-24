"""כל ההגדרות של המערכת במקום אחד.

הערכים נקראים מקובץ .env או ממשתני סביבה. בשאר הקוד משתמשים ב-`settings`
ולא קוראים משתני סביבה ישירות.
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_parse_none_str="none",
        extra="ignore",
    )

    # OpenAI
    openai_api_key: str = ""
    chat_model: str = "gpt-4.1-mini"
    embed_model: str = "text-embedding-3-large"   # בבדיקות על המסמכים, מדויק משמעותית מ-small בעברית
    # 0 = תשובות עקביות ושמרניות. יש מודלים (מודלי reasoning) שלא מקבלים טמפרטורה,
    # ואז מגדירים ב-.env את הערך LLM_TEMPERATURE=none
    llm_temperature: float | None = 0.0

    # תיקיות
    docs_dir: Path = PROJECT_ROOT / "data" / "docs"
    processed_dir: Path = PROJECT_ROOT / "data" / "processed"
    index_dir: Path = PROJECT_ROOT / "data" / "index"

    # חיפוש
    top_k_vector: int = 15
    top_k_keyword: int = 10
    final_k: int = 8
    max_old_edition_chunks: int = 2   # כמה קטעים ממהדורות קודמות לכל היותר, כדי שלא ידחקו את הנוכחית
    min_similarity: float = 0.30

    # חלוקה לקטעים (בתווים: בעברית, תו אחד שווה בערך לחצי טוקן)
    chunk_max_chars: int = 2500
    chunk_overlap_chars: int = 300

    # הגבלות קלט
    max_question_chars: int = 1000
    max_history_messages: int = 6


settings = Settings()
