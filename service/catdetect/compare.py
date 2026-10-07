"""Честное сравнение моделей поиска (детекторов) на одних и тех же проверочных кадрах.

Проверочные кадры — каждый 5-й размеченный (dataset.VAL_EVERY): ни одна модель на них не обучается,
поэтому новая, активная и стандартная модели сравниваются на равных. Запускается в процессе обучения
(после него) или отдельной задачей «Сравнить».
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from .models import SPECIES, MlModel, utcnow
from .settings import Settings

log = logging.getLogger(__name__)

# разница mAP50-95, меньше которой модели считаются равными (шум на сотнях кадров)
SAME = 0.01


def _val(weights: str, data: str, project: Path, name: str, device: str | None, classes=None) -> Any:
    from ultralytics import YOLO

    kw = {"classes": classes} if classes is not None else {}
    return YOLO(weights).val(data=data, imgsz=640, device=device or None, split="val", plots=False, verbose=False,
                             project=str(project), name=name, exist_ok=True, **kw)


def _row(r, ids: dict[str, int], present: set[str]) -> dict[str, Any]:
    maps = list(r.box.maps)
    return {
        "precision": round(float(r.box.mp), 4),
        "recall": round(float(r.box.mr), 4),
        "mAP50": round(float(r.box.map50), 4),
        "mAP50-95": round(float(r.box.map), 4),
        # для вида, которого нет на проверочных кадрах, оценки нет (ultralytics подставил бы общее среднее)
        "per_class": {sp: (round(float(maps[i]), 4) if sp in present and i < len(maps) else None)
                      for sp, i in ids.items()},
    }


def _base_dataset(ds_dir: Path, names: dict[int, str], tmp: Path) -> Path:
    """Проверочные кадры с номерами классов стандартной модели (COCO: person=0, cat=15, dog=16)."""
    coco = {v: k for k, v in names.items()}
    remap = {i: coco[sp] for i, sp in enumerate(SPECIES) if sp in coco}
    # кадры копируем (жёсткая ссылка, если можно): ultralytics ищет разметку рядом с настоящим путём кадра,
    # и через символическую ссылку взял бы исходную разметку с нашими номерами классов
    (tmp / "images" / "val").mkdir(parents=True)
    for img in (ds_dir / "images" / "val").glob("*.jpg"):
        dst = tmp / "images" / "val" / img.name
        try:
            os.link(img, dst)
        except OSError:
            shutil.copyfile(img, dst)
    (tmp / "labels" / "val").mkdir(parents=True)
    for f in (ds_dir / "labels" / "val").glob("*.txt"):
        lines = []
        for line in f.read_text().splitlines():
            parts = line.split()
            if parts and int(parts[0]) in remap:
                lines.append(" ".join([str(remap[int(parts[0])]), *parts[1:]]))
        (tmp / "labels" / "val" / f.name).write_text("\n".join(lines))
    data = tmp / "data.yaml"
    data.write_text(yaml.safe_dump({"path": str(tmp), "train": "images/val", "val": "images/val",
                                    "names": dict(names)}, allow_unicode=True))
    return data


def verdict(candidate: dict, reference: dict) -> str:
    d = candidate["mAP50-95"] - reference["mAP50-95"]
    if d >= SAME:
        return "better"
    if d <= -SAME:
        return "worse"
    return "same"


def compare_detectors(engine: Engine, settings: Settings, ds_dir: Path, candidate_id: int,
                      device: str | None, progress=None) -> dict[str, Any]:
    """Сравнить модель candidate_id с активной (если есть дообученная) и стандартной. Результат —
    в MlModel.comparison кандидата."""
    data = ds_dir / "data.yaml"
    n_val = len(list((ds_dir / "images" / "val").glob("*.jpg")))
    counts = {sp: 0 for sp in SPECIES}
    for f in (ds_dir / "labels" / "val").glob("*.txt"):
        for line in f.read_text().splitlines():
            if line.strip():
                counts[SPECIES[int(line.split()[0])]] += 1
    present = {sp for sp, n in counts.items() if n}
    with Session(engine) as s:
        cand = s.get(MlModel, candidate_id)
        active = s.exec(select(MlModel).where(MlModel.kind == "detector", MlModel.active == True)).first()  # noqa: E712
        cand_name, cand_path = cand.name, cand.path
        active_info = (active.id, active.name, active.path) if active and active.id != candidate_id else None
        cand_is_active = bool(active and active.id == candidate_id)

    own_ids = {sp: i for i, sp in enumerate(SPECIES)}
    project = settings.training_dir / f"compare_{candidate_id}"
    rows: list[dict[str, Any]] = []
    try:
        if progress:
            progress(f"Сравнение: {cand_name}")
        r = _val(str(settings.data_dir / cand_path), str(data), project, "candidate", device)
        rows.append({"key": "candidate", "label": cand_name, "model_id": candidate_id, "active": cand_is_active,
                     **_row(r, own_ids, present)})
        if active_info:
            if progress:
                progress(f"Сравнение: {active_info[1]} (активная)")
            r = _val(str(settings.data_dir / active_info[2]), str(data), project, "active", device)
            rows.append({"key": "active", "label": active_info[1], "model_id": active_info[0], "active": True,
                         **_row(r, own_ids, present)})
        if progress:
            progress("Сравнение: стандартная модель")
        from ultralytics import YOLO

        base_w = settings.models_dir / "base" / settings.base_detector
        names = dict(YOLO(str(base_w)).names)
        ids = {sp: i for i, sp in names.items() if sp in SPECIES}  # номера классов в стандартной модели
        with tempfile.TemporaryDirectory() as tmp:
            bdata = _base_dataset(ds_dir, names, Path(tmp))
            r = _val(str(base_w), str(bdata), project, "base", device, classes=sorted(ids.values()))
        rows.append({"key": "base", "label": "Стандартная", "model_id": None,
                     "active": not cand_is_active and active_info is None, **_row(r, ids, present)})
    finally:
        shutil.rmtree(project, ignore_errors=True)

    # с чем сравниваем: с той моделью, что работает сейчас (дообученная активная или стандартная)
    reference = next((x for x in rows if x["key"] == "active"), None) or next(x for x in rows if x["key"] == "base")
    candidate = rows[0]
    result = {
        "date": utcnow().isoformat(),
        "val_images": n_val,
        "val_objects": counts,
        "rows": rows,
        "reference": reference["label"],
        "verdict": "current" if cand_is_active else verdict(candidate, reference),
    }
    with Session(engine) as s:
        m = s.get(MlModel, candidate_id)
        m.comparison = result
        s.add(m)
        s.commit()
    return result
