"""
Ingesta incremental desde la API pública de Mercado Público.

A diferencia de loader.py (bulk, CREATE OR REPLACE = snapshot completo),
este módulo hace upsert por NroLicitacion: preserva todo el histórico ya
cargado por la vía masiva (usado para entrenar el modelo) y solo agrega o
actualiza las licitaciones que están o estuvieron activas.

Estrategia para mantenerse muy por debajo del cupo documentado de 10.000
requests/día con un scheduler que corre cada hora (ver config.REALTIME_INTERVAL_HOURS):
  1. Listado liviano de TODAS las activas a nivel nacional (1 request).
  2. Solo se pide el detalle (1 request c/u) de códigos NUEVOS o cuyo
     CodigoEstado cambió desde la corrida anterior — no de las ~4000
     activas completas en cada corrida.
  3. Además se revisan las licitaciones que ANTES estaban "Publicada" en
     nuestra tabla y ya no aparecen en el listado de activas (se cerraron,
     adjudicaron, etc.) para actualizar su estado final — si no, quedarían
     marcadas como "en proceso" para siempre en el panel.
  4. Filtro de sector: solo se guardan las municipales (mismo alcance que
     la descarga histórica), aunque el chequeo de cambio de estado
     considera todas las nacionales (es barato, es solo el listado liviano).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from app.config import (
    ESTADO_LICITACION_EN_PROCESO,
    ID_COLUMN_DEDUP,
    MP_API_ESPERA_ENTRE_LLAMADAS_SEG,
    MP_API_MAX_DETALLES_POR_CORRIDA,
    REALTIME_STATE_PATH,
    TABLE_LICITACIONES_CLEAN,
)
from app.db import get_connection
from app.pipeline.api_client import (
    MercadoPublicoAPIError,
    MercadoPublicoAuthError,
    listar_activas,
    obtener_detalle,
)
from app.pipeline.api_mapping import aplanar_detalle, es_municipal

logger = logging.getLogger("pipeline.api_loader")


@dataclass
class ResumenIngestaAPI:
    estado: str  # "ok" | "sin_credenciales" | "error"
    n_activas_nacional: int = 0
    n_consultadas: int = 0
    n_municipales_upsertadas: int = 0
    n_errores_detalle: int = 0
    detalle: str = ""
    codigos_estado_procesados: dict[str, int] = field(default_factory=dict)


def _leer_estado_rt() -> dict:
    if not REALTIME_STATE_PATH.exists():
        return {}
    try:
        return json.loads(REALTIME_STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("realtime_state.json corrupto o ilegible; se trata como vacío.")
        return {}


def _escribir_estado_rt(estado: dict) -> None:
    REALTIME_STATE_PATH.write_text(json.dumps(estado, indent=2, default=str))


def _tabla_existe(con, nombre_tabla: str) -> bool:
    resultado = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
        [nombre_tabla],
    ).fetchone()
    return resultado[0] > 0


def _codigos_activos_en_db(con) -> set[str]:
    """NroLicitacion que hoy figuran como 'en proceso' en nuestra propia tabla."""
    if not _tabla_existe(con, TABLE_LICITACIONES_CLEAN):
        return set()
    filas = con.execute(
        f"SELECT DISTINCT {ID_COLUMN_DEDUP} FROM {TABLE_LICITACIONES_CLEAN} "
        f"WHERE EstadoLicitacion = ?",
        [ESTADO_LICITACION_EN_PROCESO],
    ).fetchall()
    return {r[0] for r in filas}


def _columnas_tabla(con, nombre_tabla: str) -> set[str]:
    filas = con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
        [nombre_tabla],
    ).fetchall()
    return {r[0] for r in filas}


def _upsert_duckdb(con, df_nuevas: pd.DataFrame) -> None:
    """
    DELETE + INSERT BY NAME por NroLicitacion — evita duplicados sin requerir
    una constraint UNIQUE (la tabla se crea dinámicamente en loader.py).

    licitaciones_clean no tiene un esquema fijo (loader.py ya lo documenta:
    varía según qué columnas sobrevivan al dropeo de nulos en cada corrida
    masiva), así que antes de insertar se recorta el DataFrame de la API a
    solo las columnas que la tabla YA tiene — evita romper el INSERT si el
    mapeo de la API trae algún campo que esta corrida de la tabla no tiene.
    """
    if _tabla_existe(con, TABLE_LICITACIONES_CLEAN):
        columnas_destino = _columnas_tabla(con, TABLE_LICITACIONES_CLEAN)
        columnas_validas = [c for c in df_nuevas.columns if c in columnas_destino]
        df_nuevas = df_nuevas[columnas_validas]
        con.register("api_rows_view", df_nuevas)
        try:
            con.execute(
                f"DELETE FROM {TABLE_LICITACIONES_CLEAN} "
                f"WHERE {ID_COLUMN_DEDUP} IN (SELECT {ID_COLUMN_DEDUP} FROM api_rows_view)"
            )
            con.execute(f"INSERT INTO {TABLE_LICITACIONES_CLEAN} BY NAME SELECT * FROM api_rows_view")
        finally:
            con.unregister("api_rows_view")
    else:
        con.register("api_rows_view", df_nuevas)
        try:
            con.execute(f"CREATE TABLE {TABLE_LICITACIONES_CLEAN} AS SELECT * FROM api_rows_view")
        finally:
            con.unregister("api_rows_view")


def _registrar_log_api(resumen: ResumenIngestaAPI) -> None:
    con = get_connection()
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS api_ingestion_log (
            id BIGINT,
            ejecutado_en TIMESTAMP NOT NULL,
            estado VARCHAR NOT NULL,
            n_activas_nacional BIGINT,
            n_consultadas BIGINT,
            n_municipales_upsertadas BIGINT,
            n_errores_detalle BIGINT,
            detalle VARCHAR
        )
        """
    )
    siguiente_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM api_ingestion_log").fetchone()[0]
    con.execute(
        "INSERT INTO api_ingestion_log VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            siguiente_id, datetime.now(), resumen.estado,
            resumen.n_activas_nacional, resumen.n_consultadas,
            resumen.n_municipales_upsertadas, resumen.n_errores_detalle,
            resumen.detalle,
        ],
    )


