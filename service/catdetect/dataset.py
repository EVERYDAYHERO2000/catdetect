"""Экспорт размеченных кадров в форматы ultralytics (detect и classify)."""

from __future__ import annotations

import shutil
from collections import defaultdict
from pathlib import Path

import yaml
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from .annotate import read_image, write_jpeg
from .labels import ASSIGNED
from .models import SPECIES, Annotation, Identity, Image
from .vision.detector import crop

VAL_EVERY = 5  # каждый 5-й кадр — в валидацию


class DatasetError(ValueError):
    pass


def _is_val(image_id: int) -> bool:
    return image_id % VAL_EVERY == 0


def export_detect(engine: Engine, data_dir: Path, out: Path, min_images: int = 10) -> Path:
    """images/{train,val} + labels/{train,val} + data.yaml. Кадры без боксов — негативные примеры."""
    with Session(engine) as s:
        images = s.exec(select(Image).where(Image.status == "labeled").order_by(Image.id)).all()
        anns: dict[int, list[Annotation]] = defaultdict(list)
        for a in s.exec(select(Annotation)).all():
            anns[a.image_id].append(a)
    positives = sum(1 for i in images if anns.get(i.id))
    if positives < min_images:
        raise DatasetError(f"Нужно минимум {min_images} размеченных кадров с животными (сейчас {positives})")
    if out.exists():
        shutil.rmtree(out)
    cls_index = {sp: i for i, sp in enumerate(SPECIES)}
    has_val = False
    for img in images:
        src = data_dir / img.path
        if not src.exists():
            continue
        split = "val" if _is_val(img.id) else "train"
        has_val |= split == "val"
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, out / "images" / split / f"{img.id}.jpg")
        lines = []
        for a in anns.get(img.id, []):
            if a.species not in cls_index or a.state != ASSIGNED:  # «Не объект» — просто фон
                continue
            cx, cy = (a.x1 + a.x2) / 2, (a.y1 + a.y2) / 2
            lines.append(f"{cls_index[a.species]} {cx:.6f} {cy:.6f} {a.x2 - a.x1:.6f} {a.y2 - a.y1:.6f}")
        (out / "labels" / split / f"{img.id}.txt").write_text("\n".join(lines))
    if not has_val:  # совсем маленький датасет — валидируемся на train
        shutil.copytree(out / "images" / "train", out / "images" / "val")
        shutil.copytree(out / "labels" / "train", out / "labels" / "val")
    (out / "data.yaml").write_text(yaml.safe_dump({
        "path": str(out), "train": "images/train", "val": "images/val",
        "names": {i: sp for sp, i in cls_index.items()},
    }, allow_unicode=True))
    return out / "data.yaml"


def export_classify(engine: Engine, data_dir: Path, out: Path, min_per_class: int = 5) -> tuple[Path, dict[str, int]]:
    """Кропы размеченных животных: {train,val}/id_<identity>/*.jpg. Возвращает каталог и карту класс→identity_id."""
    with Session(engine) as s:
        idents = {i.id: i for i in s.exec(select(Identity)).all()}
        rows = s.exec(select(Annotation, Image).join(Image, Image.id == Annotation.image_id)
                      .where(col(Annotation.identity_id).is_not(None), Annotation.state == ASSIGNED)
                      .order_by(Annotation.id)).all()
    by_cls: dict[int, list[tuple[Annotation, Image]]] = defaultdict(list)
    for a, img in rows:
        if a.identity_id in idents:
            by_cls[a.identity_id].append((a, img))
    good = {k: v for k, v in by_cls.items() if len(v) >= min_per_class}
    if len(good) < 2:
        raise DatasetError(
            f"Нужно минимум 2 объекта, у каждого ≥{min_per_class} размеченных примеров "
            f"(сейчас подходят: {len(good)}). Добавьте, например, объект «чужие кошки»."
        )
    if out.exists():
        shutil.rmtree(out)
    cache: dict[int, object] = {}
    class_map: dict[str, int] = {}
    for iid, items in good.items():
        name = f"id_{iid}"
        class_map[name] = iid
        for n, (a, img) in enumerate(items):
            if img.id not in cache:
                cache.clear()  # держим в памяти только текущий кадр
                cache[img.id] = read_image(data_dir / img.path)
            frame = cache[img.id]
            if frame is None:
                continue
            c = crop(frame, (a.x1, a.y1, a.x2, a.y2), pad=0.1)
            split = "val" if n % VAL_EVERY == 0 else "train"
            write_jpeg(out / split / name / f"{a.id}.jpg", c)
            if split == "val" and len(items) < 2 * VAL_EVERY:
                write_jpeg(out / "train" / name / f"{a.id}.jpg", c)  # мало данных — не отнимаем у train
    return out, class_map
