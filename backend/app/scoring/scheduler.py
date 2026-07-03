"""
Scheduler del pipeline de scoring (detección de licitaciones dirigidas).

Independiente del scheduler de ingesta (app/scheduler.py): corren en
paralelo, en el mismo proceso de FastAPI, pero con ciclos de vida
completamente distintos.

  - Ingesta: una vez al día a las 03:00 hora de Chile (tráfico mínimo).
  - Scoring (reentrenamiento): una vez a la semana, mismo horario de baja
    carga, tolerante a fallos por ventana semanal — si el proceso estuvo
    apagado durante la corrida programada, corre apenas vuelve a levantar
    en vez de esperar silenciosamente a la próxima semana.

CronTrigger con día de semana fijo (SCORING_DAY_OF_WEEK) y zona horaria
explícita (America/Santiago) resuelve correctamente el cambio de horario
de verano/invierno de Chile sin que tengamos que calcularlo a mano.

Lock de ejecución: el scoring puede tardar minutos (carga de modelo de
embeddings, entrenamiento del Isolation Forest). Si por algún motivo dos
corridas se superponen (reinicio del proceso justo en la ventana), un
lock de archivo simple evita que corran en paralelo y se corrompan los
datos a mitad de escritura en DuckDB.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.config import (
    SCORING_DAY_OF_WEEK,
    SCORING_HOUR,
    SCORING_MIN_HOURS_BETWEEN_RUNS,
    SCORING_MINUTE,
    SCORING_TIMEZONE,
)

logger = logging.getLogger("scoring.scheduler")

_SCORING_STATE_PATH = None  # se resuelve en tiempo de import, ver abajo
_scoring_scheduler: AsyncIOScheduler | None = None
_TZ = ZoneInfo(SCORING_TIMEZONE)


def _state_path():
    global _SCORING_STATE_PATH
    if _SCORING_STATE_PATH is None:
        from app.config import BASE_DIR
        _SCORING_STATE_PATH = BASE_DIR / "scoring_state.json"
    return _SCORING_STATE_PATH


def _lock_path():
    from app.config import SCORING_LOCK_PATH
    return SCORING_LOCK_PATH


def _leer_estado() -> dict:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("scoring_state.json corrupto o ilegible; se trata como vacío.")
        return {}


def _escribir_estado(estado: dict) -> None:
    _state_path().write_text(json.dumps(estado, indent=2, default=str))


def _ultima_corrida_exitosa() -> datetime | None:
    valor = _leer_estado().get("ultima_corrida_exitosa")
    if not valor:
        return None
    return datetime.fromisoformat(valor)


def _marcar_corrida_exitosa(detalle: dict) -> None:
    estado = _leer_estado()
    estado["ultima_corrida_exitosa"] = datetime.now(_TZ).isoformat()
    estado["ultimo_detalle"] = detalle
    _escribir_estado(estado)


def _adquirir_lock() -> bool:
    lock = _lock_path()
    if lock.exists():
        logger.warning("Lock de scoring ya existe (%s); se omite esta corrida.", lock)
        return False
    lock.write_text(datetime.now(_TZ).isoformat())
    return True


def _liberar_lock() -> None:
    _lock_path().unlink(missing_ok=True)


def ejecutar_pipeline_scoring() -> None:
    """
    Corre el pipeline de scoring completo (features -> red -> texto ->
    modelo -> carga a DuckDB). Importado de forma diferida para no cargar
    sklearn/sentence-transformers/networkx en cada arranque del proceso
    si esta función nunca llega a dispararse en una sesión corta de
    pruebas.
    """
    if not _adquirir_lock():
        return

    try:
        logger.info("=== Iniciando corrida de scoring ===")
        from app.scoring.pipeline import ejecutar_scoring_completo

        resultado = ejecutar_scoring_completo()
        _marcar_corrida_exitosa(resultado)
        logger.info("=== Scoring completo ===")
    except Exception as e:  # noqa: BLE001 — se loguea y se reintenta en la próxima ventana
        logger.error("Scoring falló: %s", e, exc_info=True)
        _marcar_corrida_exitosa({"estado": "error", "detalle": str(e)})
    finally:
        _liberar_lock()


def _debe_correr_ahora() -> bool:
    """
    Tolerancia a fallos: si pasaron >= SCORING_MIN_HOURS_BETWEEN_RUNS
    (una semana, con margen) desde la última corrida exitosa, hay que
    correr ya — sin importar si es exactamente el día/hora programados.
    Esto cubre el caso de que el servidor estuvo apagado durante la
    corrida semanal.
    """
    ultima = _ultima_corrida_exitosa()
    if ultima is None:
        return True
    horas_transcurridas = (datetime.now(_TZ) - ultima).total_seconds() / 3600
    return horas_transcurridas >= SCORING_MIN_HOURS_BETWEEN_RUNS


def iniciar_scoring_scheduler() -> AsyncIOScheduler:
    """
    Llamado desde el lifespan de FastAPI junto al scheduler de ingesta.

    1. Si ya pasó la ventana de tolerancia desde la última corrida
       exitosa (o nunca corrió), dispara el scoring de inmediato.
    2. Programa la corrida recurrente semanal (SCORING_DAY_OF_WEEK) a
       las 03:00 hora de Chile vía CronTrigger.
    """
    global _scoring_scheduler
    _scoring_scheduler = AsyncIOScheduler(timezone=_TZ)

    if _debe_correr_ahora():
        logger.info("Scoring atrasado o nunca corrió; se ejecuta de inmediato.")
        _scoring_scheduler.add_job(
            ejecutar_pipeline_scoring,
            trigger=DateTrigger(run_date=datetime.now(_TZ) + timedelta(seconds=5)),
            id="scoring_inicial",
        )
    else:
        logger.info("Último scoring reciente; se respeta el horario fijo semanal.")

    _scoring_scheduler.add_job(
        ejecutar_pipeline_scoring,
        trigger=CronTrigger(
            day_of_week=SCORING_DAY_OF_WEEK, hour=SCORING_HOUR, minute=SCORING_MINUTE, timezone=_TZ,
        ),
        id="scoring_semanal",
    )

    _scoring_scheduler.start()
    logger.info(
        "Scoring programado semanalmente (%s) a las %02d:%02d (%s). Próxima corrida: %s",
        SCORING_DAY_OF_WEEK, SCORING_HOUR, SCORING_MINUTE, SCORING_TIMEZONE,
        _scoring_scheduler.get_job("scoring_semanal").next_run_time,
    )
    return _scoring_scheduler


def detener_scoring_scheduler() -> None:
    if _scoring_scheduler is not None:
        _scoring_scheduler.shutdown(wait=False)
    _liberar_lock()
