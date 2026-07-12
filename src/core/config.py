"""Настройки из .env — API акселератора, LLM, прокси, презентации."""
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    """Настройки приложения, загружаемые из .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_title: str = "accelerator_agent"

    # ─── API акселератора ───────────────────────────────────────────────────
    # Базовый URL БЕЗ /api — префикс роутера добавляется в клиенте.
    api_base_url: str = "http://localhost:8000"
    api_timeout: float = 30.0
    api_verify_ssl: bool = False

    # Токен по умолчанию — только для локальной отладки в Studio.
    # В проде токен приходит в context (configurable.user_token) на каждый run.
    dev_user_token: str | None = None

    # ─── LLM (OpenRouter / kodik_router / локальная — OpenAI-совместимая) ────
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str = "local-model"
    llm_temperature: float = 0.2
    llm_max_concurrency: int = 3

    # ─── Прокси для LLM (IS_PROXY=true) ─────────────────────────────────────
    is_proxy: bool = False
    proxy_host: str | None = None
    proxy_port: str | None = None
    proxy_user: str | None = None
    proxy_pass: str | None = None
    proxy_scheme: str | None = None  # http / socks5h; по умолчанию — по порту

    # ─── Сбор требований ────────────────────────────────────────────────────
    # Сколько вопросов задаём за один ход (чтобы не утомлять).
    questions_per_turn: int = 3
    # Сколько ходов вопросов минимум, прежде чем предложить "хватит".
    min_question_rounds: int = 1
    # Порог «много информации» → строгий генератор ТЗ вместо креативного.
    rich_info_threshold: int = 6

    # ─── Подбор команды ─────────────────────────────────────────────────────
    candidates_per_role: int = 3
    candidates_pool_size: int = 30

    # ─── Презентация (бизнес-логика; провайдер gamma|local|off) ─────────────
    presentation_provider: str = "off"
    gamma_api_key: str | None = None
    gamma_base_url: str = "https://public-api.gamma.app/v1.0"
    gamma_export_as: str = "pptx"
    gamma_poll_interval: float = 5.0
    gamma_poll_timeout: float = 180.0

    # ─── Скачивание файлов (ТЗ существующего проекта) ───────────────────────
    doc_cache_dir: str = "output/doc_cache"
    doc_download_timeout: float = 30.0
    doc_download_max_bytes: int = 25 * 1024 * 1024  # 25 МБ

    langsmith_project: str | None = None

    @property
    def project_root(self) -> Path:
        """Возвращает корневую директорию проекта."""
        return Path(__file__).resolve().parent.parent.parent

    @property
    def output_dir(self) -> Path:
        """Возвращает директорию выходных артефактов."""
        return self.project_root / "output"

    @property
    def doc_cache_dir_path(self) -> Path:
        """Абсолютный путь к каталогу кеша скачанных документов."""
        p = Path(self.doc_cache_dir)
        return p if p.is_absolute() else self.project_root / p

    @property
    def api_prefix(self) -> str:
        """Полный базовый URL API акселератора (с /api)."""
        return f"{self.api_base_url.rstrip('/')}/api"

    @property
    def proxy_url(self) -> str | None:
        """Формирует URL прокси для LLM, если прокси включён."""
        if not self.is_proxy or not self.proxy_host or not self.proxy_port:
            return None
        scheme = self.proxy_scheme
        if not scheme:
            scheme = "socks5h" if self.proxy_port in ("1080", "1081", "9050") else "http"
        auth = f"{self.proxy_user}:{self.proxy_pass}@" if self.proxy_user else ""
        return f"{scheme}://{auth}{self.proxy_host}:{self.proxy_port}"


settings = Settings()
