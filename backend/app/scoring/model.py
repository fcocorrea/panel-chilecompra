"""
Modelo de detección refactorizado desde las secciones 9, 17, 20 y 22 del
notebook (score_reglas -> score v2 -> split temporal -> score v3).

Para producción saltamos directo a la versión final (v3 con features
temporales sin leakage + texto + red), que es la única que tiene sentido
servir en el panel. v1/v2 del notebook eran pasos intermedios de
validación, no productos finales.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import polars as pl
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import MinMaxScaler, StandardScaler

from app.config import (
    ANIO_CORTE_TRAIN_TEST,
    ISO_CONTAMINATION,
    ISO_N_ESTIMATORS,
    ISO_RANDOM_STATE,
    PESO_BASES_DIRIGIDAS,
    PESO_COMUNIDAD_ALTO_SCORE,
    PESO_PROVEEDOR_CAUTIVO,
    PESO_RELACION_INTENSA,
    PESOS_REGLAS,
    UMBRAL_DELTA_SIM_BASES_DIRIGIDAS,
    UMBRAL_N_PARES_RELACION_INTENSA,
    UMBRAL_SHARE_CAUTIVO,
)

logger = logging.getLogger("scoring.model")

FEATURES_BASE = [
    "n_oferentes", "n_items", "dias_pub_cierre", "dias_eval",
    "monto_adjudicado", "MontoEstimadoLicitacion", "ratio_adj_estimado",
    "margen_2do_vs_ganador",
    "share_unidad_para_proveedor_t", "share_proveedor_para_unidad_t", "n_pares_t",
]
FEATURES_TEXTO_TFIDF = ["sim_ganador_tfidf", "sim_perdedoras_tfidf", "delta_sim_tfidf"]
FEATURES_TEXTO_DENSO = ["sim_ganador_denso", "sim_perdedoras_denso", "delta_sim_denso"]


def calcular_score_reglas(lic: pl.DataFrame) -> pl.DataFrame:
    """
    Sección 9 + 17 del notebook: suma ponderada de red flags Tier 1, más
    flags de concentración (proveedor cautivo, relación intensa) y de
    bases dirigidas (delta_sim_tfidf > umbral) cuando esas columnas ya
    existen en el DataFrame.
    """
    expr_reglas = sum(pl.col(f).fill_null(0) * w for f, w in PESOS_REGLAS.items())

    lic = lic.with_columns([
        pl.when(pl.col("share_unidad_para_proveedor_t") > UMBRAL_SHARE_CAUTIVO)
        .then(PESO_PROVEEDOR_CAUTIVO).otherwise(0.0).alias("flag_proveedor_cautivo"),
        pl.when(pl.col("n_pares_t") >= UMBRAL_N_PARES_RELACION_INTENSA)
        .then(PESO_RELACION_INTENSA).otherwise(0.0).alias("flag_relacion_intensa"),
    ])

    score_extra = pl.col("flag_proveedor_cautivo") + pl.col("flag_relacion_intensa")

    if "delta_sim_tfidf" in lic.columns:
        lic = lic.with_columns(
            pl.when(pl.col("delta_sim_tfidf").fill_null(0) > UMBRAL_DELTA_SIM_BASES_DIRIGIDAS)
            .then(PESO_BASES_DIRIGIDAS).otherwise(0.0).alias("flag_bases_dirigidas")
        )
        score_extra = score_extra + pl.col("flag_bases_dirigidas")

    if "comunidad_score_promedio" in lic.columns:
        com_mean = lic["comunidad_score_promedio"].mean() or 0.0
        com_std = lic["comunidad_score_promedio"].std() or 0.0
        umbral_comunidad = com_mean + com_std
        lic = lic.with_columns(
            pl.when(pl.col("comunidad_score_promedio").fill_null(0) > umbral_comunidad)
            .then(PESO_COMUNIDAD_ALTO_SCORE).otherwise(0.0).alias("flag_comunidad_alto_score")
        )
        score_extra = score_extra + pl.col("flag_comunidad_alto_score")

    return lic.with_columns((expr_reglas + score_extra).alias("score_reglas"))


def entrenar_isolation_forest_temporal(
    lic: pl.DataFrame, features: list[str]
) -> tuple[IsolationForest, StandardScaler, pd.Series, np.ndarray, np.ndarray, pl.DataFrame, pl.DataFrame]:
    """
    Secciones 20/22: split temporal train (< ANIO_CORTE_TRAIN_TEST) /
    test (>= ANIO_CORTE_TRAIN_TEST), entrenamiento SOLO con train,
    aplicado a ambos.

    Devuelve también train/test (los DataFrames Polars) porque loader.py
    necesita reconstruir el score por NroLicitacion para ambos splits.
    """
    features = [f for f in features if f in lic.columns]
    lic = lic.with_columns(pl.col("FechaPublicacion").dt.year().alias("anio_pub"))

    train = lic.filter(pl.col("anio_pub") < ANIO_CORTE_TRAIN_TEST)
    test = lic.filter(pl.col("anio_pub") >= ANIO_CORTE_TRAIN_TEST)

    # ponytail: si no hay datos de entrenamiento (ej. solo datos recientes), entrenar con todo
    if len(train) == 0:
        logger.warning("Sin datos de train (< %d); entrenando con todos los datos.", ANIO_CORTE_TRAIN_TEST)
        train = lic
        test = lic

    X_train = train.select(features).to_pandas()
    X_test = test.select(features).to_pandas()

    medianas = X_train.median(numeric_only=True)
    X_train = X_train.fillna(medianas).fillna(0)
    X_test = X_test.fillna(medianas).fillna(0)

    scaler = StandardScaler().fit(X_train)
    X_train_s = scaler.transform(X_train)
    X_test_s = scaler.transform(X_test)

    logger.info("Train: %d licitaciones (< %d)", len(X_train), ANIO_CORTE_TRAIN_TEST)
    logger.info("Test:  %d licitaciones (>= %d)", len(X_test), ANIO_CORTE_TRAIN_TEST)

    iso = IsolationForest(
        contamination=ISO_CONTAMINATION,
        random_state=ISO_RANDOM_STATE,
        n_estimators=ISO_N_ESTIMATORS,
        n_jobs=-1,
    )
    iso.fit(X_train_s)

    iso_train = -iso.score_samples(X_train_s)
    iso_test = -iso.score_samples(X_test_s)

    pct_train = (iso.predict(X_train_s) == -1).mean() * 100
    pct_test = (iso.predict(X_test_s) == -1).mean() * 100
    logger.info("Anomalías train: %.2f%% | test: %.2f%%", pct_train, pct_test)

    return iso, scaler, medianas, iso_train, iso_test, train, test


def combinar_score_final(
    lic: pl.DataFrame,
    train: pl.DataFrame,
    test: pl.DataFrame,
    iso_train: np.ndarray,
    iso_test: np.ndarray,
    nombre_columna_score: str = "score_fraude_v3",
) -> pl.DataFrame:
    """
    Pega el iso_score de train/test de vuelta a lic por NroLicitacion,
    combina 60% reglas + 40% Isolation Forest, normaliza 0-100.
    Sección 22 del notebook (score_fraude_v3).
    """
    train_pd = train.select("NroLicitacion").to_pandas()
    train_pd["iso_score_temporal"] = iso_train
    test_pd = test.select("NroLicitacion").to_pandas()
    test_pd["iso_score_temporal"] = iso_test

    iso_df = pd.concat([train_pd, test_pd], ignore_index=True)
    lic = lic.join(pl.from_pandas(iso_df), on="NroLicitacion", how="left")

    mm = MinMaxScaler()
    score_reglas_norm = mm.fit_transform(lic[["score_reglas"]].to_pandas())[:, 0]
    score_iso_norm = mm.fit_transform(lic[["iso_score_temporal"]].to_pandas())[:, 0]

    score_combinado = 0.6 * np.nan_to_num(score_reglas_norm) + 0.4 * np.nan_to_num(score_iso_norm)
    maximo = np.nanmax(score_combinado) if np.nanmax(score_combinado) > 0 else 1.0
    score_final = (score_combinado / maximo * 100).round(2)

    return lic.with_columns(pl.Series(nombre_columna_score, score_final))
