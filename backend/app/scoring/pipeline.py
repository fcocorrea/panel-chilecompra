"""
Orquestador del pipeline de scoring completo.

Encadena, en orden, los módulos que replican el notebook
'Deteccion_Licitaciones_Dirigidas.py':

  1. Lee licitaciones_clean (salida del ETL de ingesta) desde DuckDB.
  2. features.py        -> cambio de granularidad + Tier 1 + Tier 2 temporal
  3. text_similarity.py -> TF-IDF + embeddings densos (cacheados)
  4. network.py          -> grafo bipartito + comunidades Louvain
  5. model.py             -> score por reglas + Isolation Forest temporal + score v3
  6. Escribe licitaciones_scored_v3 a DuckDB + un registro en scoring_log

Esta función es la que invoca scoring/scheduler.py una vez al día.
"""
from __future__ import annotations

import logging
from datetime import datetime

import polars as pl

from app.config import (
    TABLE_LICITACIONES_CLEAN,
    TABLE_LICITACIONES_SCORED,
    TABLE_SCORING_LOG,
)
from app.db import get_connection
from app.scoring.features import (
    agregar_features_concentracion_temporal,
    agregar_features_tier1,
    construir_dataset_licitaciones,
    parsear_fechas,
)
from app.scoring.model import (
    FEATURES_BASE,
    FEATURES_TEXTO_DENSO,
    FEATURES_TEXTO_TFIDF,
    calcular_score_reglas,
    combinar_score_final,
    entrenar_isolation_forest_temporal,
)
from app.scoring.network import detectar_comunidades_y_features
from app.scoring.text_similarity import (
    agregar_delta_sim_a_licitacion,
    calcular_embeddings_densos,
    calcular_similitud_tfidf,
)

logger = logging.getLogger("scoring.pipeline")


def _leer_licitaciones_clean() -> pl.DataFrame:
    con = get_connection()
    df_pd = con.execute(f"SELECT * FROM {TABLE_LICITACIONES_CLEAN}").fetchdf()
    return pl.from_pandas(df_pd)


def _registrar_log_scoring(estado: str, detalle: dict) -> None:
    con = get_connection()
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_SCORING_LOG} (
            id BIGINT,
            ejecutado_en TIMESTAMP NOT NULL,
            estado VARCHAR NOT NULL,
            n_licitaciones BIGINT,
            detalle VARCHAR
        )
        """
    )
    siguiente_id = con.execute(f"SELECT COALESCE(MAX(id), 0) + 1 FROM {TABLE_SCORING_LOG}").fetchone()[0]
    con.execute(
        f"INSERT INTO {TABLE_SCORING_LOG} VALUES (?, ?, ?, ?, ?)",
        [siguiente_id, datetime.now(), estado, detalle.get("n_licitaciones", 0), str(detalle)],
    )


def ejecutar_scoring_completo() -> dict:
    """
    Punto de entrada único. Devuelve un dict con métricas de la corrida
    para que scoring/scheduler.py lo persista en scoring_state.json.
    """
    df_raw = _leer_licitaciones_clean()
    if df_raw.height == 0:
        logger.warning("licitaciones_clean está vacía; no hay nada que scorear.")
        _registrar_log_scoring("sin_datos", {"n_licitaciones": 0})
        return {"estado": "sin_datos", "n_licitaciones": 0}

    logger.info("Licitaciones crudas (nivel item) leídas: %d filas", df_raw.height)

    df_raw = parsear_fechas(df_raw)
    lic = construir_dataset_licitaciones(df_raw)
    logger.info("Dataset a nivel licitación: %d filas", lic.height)

    lic = agregar_features_tier1(lic)
    lic = agregar_features_concentracion_temporal(lic)

    # --- Texto: TF-IDF siempre; embeddings densos best-effort -------------
    df_pd_tfidf, unique_a, unique_b = calcular_similitud_tfidf(df_raw)
    lic = agregar_delta_sim_a_licitacion(lic, df_pd_tfidf, "similitud_desc_esp", "tfidf")

    try:
        emb_a, emb_b = calcular_embeddings_densos(unique_a, unique_b)
        idx_a = df_pd_tfidf["_idx_a"].values
        idx_b = df_pd_tfidf["_idx_b"].values
        df_pd_tfidf["similitud_dense"] = (emb_a[idx_a] * emb_b[idx_b]).sum(axis=1)
        lic = agregar_delta_sim_a_licitacion(lic, df_pd_tfidf, "similitud_dense", "denso")
        features_texto = FEATURES_TEXTO_TFIDF + FEATURES_TEXTO_DENSO
    except Exception as e:  # noqa: BLE001
        # Cubre tanto la ausencia del paquete (ImportError) como fallas de
        # red al descargar el modelo desde HuggingFace (HfHubHTTPError,
        # OSError, timeouts) — ninguna de las dos debe tumbar el pipeline
        # completo. El scoring sigue siendo válido solo con TF-IDF.
        logger.warning(
            "Embeddings densos omitidos (%s: %s). Se continúa solo con TF-IDF.",
            type(e).__name__, e,
        )
        features_texto = FEATURES_TEXTO_TFIDF

    # --- Red: necesita un score preliminar para comunidad_score_promedio --
    lic_preliminar = calcular_score_reglas(lic)
    lic = detectar_comunidades_y_features(lic_preliminar, score_col="score_reglas")

    # Recalcular score_reglas ahora que sí existen comunidad_* y delta_sim_tfidf
    lic = calcular_score_reglas(lic)

    # --- Modelo: Isolation Forest con split temporal -----------------------
    features_modelo = FEATURES_BASE + features_texto
    iso, scaler, medianas, iso_train, iso_test, train, test = entrenar_isolation_forest_temporal(
        lic, features_modelo
    )
    lic = combinar_score_final(lic, train, test, iso_train, iso_test, "score_fraude_v3")

    # --- Carga a DuckDB -----------------------------------------------------
    con = get_connection()
    lic_pd = lic.to_pandas()
    con.register("lic_scored_view", lic_pd)
    con.execute(
        f"CREATE OR REPLACE TABLE {TABLE_LICITACIONES_SCORED} AS SELECT * FROM lic_scored_view"
    )
    con.unregister("lic_scored_view")

    detalle = {
        "n_licitaciones": lic.height,
        "n_features_modelo": len(features_modelo),
        "incluye_embeddings_densos": features_texto == FEATURES_TEXTO_TFIDF + FEATURES_TEXTO_DENSO,
        "score_promedio": float(lic["score_fraude_v3"].mean()),
        "score_max": float(lic["score_fraude_v3"].max()),
    }
    _registrar_log_scoring("ok", detalle)
    logger.info("Scoring completo: %s", detalle)

    return {"estado": "ok", **detalle}
