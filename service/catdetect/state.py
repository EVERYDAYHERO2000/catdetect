"""Текущее состояние камер и животных (в памяти) — источник данных для HA."""

from __future__ import annotations

import threading
import time
from copy import deepcopy
from typing import Any

from .events import EventBus


class StateStore:
    def __init__(self, bus: EventBus):
        self.bus = bus
        self._lock = threading.Lock()
        self.cameras: dict[int, dict[str, Any]] = {}
        self.identities: dict[int, dict[str, Any]] = {}
        # identity_id -> {camera_id: {"present": bool, "at_door": bool}}
        self._identity_by_cam: dict[int, dict[int, dict[str, bool]]] = {}
        self.snapshots: dict[int, tuple[float, bytes]] = {}

    # --- конфигурация ---

    def configure(self, cameras: list[tuple[int, list[str]]], identity_ids: list[int],
                  last_directions: dict[int, dict[str, Any]] | None = None) -> None:
        """Пересоздать список камер/объектов после изменения настроек; сохраняет известные значения."""
        with self._lock:
            new_cams = {}
            for cam_id, species in cameras:
                old = self.cameras.get(cam_id, {})
                new_cams[cam_id] = {
                    "online": old.get("online", False),
                    "motion": old.get("motion", False),
                    "species": {sp: old.get("species", {}).get(sp, {"present": False, "at_door": False})
                                for sp in species},
                    "last_event": old.get("last_event"),
                }
            self.cameras = new_cams
            new_ids = {}
            for iid in identity_ids:
                old = self.identities.get(iid)
                if old is None:
                    old = {"present": False, "at_door": False, "last_direction": None,
                           "last_camera_id": None, "last_seen": None}
                    if last_directions and iid in last_directions:
                        old.update(last_directions[iid])
                new_ids[iid] = old
            self.identities = new_ids
            self._identity_by_cam = {k: v for k, v in self._identity_by_cam.items() if k in new_ids}

    # --- обновления из пайплайна ---

    def _camera_changed(self, cam_id: int) -> None:
        self.bus.publish({"type": "camera_state", "camera_id": cam_id, "state": deepcopy(self.cameras[cam_id])})

    def set_flag(self, cam_id: int, key: str, value: bool) -> None:
        with self._lock:
            cam = self.cameras.get(cam_id)
            if cam is None or cam[key] == value:
                return
            cam[key] = value
            self._camera_changed(cam_id)

    def set_species(self, cam_id: int, species: str, present: bool, at_door: bool) -> None:
        with self._lock:
            cam = self.cameras.get(cam_id)
            if cam is None or species not in cam["species"]:
                return
            cam["species"][species] = {"present": present, "at_door": at_door}
            self._camera_changed(cam_id)

    def set_identity_presence(self, cam_id: int, identity_id: int, present: bool, at_door: bool) -> None:
        with self._lock:
            ident = self.identities.get(identity_id)
            if ident is None:
                return
            per_cam = self._identity_by_cam.setdefault(identity_id, {})
            per_cam[cam_id] = {"present": present, "at_door": at_door}
            new_present = any(v["present"] for v in per_cam.values())
            new_at_door = any(v["at_door"] for v in per_cam.values())
            if present:
                ident["last_camera_id"] = cam_id
                ident["last_seen"] = time.time()
            if (new_present, new_at_door) != (ident["present"], ident["at_door"]):
                ident["present"], ident["at_door"] = new_present, new_at_door
                self.bus.publish({"type": "identity_state", "identity_id": identity_id, "state": dict(ident)})

    def on_event(self, event: dict[str, Any], snapshot: bytes | None) -> None:
        cam_id = event["camera_id"]
        with self._lock:
            if cam_id in self.cameras:
                self.cameras[cam_id]["last_event"] = event
            iid = event.get("identity_id")
            if iid in self.identities and event["kind"] in ("arrived", "left"):
                ident = self.identities[iid]
                ident["last_direction"] = event["kind"]
                ident["last_camera_id"] = cam_id
                ident["last_seen"] = time.time()
                self.bus.publish({"type": "identity_state", "identity_id": iid, "state": dict(ident)})
            if snapshot is not None:
                self.snapshots[cam_id] = (time.time(), snapshot)
        self.bus.publish({"type": "event", "event": event})
        if snapshot is not None:
            self.bus.publish({"type": "snapshot", "camera_id": cam_id})

    # --- чтение ---

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "cameras": deepcopy(self.cameras),
                "identities": deepcopy(self.identities),
            }

    def last_snapshot(self, cam_id: int) -> tuple[float, bytes] | None:
        with self._lock:
            return self.snapshots.get(cam_id)
