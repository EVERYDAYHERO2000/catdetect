"""Настройки сервиса.

Все секреты и данные лежат вне репозитория:
- env-файл: $CATDETECT_ENV или ~/.config/catdetect/.env
- каталог данных: $CATDETECT_DATA_DIR или ~/catdetect-data (БД, кадры, модели, ключ сессий)
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_ENV_FILE = Path.home() / ".config" / "catdetect" / ".env"


def _load_env() -> None:
    env_file = Path(os.environ.get("CATDETECT_ENV", DEFAULT_ENV_FILE)).expanduser()
    if env_file.is_file():
        load_dotenv(env_file, override=False)


def _default_web_dir() -> Path:
    # web/dist рядом с репозиторием при разработке; в Docker задаётся через CATDETECT_WEB_DIR
    return Path(__file__).resolve().parents[2] / "web" / "dist"


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path.home() / "catdetect-data")
    web_dir: Path = field(default_factory=_default_web_dir)
    secret_key: str = ""
    compute: str = "auto"  # auto | cpu | gpu — по умолчанию; меняется в веб-интерфейсе
    base_detector: str = "yolo26m.pt"
    base_classifier: str = "yolo26s-cls.pt"
    # формат модели при работе на процессоре: openvino | onnx | torch
    inference_format: str = "openvino"
    run_pipeline: bool = True
    cookie_secure: bool = False

    @property
    def db_path(self) -> Path:
        return self.data_dir / "catdetect.db"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def events_dir(self) -> Path:
        return self.data_dir / "events"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def datasets_dir(self) -> Path:
        return self.data_dir / "datasets"

    @property
    def training_dir(self) -> Path:
        return self.data_dir / "training"

    def prepare(self) -> None:
        for d in (self.data_dir, self.images_dir, self.events_dir, self.models_dir,
                  self.datasets_dir, self.training_dir):
            d.mkdir(parents=True, exist_ok=True)
        # каталог настроек ultralytics — тоже в данных, а не в домашней папке контейнера
        os.environ.setdefault("YOLO_CONFIG_DIR", str(self.data_dir / "ultralytics"))
        if not self.secret_key:
            key_file = self.data_dir / "secret_key"
            if not key_file.exists():
                key_file.write_text(secrets.token_urlsafe(48))
                try:
                    key_file.chmod(0o600)
                except OSError:
                    pass
            self.secret_key = key_file.read_text().strip()


def load_settings() -> Settings:
    _load_env()
    s = Settings()
    if v := os.environ.get("CATDETECT_DATA_DIR"):
        s.data_dir = Path(v).expanduser()
    if v := os.environ.get("CATDETECT_WEB_DIR"):
        s.web_dir = Path(v).expanduser()
    if v := os.environ.get("CATDETECT_SECRET_KEY"):
        s.secret_key = v
    if v := os.environ.get("CATDETECT_COMPUTE"):
        s.compute = v.lower()
    if v := os.environ.get("CATDETECT_BASE_DETECTOR"):
        s.base_detector = v
    if v := os.environ.get("CATDETECT_BASE_CLASSIFIER"):
        s.base_classifier = v
    if v := os.environ.get("CATDETECT_INFERENCE_FORMAT"):
        s.inference_format = v.lower()
    if os.environ.get("CATDETECT_DISABLE_PIPELINE", "").lower() in ("1", "true", "yes"):
        s.run_pipeline = False
    if os.environ.get("CATDETECT_COOKIE_SECURE", "").lower() in ("1", "true", "yes"):
        s.cookie_secure = True
    return s
