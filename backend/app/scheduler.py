"""
Scheduler de la tarea de ingesta, tolerante a fallos de proceso/máquina.

Mismo patrón que app/scoring/scheduler.py: CronTrigger a hora fija (baja
carga, evita tráfico) + ventana de tolerancia para ponerse al día si el
backend estuvo apagado durante la corrida programada.

Estrategia:
  1. Al arrancar el proceso (startup de FastAPI), se lee state.json con
     la marca de tiempo de la última corrida exitosa.
  2. Si nunca corrió, o si ya pasaron >= INGESTION_MIN_HOURS_BETWEEN_RUNS
     desde la última corrida, se ejecuta la ingesta INMEDIATAMENTE.
  3. Se deja además una corrida recurrente vía CronTrigger todos los días
     a INGESTION_HOUR:INGESTION_MINUTE (hora de Chile).
  4. Cada corrida exitosa actualiza state.json.
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
    INGESTION_HOUR,
    INGESTION_MIN_HOURS_BETWEEN_RUNS,
    INGESTION_MINUTE,
    INGESTION_TIMEZONE,
    STATE_PATH,
)
from app.pipeline.downloader import descargar_licitaciones
from app.pipeline.extractor import extraer_pendientes, limpiar_staging
from app.pipeline.loader import cargar_a_duckdb

logger = logging.getLogger("scheduler")

_scheduler: AsyncIOScheduler | None = None
_TZ = ZoneInfo(INGESTION_TIMEZONE)


def _leer_estado() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("state.json corrupto o ilegible; se trata como vacío.")
        return {}


def _escribir_estado(estado: dict) -> None:
    STATE_PATH.write_text(json.dumps(estado, indent=2, default=str))


def _ultima_corrida_exitosa() -> datetime | None:
    estado = _leer_estado()
    valor = estado.get("ultima_corrida_exitosa")
    if not valor:
        return None
    corrida = datetime.fromisoformat(valor)
    if corrida.tzinfo is None:
        # state.json de una corrida previa al cambio a CronTrigger, cuando
        # se guardaba con datetime.now() naive (hora local == hora Chile).
        corrida = corrida.replace(tzinfo=_TZ)
    return corrida


def _marcar_corrida_exitosa(detalle: dict) -> None:
    estado = _leer_estado()
    estado["ultima_corrida_exitosa"] = datetime.now(_TZ).isoformat()
    estado["ultimo_detalle"] = detalle
    _escribir_estado(estado)


def ejecutar_pipeline_completo() -> None:
    """
    Corre las 4 etapas en orden: descarga -> extracción -> limpieza -> carga.
    Si la etapa de descarga no trae nada nuevo Y no hay staging pendiente
    de una corrida anterior interrumpida, no tiene sentido re-cargar a
    DuckDB lo mismo que ya está — pero igual lo dejamos simple por ahora:
    siempre se re-extrae lo pendiente y se recarga, porque extractor.py y
    loader.py ya son idempotentes/baratos si no hay archivos nuevos.
    """
    logger.info("=== Iniciando corrida de pipeline ===")

    resumen_descarga = descargar_licitaciones()
    logger.info(
        "Descarga: %d nuevos, %d omitidos, cupo_agotado=%s",
        sum(1 for r in resumen_descarga.resultados if r.estado == "descargado"),
        sum(1 for r in resumen_descarga.resultados if r.estado == "omitido_existente"),
        resumen_descarga.cupo_agotado,
    )

    resultados_extraccion = extraer_pendientes()
    carpetas_ok = [r.carpeta_destino for r in resultados_extraccion if r.estado in ("extraido", "ya_extraido")]

    if not carpetas_ok:
        logger.warning("No hay carpetas de staging disponibles; se omite la carga.")
        _marcar_corrida_exitosa({"etapa_final": "extraccion_vacia"})
        return

    metricas = cargar_a_duckdb(carpetas_ok)

    # Limpieza de staging ya cargado, para no acumular disco indefinidamente.
    for r in resultados_extraccion:
        if r.estado == "extraido":
            limpiar_staging(r.carpeta_destino)

    detalle = {
        "filas_finales": metricas.filas_finales if metricas else 0,
        "cupo_agotado": resumen_descarga.cupo_agotado,
    }
    _marcar_corrida_exitosa(detalle)
    logger.info("=== Pipeline completo ===")


def _debe_correr_ahora() -> bool:
    """
    Tolerancia a fallos: si pasaron >= INGESTION_MIN_HOURS_BETWEEN_RUNS
    desde la última corrida exitosa, hay que correr ya — sin importar si
    la hora actual es exactamente la programada. Cubre el caso de que el
    servidor estuvo apagado durante la ventana nocturna.
    """
    ultima = _ultima_corrida_exitosa()
    if ultima is None:
        return True
    horas_transcurridas = (datetime.now(_TZ) - ultima).total_seconds() / 3600
    return horas_transcurridas >= INGESTION_MIN_HOURS_BETWEEN_RUNS


def iniciar_scheduler() -> AsyncIOScheduler:
    """
    Llamado desde el lifespan de FastAPI al arrancar la app.

    1. Si ya pasó la ventana de tolerancia desde la última corrida exitosa
       (o nunca corrió), dispara la ingesta de inmediato.
    2. Programa la corrida recurrente diaria a INGESTION_HOUR:INGESTION_MINUTE
       hora de Chile vía CronTrigger.
    """
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone=_TZ)

    if _debe_correr_ahora():
        logger.info("Ingesta atrasada o nunca corrió; se ejecuta de inmediato.")
        _scheduler.add_job(
            ejecutar_pipeline_completo,
            trigger=DateTrigger(run_date=datetime.now(_TZ) + timedelta(seconds=5)),
            id="ingesta_inicial",
        )
    else:
        logger.info("Última ingesta reciente; se respeta el horario fijo diario.")

    _scheduler.add_job(
        ejecutar_pipeline_completo,
        trigger=CronTrigger(hour=INGESTION_HOUR, minute=INGESTION_MINUTE, timezone=_TZ),
        id="ingesta_diaria",
    )

    _scheduler.start()
    logger.info(
        "Ingesta programada diariamente a las %02d:%02d (%s). Próxima corrida: %s",
        INGESTION_HOUR, INGESTION_MINUTE, INGESTION_TIMEZONE,
        _scheduler.get_job("ingesta_diaria").next_run_time,
    )
    return _scheduler


def detener_scheduler() -> None:
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
