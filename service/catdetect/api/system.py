from __future__ import annotations

import threading
from typing import Literal

from pydantic import BaseModel, Field
from fastapi import APIRouter, Request

from .. import cleanup, compute
from ..models import AppSetting
from .deps import Auth, Db, Rt

router = APIRouter(prefix="/api/system", tags=["system"])


class ComputeIn(BaseModel):
    preference: Literal["auto", "cpu", "gpu"]


@router.get("/compute")
def get_compute(_: Auth, rt: Rt):
    return {
        "preference": rt.compute_preference(),
        "available": compute.available(),
        "active": rt.compute.as_dict() if rt.compute else None,
        "detector": rt.detector_status(),
    }


@router.put("/compute")
def set_compute(body: ComputeIn, _: Auth, db: Db, rt: Rt):
    """Переключить процессор/видеокарту: модели перезагружаются в фоне (детекция на несколько секунд пауза)."""
    row = db.get(AppSetting, "compute") or AppSetting(key="compute")
    row.value = body.preference
    db.add(row)
    db.commit()
    if rt.settings.run_pipeline:
        threading.Thread(target=rt.reload_models, name="model-loader", daemon=True).start()
    return {"ok": True, "preference": body.preference}


class RetentionIn(BaseModel):
    hours: float = Field(ge=0, le=24 * 365)


def _retention(request: Request, db) -> dict:
    cl = request.app.state.cleaner
    return {"hours": cleanup.retention_hours(request.app.state.engine), "last_run": cl.last_run,
            "last_result": cl.last_result}


@router.get("/retention")
def get_retention(request: Request, _: Auth, db: Db):
    """Очистка старых снимков без объектов: срок хранения (0 — выключено) и итог последнего запуска."""
    return _retention(request, db)


@router.put("/retention")
def set_retention(body: RetentionIn, request: Request, _: Auth, db: Db):
    row = db.get(AppSetting, "retention_hours") or AppSetting(key="retention_hours")
    row.value = body.hours
    db.add(row)
    db.commit()
    return _retention(request, db)


@router.post("/retention/run")
def run_retention(request: Request, _: Auth, db: Db):
    """Очистить сейчас, не дожидаясь часового запуска."""
    request.app.state.cleaner.run_now()
    return _retention(request, db)
