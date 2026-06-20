"""
Carga de datos limpios a DuckDB.

Responsabilidades:
  1. Leer cada CSV extraído en STAGING_DIR (uno por año/semestre) con el
     encoding/separador confirmado (latin-1, ';').
  2. Pasarlos a cleaning.limpiar_licitaciones() para aplicar el ETL.
  3. Escribir el resultado en la tabla TABLE_LICITACIONES_CLEAN de DuckDB,
     reemplazando el contenido anterior (cada corrida es una resnapshot
     completa, no un append — evita duplicar histórico entre corridas).
  4. Dejar un registro en TABLE_INGESTION_LOG con las métricas de la
     corrida (para poder auditar qué pasó en cada ejecución del scheduler).
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.config import (
    CSV_ENCODING,
    CSV_SEPARATOR,
    DUCKDB_PATH,
    TABLE_INGESTION_LOG,
    TABLE_LICITACIONES_CLEAN,
)
from app.db import get_connection
from app.pipeline.cleaning import MetricasLimpieza, limpiar_licitaciones

logger = logging.getLogger("pipeline.loader")


def _leer_csvs_de_staging(carpetas_staging: list[Path]) -> list[pd.DataFrame]:
    """
    Lee todos los .csv encontrados dentro de cada carpeta de staging.
    Cada .7z puede contener uno o más CSV; se leen todos con el
    encoding/separador confirmado para este origen de datos.
    """
    dataframes = []
    for carpeta in carpetas_staging:
        for csv_path in sorted(carpeta.glob("*.csv")):
            try:
                df = pd.read_csv(
                    csv_path, sep=CSV_SEPARATOR, low_memory=False, encoding=CSV_ENCODING
                )
                dataframes.append(df)
                logger.info("Leído %s: %d filas", csv_path.name, len(df))
            except Exception as e:  # noqa: BLE001
                logger.error("No se pudo leer %s: %s", csv_path, e)
    return dataframes


def _registrar_log_ingesta(metricas: MetricasLimpieza, estado: str, detalle: str = "") -> None:
    con = get_connection()
    con.execute(
        f"""
        INSERT INTO {TABLE_INGESTION_LOG} (
            ejecutado_en, estado, filas_iniciales, filas_finales,
            columnas_eliminadas, duplicados_eliminados,
            filas_excluidas_monto_cero, detalle
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            datetime.now(),
            estado,
            metricas.filas_iniciales,
            metricas.filas_finales,
            len(metricas.columnas_eliminadas),
            metricas.duplicados_eliminados,
            metricas.filas_excluidas_monto_cero,
            detalle,
        ],
    )


def cargar_a_duckdb(carpetas_staging: list[Path]) -> MetricasLimpieza | None:
    """
    Orquesta lectura + limpieza + carga. Devuelve las métricas de la
    corrida, o None si no había nada que procesar.
    """
    dataframes_crudos = _leer_csvs_de_staging(carpetas_staging)

    if not dataframes_crudos:
        logger.warning("No se encontraron CSV para procesar en staging.")
        return None

    df_limpio, metricas = limpiar_licitaciones(dataframes_crudos)

    con = get_connection()
    con.register("df_limpio_view", df_limpio)
    con.execute(f"CREATE OR REPLACE TABLE {TABLE_LICITACIONES_CLEAN} AS SELECT * FROM df_limpio_view")
    con.unregister("df_limpio_view")

    _registrar_log_ingesta(metricas, estado="ok")
    logger.info("Carga a DuckDB completa: %s (%d filas)", DUCKDB_PATH, metricas.filas_finales)

    return metricas
