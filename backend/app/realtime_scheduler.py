"""
Scheduler de la ingesta incremental vía API pública de Mercado Público.

Mismo patrón tolerante a fallos que app/scheduler.py (DateTrigger relativo +
estado en disco), pero con su propio archivo de estado (realtime_state.json)
e intervalo (REALTIME_INTERVAL_HOURS) porque es un job independiente: la
ingesta masiva trae el histórico completo cada 5h, esta trae solo lo
nuevo/cambiado desde la API cada 1h (ver pipeline/api_loader.py para el
detalle de por qué eso no revienta el cupo diario de la API).

Si no hay credenciales configuradas (backend/.env), el scheduler no se
registra — no tiene sentido programar un job que va a fallar cada hora.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from app.config import MP_API_TICKET, REALTIME_INTERVAL_HOURS, REALTIME_STATE_PATH

logger = logging.getLogger("realtime_scheduler")

_scheduler: AsyncIOScheduler | None = None


def _leer_estado() -> dict:
    if not REALTIME_STATE_PATH.exists():
        return {}
    try:
        import json
        return json.loads(REALTIME_STATE_PATH.read_text())
    except Exception:  # noqa: BLE001 — estado corrupto se trata como vacío
        return {}


def _ultima_corrida_exitosa() -> datetime | None:
    valor = _leer_estado().get("ultima_corrida_exitosa")
    return datetime.fromisoformat(valor) if valor else None


def _segundos_hasta_proxima_corrida() -> float:
    ultima = _ultima_corrida_exitosa()
    if ultima is None:
        return 0.0
    proxima = ultima + timedelta(hours=REALTIME_INTERVAL_HOURS)
    return max((proxima - datetime.now()).total_seconds(), 0.0)


def _ejecutar_y_reprogramar() -> None:
    from app.pipeline.api_loader import ejecutar_ingesta_incremental

    try:
        ejecutar_ingesta_incremental()
    except Exception as e:  # noqa: BLE001 — se loguea y se reintenta en la próxima corrida
        logger.error("Ingesta API falló: %s", e, exc_info=True)
    _reprogramar_siguiente()


def _reprogramar_siguiente() -> None:
    if _scheduler is None:
        return
    proxima = datetime.now() + timedelta(hours=REALTIME_INTERVAL_HOURS)
    _scheduler.add_job(
        _ejecutar_y_reprogramar,
        trigger=DateTrigger(run_date=proxima),
        id=f"ingesta_api_{proxima.timestamp()}",
    )


def iniciar_realtime_scheduler() -> AsyncIOScheduler | None:
    global _scheduler

    if not MP_API_TICKET:
        logger.warning(
            "TICKET_API/API_KEY no configurado (backend/.env) — ingesta en tiempo real desactivada."
        )
        return None

    _scheduler = AsyncIOScheduler()
    espera_seg = _segundos_hasta_proxima_corrida()
    proxima_ejecucion = datetime.now() + timedelta(seconds=espera_seg)

    logger.info(
        "Próxima ingesta API programada para %s (en %.1f minutos)",
        proxima_ejecucion.isoformat(timespec="seconds"), espera_seg / 60,
    )

    _scheduler.add_job(
        _ejecutar_y_reprogramar,
        trigger=DateTrigger(run_date=proxima_ejecucion),
        id="ingesta_api_inicial",
    )
    _scheduler.start()
    return _scheduler


def detener_realtime_scheduler() -> None:
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
