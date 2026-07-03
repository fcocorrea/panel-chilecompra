"""
Entry point del backend.

Arranque:
    uvicorn app.main:app --reload --port 8000

El scheduler de ingesta se inicia dentro del lifespan de FastAPI: arranca
junto con el servidor web y se apaga limpiamente al detenerlo (Ctrl+C),
en vez de vivir como un proceso de cron totalmente separado. Esto es
deliberado para la etapa de pruebas: un solo proceso, un solo comando
para levantar todo.

Cuando esto pase al servidor 24/7, este mismo diseño sigue funcionando
sin cambios — simplemente correrá de forma continua en vez de
levantarse/apagarse manualmente.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as licitaciones_router
from app.realtime_scheduler import detener_realtime_scheduler, iniciar_realtime_scheduler
from app.scheduler import detener_scheduler, iniciar_scheduler
from app.scoring.scheduler import detener_scoring_scheduler, iniciar_scoring_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    iniciar_scheduler()
    iniciar_scoring_scheduler()
    iniciar_realtime_scheduler()
    yield
    detener_scheduler()
    detener_scoring_scheduler()
    detener_realtime_scheduler()


app = FastAPI(
    title="ChileCompra Auditoría API",
    description="Backend de ingesta, limpieza y exposición de licitaciones municipales.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS abierto para la etapa de pruebas (panel React corriendo en localhost
# con otro puerto). Restringir a un origin explícito antes de exponerlo
# fuera de la intranet.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(licitaciones_router)


@app.get("/health")
def health():
    return {"status": "ok"}
