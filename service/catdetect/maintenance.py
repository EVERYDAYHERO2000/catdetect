"""Разовая чистка разметки: одно и то же имя дважды на одном кадре.

- рамки сильно перекрываются — это одно животное, обведённое дважды: лишняя (менее уверенная) удаляется;
- рамки в разных местах — два разных объекта под одним именем: менее уверенная возвращается в «Неразобранные».

Запуск (в контейнере): python -m catdetect.maintenance dedupe [--apply]
Без --apply только показывает, что будет сделано.
"""

from __future__ import annotations

import sys
from collections import defaultdict

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from .labels import ASSIGNED, PENDING, refresh_image_status
from .models import Annotation
from .vision.geometry import iou

OVERLAP = 0.3


def dedupe_assigned(engine: Engine, apply: bool = False) -> dict[str, int]:
    removed = returned = frames = 0
    with Session(engine) as db:
        groups: dict[tuple[int, int], list[Annotation]] = defaultdict(list)
        for a in db.exec(select(Annotation).where(Annotation.state == ASSIGNED)).all():
            groups[(a.image_id, a.identity_id)].append(a)
        touched = []
        for anns in groups.values():
            if len(anns) < 2:
                continue
            frames += 1
            anns.sort(key=lambda a: -(a.conf or 0))  # остаётся самая уверенная рамка
            keep = anns[0]
            for a in anns[1:]:
                if iou((a.x1, a.y1, a.x2, a.y2), (keep.x1, keep.y1, keep.x2, keep.y2)) >= OVERLAP:
                    removed += 1
                    if apply:
                        db.delete(a)
                else:
                    returned += 1
                    if apply:
                        a.state, a.identity_id, a.assigned_at = PENDING, None, None
                        db.add(a)
                touched.append(a.image_id)
        if apply:
            db.flush()
            refresh_image_status(db, touched)
            db.commit()
    return {"frames": frames, "removed_duplicate_boxes": removed, "returned_to_pending": returned, "applied": apply}


if __name__ == "__main__":
    from .db import make_engine
    from .settings import load_settings

    if len(sys.argv) < 2 or sys.argv[1] != "dedupe":
        print(__doc__)
        sys.exit(1)
    s = load_settings()
    s.prepare()
    print(dedupe_assigned(make_engine(s.db_path), apply="--apply" in sys.argv))
