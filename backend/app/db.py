"""
Conexión a DuckDB. Un único archivo local (warehouse.duckdb) que sirve
tanto de destino del ETL como de fuente para la API REST del panel.

DuckDB permite múltiples conexiones de lectura concurrentes desde el mismo
proceso; como FastAPI + el scheduler corren en el mismo proceso (ver
scheduler.py), no hay problema de bloqueo entre la escritura del ETL y las
lecturas de los endpoints, siempre que se reutilice esta misma conexión.
"""
from __future__ import annotations

import duckdb

from app.config import DUCKDB_PATH, TABLE_INGESTION_LOG

_connection: duckdb.DuckDBPyConnection | None = None


def get_connection() -> duckdb.DuckDBPyConnection:
    global _connection
    if _connection is None:
        _connection = duckdb.connect(str(DUCKDB_PATH))
        _inicializar_esquema(_connection)
    return _connection


def _inicializar_esquema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(f"CREATE SEQUENCE IF NOT EXISTS seq_{TABLE_INGESTION_LOG}_id START 1")
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_INGESTION_LOG} (
            id BIGINT DEFAULT nextval('seq_{TABLE_INGESTION_LOG}_id'),
            ejecutado_en TIMESTAMP NOT NULL,
            estado VARCHAR NOT NULL,
            filas_iniciales BIGINT,
            filas_finales BIGINT,
            columnas_eliminadas INTEGER,
            duplicados_eliminados BIGINT,
            filas_excluidas_monto_cero BIGINT,
            detalle VARCHAR
        )
        """
    )
    # licitaciones_clean se crea dinámicamente en loader.py vía
    # CREATE OR REPLACE TABLE ... AS SELECT, porque su esquema depende
    # de las columnas que sobrevivan al dropeo de nulos (puede variar
    # levemente entre corridas si MercadoPúblico cambia columnas).
