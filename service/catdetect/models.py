"""Таблицы БД. Координаты на кадре везде нормализованные (0..1)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

# виды объектов: детектор, разметка, обучение и распознавание конкретных объектов
SPECIES = ("cat", "dog", "person")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json(default_factory=None, nullable=True):
    kw: dict[str, Any] = {"sa_column": Column(JSON, nullable=nullable)}
    if default_factory is not None:
        kw["default_factory"] = default_factory
    else:
        kw["default"] = None
    return Field(**kw)


class AppSetting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: Any = _json()


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    password_hash: str
    created_at: datetime = Field(default_factory=utcnow)


class ApiToken(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    token_hash: str = Field(unique=True, index=True)
    prefix: str
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: Optional[datetime] = None


class Nvr(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    host: str
    http_port: int = 80
    rtsp_port: int = 554
    https: bool = False
    username: str = "admin"
    password: str = ""
    event_codes: list[str] = _json(lambda: ["VideoMotion"])
    enabled: bool = True


class Camera(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    slug: str = Field(unique=True, index=True)  # для entity_id в HA: латиница, цифры, _
    name: str
    nvr_id: Optional[int] = Field(default=None, foreign_key="nvr.id")
    channel: int = 1  # как в интерфейсе регистратора, с 1
    stream: str = "sub"  # main | sub
    source_url: Optional[str] = None  # произвольный RTSP/файл вместо канала регистратора
    trigger: str = "motion"  # motion | always
    fps: float = 5.0
    linger: float = 10.0  # сек работы после окончания движения
    clear_after: float = 10.0  # сек удержания «присутствия» после пропадания
    species: list[str] = _json(lambda: ["cat", "dog"])
    zone: Optional[list[list[float]]] = _json()  # полигон [[x,y],...]
    # {"line": [[x,y],[x,y]], "door_point": [x,y], "margin": 0.02}
    direction: Optional[dict[str, Any]] = _json()
    min_conf: float = 0.25  # порог детектора на кадре
    confirm_hits: int = 3  # сколько кадров подряд нужно для подтверждения трека
    confirm_conf: float = 0.5  # средняя уверенность для подтверждения
    identity_conf: float = 0.6  # порог распознавания конкретного животного
    save_frames: bool = True  # сохранять кадры с детекциями в очередь разметки
    aspect: Optional[str] = None  # пропорции кадра «16:9», «4:3»…; None — как в потоке
    enabled: bool = True


class Identity(SQLModel, table=True):
    """Объект для распознавания: конкретное животное или человек либо группа («чужие кошки», «курьеры»)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    species: str = "cat"
    is_own: bool = True  # свой / чужой
    notes: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Image(SQLModel, table=True):
    __table_args__ = {"sqlite_autoincrement": True}  # id не переиспользуются после удаления
    id: Optional[int] = Field(default=None, primary_key=True)
    camera_id: Optional[int] = Field(default=None, foreign_key="camera.id", index=True)
    path: str  # относительно data_dir
    width: int
    height: int
    captured_at: datetime = Field(default_factory=utcnow, index=True)
    source: str = "auto"  # auto | upload
    status: str = Field(default="unlabeled", index=True)  # unlabeled | labeled | skipped
    # детекции модели на момент сохранения: [{"box":[x1,y1,x2,y2],"species":..,"conf":..,"identity_id":..}]
    predictions: list[dict[str, Any]] = _json(list)


class Annotation(SQLModel, table=True):
    """Найденный на кадре объект (рамка) — карточка в разделе «Объекты».

    state: pending — в «Неразобранных», assigned — в папке объекта (identity_id),
    rejected — в папке «Не объект» (ложное срабатывание, учит детектор не ошибаться).
    """

    __table_args__ = {"sqlite_autoincrement": True}
    id: Optional[int] = Field(default=None, primary_key=True)
    image_id: int = Field(foreign_key="image.id", index=True)
    x1: float
    y1: float
    x2: float
    y2: float
    species: str
    identity_id: Optional[int] = Field(default=None, foreign_key="identity.id")
    state: Optional[str] = Field(default="pending", index=True)
    conf: Optional[float] = None  # уверенность детектора, если рамку нашла модель
    suggested_identity_id: Optional[int] = None  # подсказка классификатора («Барсик?»)
    assigned_at: Optional[datetime] = None


class Event(SQLModel, table=True):
    __table_args__ = {"sqlite_autoincrement": True}
    id: Optional[int] = Field(default=None, primary_key=True)
    camera_id: int = Field(foreign_key="camera.id", index=True)
    ts: datetime = Field(default_factory=utcnow, index=True)
    kind: str  # seen | arrived | left | motion
    species: str  # для motion — вид лучшей детекции за эпизод или ""
    identity_id: Optional[int] = Field(default=None, foreign_key="identity.id")
    confidence: float = 0.0
    identity_confidence: Optional[float] = None
    track_id: int = 0
    snapshot_path: Optional[str] = None
    # для motion: {"duration": с, "frames": N, "best": {"species", "conf", "reason"} | None}
    details: Optional[dict[str, Any]] = _json()
    # исходный кадр без рамок и детекции на нём — чтобы событие можно было отправить в разметку
    raw_path: Optional[str] = None
    predictions: Optional[list[dict[str, Any]]] = _json()
    image_id: Optional[int] = Field(default=None, foreign_key="image.id")


class MlModel(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str  # detector | classifier
    name: str
    path: str  # относительно data_dir
    created_at: datetime = Field(default_factory=utcnow)
    metrics: dict[str, Any] = _json(dict)
    # для классификатора: {"имя класса": identity_id}
    classes: dict[str, Any] = _json(dict)
    active: bool = False


class TrainingJob(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str  # detector | classifier
    status: str = "queued"  # queued | running | done | failed | cancelled
    created_at: datetime = Field(default_factory=utcnow)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    params: dict[str, Any] = _json(dict)
    progress: float = 0.0
    message: str = ""
    model_id: Optional[int] = Field(default=None, foreign_key="mlmodel.id")
