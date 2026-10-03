from __future__ import annotations

import threading
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlmodel import col, select

from ..models import MlModel, TrainingJob
from .deps import Auth, Cfg, Db, Rt, not_found

router = APIRouter(tags=["training"])


class JobIn(BaseModel):
    kind: Literal["detector", "classifier"]
    epochs: int | None = Field(None, ge=1, le=500)
    imgsz: int | None = Field(None, ge=64, le=1920)
    batch: int | None = Field(None, ge=1, le=256)


def _manager(request: Request):
    return request.app.state.training


@router.get("/api/training/jobs")
def list_jobs(_: Auth, db: Db, limit: int = 20):
    return db.exec(select(TrainingJob).order_by(col(TrainingJob.id).desc()).limit(limit)).all()


@router.post("/api/training/jobs")
def start_job(body: JobIn, request: Request, _: Auth):
    try:
        return _manager(request).start(body.kind, body.model_dump(exclude={"kind"}))
    except RuntimeError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e


@router.get("/api/training/jobs/{job_id}")
def get_job(job_id: int, request: Request, _: Auth, db: Db):
    job = db.get(TrainingJob, job_id)
    if job is None:
        raise not_found("Задача")
    return {**job.model_dump(), "log": _manager(request).log_tail(job_id)}


@router.post("/api/training/jobs/{job_id}/cancel")
def cancel_job(job_id: int, request: Request, _: Auth):
    if not _manager(request).cancel(job_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Задача не выполняется")
    return {"ok": True}


@router.get("/api/models")
def list_models(_: Auth, db: Db, rt: Rt):
    return {
        "models": db.exec(select(MlModel).order_by(col(MlModel.id).desc())).all(),
        "runtime": rt.detector_info,
    }


def _reload_models(rt) -> None:
    threading.Thread(target=rt.reload_models, name="model-loader", daemon=True).start()


@router.post("/api/models/{model_id}/activate")
def activate_model(model_id: int, _: Auth, db: Db, rt: Rt):
    m = db.get(MlModel, model_id)
    if m is None:
        raise not_found("Модель")
    for other in db.exec(select(MlModel).where(MlModel.kind == m.kind, MlModel.active == True)).all():  # noqa: E712
        other.active = False
        db.add(other)
    m.active = True
    db.add(m)
    db.commit()
    _reload_models(rt)
    return {"ok": True}


@router.post("/api/models/deactivate")
def deactivate(kind: Literal["detector", "classifier"], _: Auth, db: Db, rt: Rt):
    """Вернуться к базовой модели (детектор) или отключить распознавание личностей (классификатор)."""
    for m in db.exec(select(MlModel).where(MlModel.kind == kind, MlModel.active == True)).all():  # noqa: E712
        m.active = False
        db.add(m)
    db.commit()
    _reload_models(rt)
    return {"ok": True}


@router.delete("/api/models/{model_id}")
def delete_model(model_id: int, _: Auth, db: Db, cfg: Cfg):
    m = db.get(MlModel, model_id)
    if m is None:
        raise not_found("Модель")
    if m.active:
        raise HTTPException(status.HTTP_409_CONFLICT, "Нельзя удалить активную модель")
    for job in db.exec(select(TrainingJob).where(TrainingJob.model_id == model_id)).all():
        job.model_id = None
        db.add(job)
    (cfg.data_dir / m.path).unlink(missing_ok=True)
    db.delete(m)
    db.commit()
    return {"ok": True}
