from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from switchsign import __version__
from switchsign.api.admin import router as admin_router
from switchsign.api.public import router as public_router
from switchsign.config import settings
from switchsign.db import init_db
from switchsign.jobs.scheduler import shutdown_scheduler, start_scheduler
from switchsign.main_support import PACKAGE_DIR

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.switchsign_db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db()
    start_scheduler()
    try:
        yield
    finally:
        shutdown_scheduler()


app = FastAPI(title="SwitchSign", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")
app.include_router(admin_router)
app.include_router(public_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