def ejecutar_ingesta_incremental() -> ResumenIngestaAPI:
    con = get_connection()

    try:
        activas_light = listar_activas()
    except MercadoPublicoAuthError as e:
        resumen = ResumenIngestaAPI(estado="sin_credenciales", detalle=str(e))
        _registrar_log_api(resumen)
        return resumen
    except MercadoPublicoAPIError as e:
        resumen = ResumenIngestaAPI(estado="error", detalle=str(e))
        _registrar_log_api(resumen)
        return resumen

    estado_previo = _leer_estado_rt().get("codigos_estado_conocidos", {})
    nuevos_o_cambiados = {
        c for c, e in activas_light.items() if estado_previo.get(c) != e
    }
    codigos_a_revisar_por_cierre = _codigos_activos_en_db(con) - activas_light.keys()

    codigos_a_consultar = list(nuevos_o_cambiados | codigos_a_revisar_por_cierre)
    if len(codigos_a_consultar) > MP_API_MAX_DETALLES_POR_CORRIDA:
        logger.warning(
            "Recorte de cupo: %d códigos pendientes, procesando %d esta corrida; el resto sigue en la próxima.",
            len(codigos_a_consultar), MP_API_MAX_DETALLES_POR_CORRIDA,
        )
        codigos_a_consultar = codigos_a_consultar[:MP_API_MAX_DETALLES_POR_CORRIDA]

    filas_municipales = []
    codigos_procesados_ok: dict[str, int] = {}
    n_errores = 0

    for codigo in codigos_a_consultar:
        try:
            detalle = obtener_detalle(codigo)
        except (MercadoPublicoAPIError, MercadoPublicoAuthError) as e:
            logger.warning("Detalle de %s falló, se reintenta en la próxima corrida: %s", codigo, e)
            n_errores += 1
            continue
        finally:
            time.sleep(MP_API_ESPERA_ENTRE_LLAMADAS_SEG)

        if detalle is None:
            continue  # código ya no existe en la API; simplemente no se actualiza

        if es_municipal(detalle):
            filas_municipales.append(aplanar_detalle(detalle))

        # Se marca como conocido aunque no sea municipal, así no se re-consulta
        # en cada corrida mientras su estado no cambie.
        codigos_procesados_ok[codigo] = activas_light.get(codigo, -1)

    if filas_municipales:
        df_nuevas = pd.DataFrame(filas_municipales)
        _upsert_duckdb(con, df_nuevas)

    estado_previo.update(codigos_procesados_ok)
    _escribir_estado_rt({
        "ultima_corrida_exitosa": datetime.now().isoformat(),
        "codigos_estado_conocidos": estado_previo,
    })

    resumen = ResumenIngestaAPI(
        estado="ok",
        n_activas_nacional=len(activas_light),
        n_consultadas=len(codigos_a_consultar),
        n_municipales_upsertadas=len(filas_municipales),
        n_errores_detalle=n_errores,
        codigos_estado_procesados=codigos_procesados_ok,
    )
    _registrar_log_api(resumen)
    logger.info(
        "Ingesta API: %d activas nacional, %d consultadas, %d municipales upsertadas, %d errores",
        resumen.n_activas_nacional, resumen.n_consultadas,
        resumen.n_municipales_upsertadas, resumen.n_errores_detalle,
    )
    return resumen


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(ejecutar_ingesta_incremental())
