"""
Scheduler de la tarea de ingesta, tolerante a fallos de proceso/máquina.

Por qué no es un cron simple:
  Un cron "cada 5 horas" asume que la máquina vive encendida 24/7. En la
  etapa de pruebas (confirmado con el usuario) el backend se levanta y
  apaga manualmente, así que un cron clásico generaría huecos sin
  ingesta o, peor, ráfagas de ejecuciones perdidas silenciosamente.

Estrategia:
  1. Al arrancar el proceso (startup de FastAPI), se lee state.json con
     la marca de tiempo de la última corrida exitosa.
  2. Si nunca corrió, o si ya pasaron >= INGESTION_INTERVAL_HOURS desde
     la última corrida, se ejecuta la ingesta INMEDIATAMENTE.
  3. Si no, se programa el próximo run para el tiempo exacto que falta
     (no se espera ciegamente 5 horas desde el arranque del proceso).
  4. Cada corrida exitosa actualiza state.json, así que si el proceso se
     reinicia, retoma el cálculo correctamente en vez de perder el rastro.

En el servidor 24/7 (producción) este mismo código funciona igual de bien
— simplemente el caso "ya pasó el intervalo" rara vez se dispara porque el
proceso no se reinicia.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from app.config import INGESTION_INTERVAL_HOURS, STATE_PATH
from app.pipeline.downloader import descargar_licitaciones
from app.pipeline.extractor import extraer_pendientes, limpiar_staging
from app.pipeline.loader import cargar_a_duckdb

logger = logging.getLogger("scheduler")

_scheduler: AsyncIOScheduler | None = None


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
    return datetime.fromisoformat(valor)


def _marcar_corrida_exitosa(detalle: dict) -> None:
    estado = _leer_estado()
    estado["ultima_corrida_exitosa"] = datetime.now().isoformat()
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
        _reprogramar_siguiente()
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
    _reprogramar_siguiente()


def _segundos_hasta_proxima_corrida() -> float:
    ultima = _ultima_corrida_exitosa()
    if ultima is None:
        return 0.0  # nunca corrió -> ejecutar ya

    proxima = ultima + timedelta(hours=INGESTION_INTERVAL_HOURS)
    delta = (proxima - datetime.now()).total_seconds()
    return max(delta, 0.0)


def iniciar_scheduler() -> AsyncIOScheduler:
    """
    Llamado desde el lifespan de FastAPI al arrancar la app.
    Programa la primera corrida según el tiempo transcurrido desde la
    última ejecución exitosa, y deja una corrida recurrente cada
    INGESTION_INTERVAL_HOURS a partir de ahí.
    """
    global _scheduler
    _scheduler = AsyncIOScheduler()

    espera_seg = _segundos_hasta_proxima_corrida()
    proxima_ejecucion = datetime.now() + timedelta(seconds=espera_seg)

    logger.info(
        "Próxima corrida de ingesta programada para %s (en %.1f minutos)",
        proxima_ejecucion.isoformat(timespec="seconds"),
        espera_seg / 60,
    )

    _scheduler.add_job(
        ejecutar_pipeline_completo,
        trigger=DateTrigger(run_date=proxima_ejecucion),
        id="ingesta_inicial",
    )
    _scheduler.start()
    return _scheduler


def _reprogramar_siguiente() -> None:
    """
    Tras cada corrida (exitosa o no), programa la siguiente exactamente
    INGESTION_INTERVAL_HOURS después — en vez de un interval trigger fijo
    desde el arranque del proceso, que se desincronizaría si el proceso
    se reinicia entre medio.
    """
    if _scheduler is None:
        return
    proxima = datetime.now() + timedelta(hours=INGESTION_INTERVAL_HOURS)
    _scheduler.add_job(
        ejecutar_pipeline_completo,
        trigger=DateTrigger(run_date=proxima),
        id=f"ingesta_{proxima.timestamp()}",
    )


def detener_scheduler() -> None:
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
