"""«Обзор»: сводка по всем разделам и нагрузка на систему."""

from __future__ import annotations

import os
import platform
import time
from datetime import timedelta

import psutil
from fastapi import APIRouter
from sqlmodel import col, func, select

from ..labels import ASSIGNED, PENDING, REJECTED
from ..models import Annotation, Camera, Event, Identity, MlModel, Nvr, TrainingJob, utcnow
from ..recorder import event_to_dict
from .deps import Auth, Cfg, Db, Rt

router = APIRouter(tags=["overview"])

_PROC = psutil.Process(os.getpid())
_STARTED = time.time()
psutil.cpu_percent(interval=None)  # первый вызов всегда 0 — «прогреваем» замер
_PROC.cpu_percent(interval=None)
_last_calls: tuple[float, int] | None = None  # (время, число кадров) для расчёта кадров/сек


def _gpu_info(rt) -> dict | None:
    comp = rt.compute
    if comp is None or comp.kind != "gpu":
        return None
    try:
        import torch

        if comp.device.startswith("cuda"):
            idx = torch.device(comp.device).index or 0
            free, total = torch.cuda.mem_get_info(idx)
            return {"name": comp.label, "mem_used": total - free, "mem_total": total}
        if comp.device == "mps":
            return {"name": comp.label, "mem_used": torch.mps.current_allocated_memory(), "mem_total": None}
    except Exception:  # noqa: BLE001 — нагрузка видеокарты необязательна
        return {"name": comp.label, "mem_used": None, "mem_total": None}
    return None


def _system(rt, cfg) -> dict:
    global _last_calls
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(str(cfg.data_dir))
    det = rt.detector
    fps = None
    if det is not None:
        now = time.time()
        if _last_calls is not None and now - _last_calls[0] > 0.5:
            fps = round((det.calls - _last_calls[1]) / (now - _last_calls[0]), 1)
        _last_calls = (now, det.calls)
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except (OSError, AttributeError):  # нет на Windows
        load = None
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_count": psutil.cpu_count(),
        "load_avg": load,
        "process_cpu_percent": round(_PROC.cpu_percent(interval=None) / (psutil.cpu_count() or 1), 1),
        "mem_percent": vm.percent,
        "mem_used": vm.total - vm.available,
        "mem_total": vm.total,
        "process_rss": _PROC.memory_info().rss,
        "disk_percent": disk.percent,
        "disk_used": disk.used,
        "disk_total": disk.total,
        "uptime": int(time.time() - _STARTED),
        "platform": f"{platform.system()} {platform.machine()}",
        "compute": rt.compute.as_dict() if rt.compute else None,
        "detector_status": rt.detector_info.get("status"),
        "inference_ms": round(det.avg_ms, 1) if det is not None and det.avg_ms is not None else None,
        "inference_fps": fps,
        "gpu": _gpu_info(rt),
    }


@router.get("/api/overview")
def overview(_: Auth, db: Db, rt: Rt, cfg: Cfg):
    st = rt.state.snapshot()
    cams = db.exec(select(Camera).order_by(Camera.id)).all()
    nvrs = db.exec(select(Nvr)).all()
    listeners = rt.listener_status()

    by_state = dict(db.exec(select(Annotation.state, func.count()).group_by(Annotation.state)).all())
    by_ident = dict(db.exec(select(Annotation.identity_id, func.count())
                            .where(Annotation.state == ASSIGNED, col(Annotation.identity_id).is_not(None))
                            .group_by(Annotation.identity_id)).all())
    idents = db.exec(select(Identity)).all()
    folders = sorted(({"id": i.id, "name": i.name, "species": i.species, "count": by_ident.get(i.id, 0)}
                      for i in idents), key=lambda f: -f["count"])

    now = utcnow()
    day_start = now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    count = lambda *w: db.exec(select(func.count()).select_from(Event).where(*w)).one()  # noqa: E731
    last_ev = db.exec(select(Event).where(Event.kind != "motion").order_by(col(Event.id).desc())).first()

    jobs = db.exec(select(TrainingJob).order_by(col(TrainingJob.id).desc())).all()
    active = {m.kind: m for m in db.exec(select(MlModel).where(MlModel.active == True)).all()}  # noqa: E712
    last_cls = db.exec(select(MlModel).where(MlModel.kind == "classifier").order_by(col(MlModel.id).desc())).first()
    q = select(func.count()).select_from(Annotation).where(Annotation.state == ASSIGNED)
    if last_cls is not None:
        q = q.where(Annotation.assigned_at > last_cls.created_at)

    def job_out(j):
        return None if j is None else {
            "id": j.id, "kind": j.kind, "status": j.status, "created_at": j.created_at, "finished_at": j.finished_at,
            "progress": j.progress, "message": j.message,
        }

    return {
        "cameras": {
            "total": len(cams),
            "enabled": sum(c.enabled for c in cams),
            "online": sum(1 for c in cams if (st["cameras"].get(c.id) or {}).get("online")),
            "motion": sum(1 for c in cams if (st["cameras"].get(c.id) or {}).get("motion")),
            "items": [{"id": c.id, "name": c.name, "enabled": c.enabled, **{k: (st["cameras"].get(c.id) or {}).get(k)
                       for k in ("online", "motion")}} for c in cams],
        },
        "nvrs": {"total": len(nvrs), "enabled": sum(n.enabled for n in nvrs),
                 "connected": sum(1 for v in listeners.values() if v)},
        "objects": {
            "folders": len(idents), "assigned": by_state.get(ASSIGNED, 0), "pending": by_state.get(PENDING, 0),
            "rejected": by_state.get(REJECTED, 0), "top": folders[:5],
            "by_species": {sp: sum(1 for i in idents if i.species == sp) for sp in ("cat", "dog", "person")},
        },
        "events": {
            "today": count(Event.kind != "motion", Event.ts >= day_start),
            "last_24h": count(Event.kind != "motion", Event.ts >= now - timedelta(hours=24)),
            "motion_today": count(Event.kind == "motion", Event.ts >= day_start),
            "total": count(Event.kind != "motion"),
            "last": event_to_dict(last_ev) if last_ev else None,
        },
        "training": {
            "jobs": len(jobs),
            "done": sum(1 for j in jobs if j.status == "done"),
            "running": job_out(next((j for j in jobs if j.status in ("queued", "running")), None)),
            "last": job_out(next((j for j in jobs if j.status in ("done", "failed")), None)),
            "active": {k: {"id": m.id, "name": m.name, "created_at": m.created_at} for k, m in active.items()},
            "new_since_last": db.exec(q).one(),
        },
        "system": _system(rt, cfg),
    }
