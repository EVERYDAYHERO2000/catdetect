from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import auth, cameras, events, identities, images, nvrs, objects, system, training
from .auth import SessionSigner
from .db import make_engine
from .events import EventBus
from .labels import normalize_legacy
from .runtime import Runtime
from .settings import Settings, load_settings
from .training import TrainingManager

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.prepare()
    engine = make_engine(settings.db_path)
    normalize_legacy(engine)
    bus = EventBus()
    runtime = Runtime(settings, engine, bus)
    trainer = TrainingManager(settings, engine, lambda: runtime.resolve_compute().device)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        bus.bind_loop(asyncio.get_running_loop())
        trainer.recover()
        runtime.start()
        log.info("CatDetect %s запущен, данные: %s", __version__, settings.data_dir)
        yield
        runtime.stop()

    app = FastAPI(title="CatDetect", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.bus = bus
    app.state.runtime = runtime
    app.state.training = trainer
    app.state.signer = SessionSigner(settings.secret_key)

    for r in (auth, nvrs, cameras, identities, images, events, training, system, objects):
        app.include_router(r.router)

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__, "detector": runtime.detector_status()}

    web = settings.web_dir
    if (web / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            f = (web / path).resolve()
            if path and f.is_file() and web.resolve() in f.parents:
                return FileResponse(f)
            # index.html не кешируем — иначе после обновления сервиса браузер держит старый интерфейс
            return FileResponse(web / "index.html", headers={"Cache-Control": "no-cache"})
    else:
        log.warning("Веб-интерфейс не собран (%s) — доступен только API", web)

    return app
