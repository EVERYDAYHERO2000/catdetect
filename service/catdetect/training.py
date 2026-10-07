"""Обучение моделей. Каждая задача — отдельный процесс (`python -m catdetect.training <job_id>`),
чтобы падение/нехватка памяти не роняли сервис и детекция продолжала работать."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from .models import MlModel, TrainingJob, utcnow
from .settings import Settings

log = logging.getLogger(__name__)

DEFAULT_PARAMS = {
    "detector": {"epochs": 60, "imgsz": 640, "batch": 16},
    "classifier": {"epochs": 40, "imgsz": 224, "batch": 32},
}


class TrainingManager:
    def __init__(self, settings: Settings, engine: Engine, device_for_training=None):
        self.settings = settings
        self.device_for_training = device_for_training  # () -> "cpu" | "cuda:0" | "mps"
        self.engine = engine
        self._proc: subprocess.Popen | None = None
        self._job_id: int | None = None
        self._lock = threading.Lock()
        self.on_finished = None  # callback(job_id) — перезагрузить модели

    def recover(self) -> None:
        """После перезапуска сервиса незавершённые задачи помечаются как прерванные."""
        with Session(self.engine) as s:
            for job in s.exec(select(TrainingJob).where(col(TrainingJob.status).in_(["queued", "running"]))).all():
                job.status, job.message, job.finished_at = "failed", "Прервано перезапуском сервиса", utcnow()
                s.add(job)
            s.commit()

    @property
    def busy(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, kind: str, params: dict) -> TrainingJob:
        with self._lock:
            if self.busy:
                raise RuntimeError("Уже идёт обучение — дождитесь окончания")
            merged = {**DEFAULT_PARAMS.get(kind, {}), **{k: v for k, v in params.items() if v is not None}}
            with Session(self.engine) as s:
                job = TrainingJob(kind=kind, params=merged)
                s.add(job)
                s.commit()
                s.refresh(job)
            env = {**os.environ, "CATDETECT_DATA_DIR": str(self.settings.data_dir)}
            if self.device_for_training is not None:
                env["CATDETECT_TRAIN_DEVICE"] = self.device_for_training()
            log_path = self.settings.training_dir / f"job_{job.id}.log"
            log_file = open(log_path, "wb")  # noqa: SIM115 — закрывается в _watch
            self._proc = subprocess.Popen(
                [sys.executable, "-m", "catdetect.training", str(job.id)],
                stdout=log_file, stderr=subprocess.STDOUT, env=env,
            )
            self._job_id = job.id
            threading.Thread(target=self._watch, args=(self._proc, job.id, log_file), daemon=True).start()
            return job

    def _watch(self, proc: subprocess.Popen, job_id: int, log_file) -> None:
        code = proc.wait()
        log_file.close()
        with Session(self.engine) as s:
            job = s.get(TrainingJob, job_id)
            if job and job.status in ("queued", "running"):
                job.status = "failed"
                job.message = job.message or f"Процесс обучения завершился с кодом {code}"
                job.finished_at = utcnow()
                s.add(job)
                s.commit()
        if self.on_finished:
            self.on_finished(job_id)

    def cancel(self, job_id: int) -> bool:
        with self._lock:
            if self._job_id != job_id or not self.busy:
                return False
            with Session(self.engine) as s:
                job = s.get(TrainingJob, job_id)
                job.status, job.message, job.finished_at = "cancelled", "Отменено", utcnow()
                s.add(job)
                s.commit()
            self._proc.terminate()
            return True

    def log_tail(self, job_id: int, lines: int = 60) -> str:
        path = self.settings.training_dir / f"job_{job_id}.log"
        if not path.exists():
            return ""
        text = path.read_bytes()[-20000:].decode("utf-8", "replace").replace("\r", "\n")
        return "\n".join([ln for ln in text.splitlines() if ln.strip()][-lines:])


# ---------------- код дочернего процесса ----------------

def _update(engine: Engine, job_id: int, **fields) -> None:
    with Session(engine) as s:
        job = s.get(TrainingJob, job_id)
        for k, v in fields.items():
            setattr(job, k, v)
        s.add(job)
        s.commit()


def _clean_metrics(metrics: dict) -> dict:
    out = {}
    for k, v in (metrics or {}).items():
        try:
            out[k.replace("metrics/", "")] = round(float(v), 4)
        except (TypeError, ValueError):
            pass
    return out


VERDICT_TEXT = {
    "better": "лучше, чем «{ref}» — стоит активировать",
    "worse": "хуже, чем «{ref}»",
    "same": "примерно так же, как «{ref}»",
    "current": "это активная модель",
}


def _compare(engine, settings, ds_dir: Path, model_id: int, job_id: int) -> str:
    """Сравнение после обучения или по кнопке; возвращает итог для сообщения задачи."""
    from .compare import compare_detectors

    res = compare_detectors(engine, settings, ds_dir, model_id, os.environ.get("CATDETECT_TRAIN_DEVICE"),
                            progress=lambda msg: _update(engine, job_id, message=msg))
    return f"сравнение на {res['val_images']} кадрах: {VERDICT_TEXT[res['verdict']].format(ref=res['reference'])}"


def run_job(job_id: int) -> None:
    from ultralytics import YOLO

    from .dataset import export_classify, export_detect
    from .db import make_engine
    from .settings import load_settings

    settings = load_settings()
    settings.prepare()
    engine = make_engine(settings.db_path)
    with Session(engine) as s:
        job = s.get(TrainingJob, job_id)
        kind, params = job.kind, dict(job.params)
    if job.status == "cancelled":
        return
    _update(engine, job_id, status="running", started_at=utcnow(), message="Подготовка датасета")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ds_dir = settings.datasets_dir / f"{kind}_{job_id}"
    class_map: dict[str, int] = {}
    try:
        if kind == "compare":  # только сравнение уже обученной модели
            export_detect(engine, settings.data_dir, ds_dir)
            summary = _compare(engine, settings, ds_dir, int(params["model_id"]), job_id)
            _update(engine, job_id, status="done", progress=1.0, finished_at=utcnow(),
                    model_id=int(params["model_id"]), message=f"Готово: {summary}")
            return
        if kind == "detector":
            data = str(export_detect(engine, settings.data_dir, ds_dir))
            base = settings.models_dir / "base" / settings.base_detector
        else:
            ds, class_map = export_classify(engine, settings.data_dir, ds_dir)
            data = str(ds)
            base = settings.models_dir / "base" / settings.base_classifier
        base.parent.mkdir(parents=True, exist_ok=True)

        model = YOLO(str(base))
        epochs = int(params["epochs"])

        def on_epoch_end(trainer):
            m = _clean_metrics(getattr(trainer, "metrics", {}))
            _update(engine, job_id, progress=round((trainer.epoch + 1) / epochs, 3),
                    message=f"Эпоха {trainer.epoch + 1}/{epochs}", params={**params, "last_metrics": m})

        model.add_callback("on_fit_epoch_end", on_epoch_end)
        _update(engine, job_id, message="Обучение")
        model.train(
            data=data, epochs=epochs, imgsz=int(params["imgsz"]), batch=int(params["batch"]),
            device=os.environ.get("CATDETECT_TRAIN_DEVICE") or None, project=str(settings.training_dir), name=f"{kind}_{job_id}",
            exist_ok=True, patience=max(10, epochs // 4), workers=int(params.get("workers", 2)),
            plots=False, verbose=False,
        )
        best = Path(model.trainer.best)
        if not best.exists():
            raise RuntimeError("Обучение не создало весов best.pt")
        dst_rel = f"models/{kind}_{job_id}_{stamp}.pt"
        shutil.copyfile(best, settings.data_dir / dst_rel)
        metrics = _clean_metrics(model.trainer.metrics)
        with Session(engine) as s:
            m = MlModel(kind=kind, name=f"{'Детектор' if kind == 'detector' else 'Классификатор'} #{job_id}",
                        path=dst_rel, metrics=metrics, classes=class_map)
            s.add(m)
            s.commit()
            s.refresh(m)
            model_id = m.id
        message = "Готово — активируйте модель, если метрики устраивают"
        if kind == "detector":
            try:
                message = f"Готово: {_compare(engine, settings, ds_dir, model_id, job_id)}"
            except Exception:  # noqa: BLE001 — сравнение не должно портить результат обучения
                log.exception("Сравнение после обучения не удалось")
        _update(engine, job_id, status="done", progress=1.0, finished_at=utcnow(), model_id=model_id, message=message)
    except Exception as e:  # noqa: BLE001
        log.exception("Обучение %s провалилось", job_id)
        _update(engine, job_id, status="failed", finished_at=utcnow(), message=str(e)[:500])
        raise SystemExit(1) from e


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_job(int(sys.argv[1]))
