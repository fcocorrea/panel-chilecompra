"""
Cliente HTTP para la API pública de Mercado Público (licitaciones).

Documentación: https://www.chilecompra.cl/api/
Base: https://api.mercadopublico.cl/servicios/v1/publico/licitaciones.json

Dos formas de consulta que usa este módulo:
  - Listado liviano por estado ("activas"): trae CodigoExterno + CodigoEstado,
    sin detalle. Sirve para saber QUÉ licitaciones están en proceso ahora.
  - Detalle por código: trae el registro completo (comprador, fechas, montos,
    ítems). Cuesta 1 request por licitación, así que solo se pide para
    códigos nuevos o con cambio de estado (ver pipeline/api_loader.py).

Manejo de errores: la documentación no especifica códigos 4xx/5xx exactos,
así que se trata de forma conservadora — 401/403 son error de credenciales
(no tiene sentido reintentar), 429/5xx son transitorios (reintento con
backoff), y cualquier otro 4xx se trata como error de la consulta puntual.
"""
from __future__ import annotations

import logging
import time

import requests

from app.config import (
    MP_API_BASE_URL,
    MP_API_ESPERA_ENTRE_REINTENTOS_SEG,
    MP_API_MAX_REINTENTOS,
    MP_API_TICKET,
    MP_API_TIMEOUT_SECONDS,
)

logger = logging.getLogger("pipeline.api_client")


class MercadoPublicoAuthError(Exception):
    """Ticket ausente o rechazado por la API (401/403). No tiene sentido reintentar."""


class MercadoPublicoAPIError(Exception):
    """Error transitorio o de la consulta puntual, tras agotar los reintentos."""


def _ticket_o_falla() -> str:
    if not MP_API_TICKET:
        raise MercadoPublicoAuthError(
            "TICKET_API/API_KEY no está configurado (backend/.env). "
            "Ver backend/.env.example."
        )
    return MP_API_TICKET


def _consultar(params: dict) -> dict:
    """
    GET con ticket + reintentos con backoff para errores transitorios.
    Devuelve el JSON parseado tal cual lo entrega la API.
    """
    ticket = _ticket_o_falla()
    query = {**params, "ticket": ticket}

    ultimo_error: Exception | None = None
    for intento in range(1, MP_API_MAX_REINTENTOS + 1):
        try:
            resp = requests.get(MP_API_BASE_URL, params=query, timeout=MP_API_TIMEOUT_SECONDS)
        except requests.exceptions.RequestException as e:
            ultimo_error = e
            logger.warning("Error de conexión (intento %d/%d): %s", intento, MP_API_MAX_REINTENTOS, e)
            time.sleep(MP_API_ESPERA_ENTRE_REINTENTOS_SEG * intento)
            continue

        if resp.status_code in (401, 403):
            raise MercadoPublicoAuthError(f"HTTP {resp.status_code}: ticket rechazado por la API")

        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as e:
                ultimo_error = e
                logger.warning("Respuesta 200 con JSON inválido (intento %d): %s", intento, e)
                time.sleep(MP_API_ESPERA_ENTRE_REINTENTOS_SEG * intento)
                continue

        if resp.status_code == 429 or resp.status_code >= 500:
            ultimo_error = MercadoPublicoAPIError(f"HTTP {resp.status_code}")
            logger.warning(
                "Error transitorio HTTP %d (intento %d/%d), reintentando...",
                resp.status_code, intento, MP_API_MAX_REINTENTOS,
            )
            time.sleep(MP_API_ESPERA_ENTRE_REINTENTOS_SEG * intento)
            continue

        # Otro 4xx: no es transitorio, no vale la pena reintentar.
        raise MercadoPublicoAPIError(f"HTTP {resp.status_code}: {resp.text[:300]}")

    raise MercadoPublicoAPIError(
        f"Agotados {MP_API_MAX_REINTENTOS} reintentos: {ultimo_error}"
    )


def listar_activas() -> dict[str, int]:
    """
    Devuelve {CodigoExterno: CodigoEstado} de todas las licitaciones
    actualmente activas a nivel nacional (listado liviano, sin detalle).
    """
    data = _consultar({"estado": "activas"})
    return {
        item["CodigoExterno"]: item["CodigoEstado"]
        for item in data.get("Listado", [])
        if item.get("CodigoExterno")
    }


def obtener_detalle(codigo: str) -> dict | None:
    """
    Detalle completo de una licitación por su código externo.
    Devuelve None si la API no encuentra el código (listado vacío).
    """
    data = _consultar({"codigo": codigo})
    listado = data.get("Listado", [])
    return listado[0] if listado else None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    activas = listar_activas()
    print(f"Licitaciones activas a nivel nacional: {len(activas)}")
    if activas:
        codigo_muestra = next(iter(activas))
        detalle = obtener_detalle(codigo_muestra)
        print(f"Detalle de muestra ({codigo_muestra}): {detalle.get('Estado') if detalle else 'no encontrado'}")
