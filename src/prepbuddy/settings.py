"""Application configuration loaded from environment variables and defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings shared by the CLI, API, UI, and tests."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="PREPBUDDY_", extra="ignore")

    db_url: str = "sqlite:///data/prepbuddy.sqlite"
    default_pdf_path: Path = Path("SLATEFALL_DOSSIER.pdf")
    data_dir: Path = Path("data")
    docs_dir: Path = Path("docs")
    outputs_dir: Path = Path("outputs")
    logs_dir: Path = Path("logs")
    mapping_file: Path = Path("config/section_mapping.json")

    llm_provider: Literal["auto", "gemini", "ollama", "fake"] = "auto"
    gemini_model: str = "gemini-3.1-flash-lite"
    gemini_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "PREPBUDDY_GEMINI_API_KEY"),
    )
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:4b-instruct"
    ollama_timeout_seconds: float = 900.0
    questions_per_section: int = 5

    def ensure_dirs(self) -> None:
        """Create runtime directories used for database, docs, logs, and exports."""
        for path in (self.data_dir, self.docs_dir, self.outputs_dir, self.logs_dir, self.mapping_file.parent):
            path.mkdir(parents=True, exist_ok=True)
        for path in (self.data_dir / "uploads", self.data_dir / "mappings", self.docs_dir / "mappings"):
            path.mkdir(parents=True, exist_ok=True)
