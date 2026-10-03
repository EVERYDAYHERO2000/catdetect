from __future__ import annotations

import threading
from typing import Literal

from pydantic import BaseModel
from fastapi import APIRouter

from .. import compute
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
