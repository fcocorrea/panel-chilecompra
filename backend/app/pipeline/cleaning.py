"""
Lógica de limpieza refactorizada desde 'Licitaciones públicas.py'.

Cambios respecto al script original:
  1. Eliminado `display(...)` — es sintaxis de notebook Jupyter, no existe
     en un script plano y rompía la ejecución headless.
  2. Eliminados todos los `plt.show()` / gráficos de Seaborn — bloquean en
     un servidor sin pantalla y no aportan al pipeline automático (el
     usuario decidió dejarlos fuera del flujo automático).
  3. CAMBIO CRÍTICO: ya no se lee un CSV estático vía
     `pd.read_csv(FILE_PATH, ...)`. La función `limpiar_licitaciones()`
     recibe los DataFrames ya cargados (uno por cada CSV extraído de los
     .7z) y los concatena antes de aplicar exactamente las mismas reglas
     de negocio del script original:
       - drop de columnas con >90% nulos
       - imputación: mediana (numéricas), 'Sin información' (texto),
         'Sin fecha' (columnas de fecha como texto)
       - deduplicación por NroLicitacion
       - inferencia de columnas numéricas mal tipadas como texto
       - exclusión de MontoNetoOferta == 0 y MontoTotalOferta == 0
  4. Devuelve un DataFrame limpio + un diccionario de métricas (las mismas
     que el script original solo imprimía) para que el loader pueda
     loguear el resultado de cada corrida en la tabla ingestion_log.

La función es pura respecto a IO: no lee ni escribe archivos. Eso es
responsabilidad de loader.py.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from app.config import (
    ID_COLUMN_DEDUP,
    MONTO_COLUMNS_EXCLUDE_ZERO,
    NULL_DROP_THRESHOLD_PCT,
)

logger = logging.getLogger("pipeline.cleaning")


@dataclass
class MetricasLimpieza:
    filas_iniciales: int = 0
    columnas_iniciales: int = 0
    columnas_eliminadas: list[str] = field(default_factory=list)
    columnas_numericas_imputadas: dict[str, float] = field(default_factory=dict)
    columnas_texto_imputadas: list[str] = field(default_factory=list)
    columnas_fecha_imputadas: list[str] = field(default_factory=list)
    duplicados_eliminados: int = 0
    columnas_reconvertidas_a_numero: list[str] = field(default_factory=list)
    filas_excluidas_monto_cero: int = 0
    filas_finales: int = 0
    columnas_finales: int = 0


def _eliminar_columnas_con_muchos_nulos(
    df: pd.DataFrame, metricas: MetricasLimpieza
) -> pd.DataFrame:
    pct_nulos = (df.isnull().sum() / len(df) * 100) if len(df) else df.isnull().sum() * 0
    cols_eliminar = pct_nulos[pct_nulos > NULL_DROP_THRESHOLD_PCT].index.tolist()
    if cols_eliminar:
        logger.info("Eliminando columnas (>%.0f%% nulos): %s", NULL_DROP_THRESHOLD_PCT, cols_eliminar)
        df = df.drop(columns=cols_eliminar)
        metricas.columnas_eliminadas = cols_eliminar
    return df


def _imputar_nulos(df: pd.DataFrame, metricas: MetricasLimpieza) -> pd.DataFrame:
    cols_numericas = df.select_dtypes(include=["number"]).columns.tolist()
    cols_texto = df.select_dtypes(include=["object"]).columns.tolist()
    cols_fecha_candidatas = [c for c in cols_texto if "fecha" in c.lower() or "date" in c.lower()]

    # Numéricas -> mediana
    for col in cols_numericas:
        n_nulos = df[col].isnull().sum()
        if n_nulos > 0:
            mediana = df[col].median()
            df[col] = df[col].fillna(mediana)
            metricas.columnas_numericas_imputadas[col] = float(mediana)

    # Texto general (no fecha) -> 'Sin información'
    cols_texto_no_fecha = [c for c in cols_texto if c not in cols_fecha_candidatas]
    for col in cols_texto_no_fecha:
        if df[col].isnull().sum() > 0:
            df[col] = df[col].fillna("Sin información")
            metricas.columnas_texto_imputadas.append(col)

    # Fecha (como texto) -> 'Sin fecha'
    for col in cols_fecha_candidatas:
        if df[col].isnull().sum() > 0:
            df[col] = df[col].fillna("Sin fecha")
            metricas.columnas_fecha_imputadas.append(col)

    return df


def _eliminar_duplicados(df: pd.DataFrame, metricas: MetricasLimpieza) -> pd.DataFrame:
    if ID_COLUMN_DEDUP not in df.columns:
        logger.warning(
            "Columna de deduplicación '%s' no está presente; se omite este paso.",
            ID_COLUMN_DEDUP,
        )
        return df

    duplicados = df.duplicated(ID_COLUMN_DEDUP).sum()
    metricas.duplicados_eliminados = int(duplicados)
    if duplicados > 0:
        df = df.drop_duplicates().reset_index(drop=True)
        logger.info("Eliminados %d duplicados por %s", duplicados, ID_COLUMN_DEDUP)
    return df


def _reconvertir_numericos_mal_tipados(
    df: pd.DataFrame, metricas: MetricasLimpieza
) -> pd.DataFrame:
    """
    Detecta columnas de texto cuyo contenido es en realidad numérico
    (posiblemente con coma decimal) y las convierte a float.

    Misma heurística del script original: ignora los valores ya imputados
    ('Sin información', 'Sin fecha', '') al evaluar si la columna es
    puramente numérica.
    """
    columnas_string = df.select_dtypes(include=["object"]).columns.tolist()
    columnas_a_transformar = []

    for col in columnas_string:
        series = df[col]
        mask = ~series.isin(["Sin información", "Sin fecha", ""]) & series.notnull()
        filtered = series[mask]

        if len(filtered) == 0:
            continue
        try:
            cast_series = pd.to_numeric(filtered.astype(str).str.replace(",", "."), errors="coerce")
            if cast_series.isnull().sum() == 0:
                columnas_a_transformar.append(col)
        except Exception:  # noqa: BLE001 — heurística best-effort, igual que el original
            pass

    for col in columnas_a_transformar:
        df[col] = pd.to_numeric(
            df[col].astype(str).str.replace(",", "."), errors="coerce"
        ).fillna(0.0)

    metricas.columnas_reconvertidas_a_numero = columnas_a_transformar
    if columnas_a_transformar:
        logger.info("Columnas reconvertidas a numérico: %s", columnas_a_transformar)
    return df


def _excluir_montos_cero(df: pd.DataFrame, metricas: MetricasLimpieza) -> pd.DataFrame:
    cols_presentes = [c for c in MONTO_COLUMNS_EXCLUDE_ZERO if c in df.columns]
    if not cols_presentes:
        logger.warning(
            "Ninguna de las columnas de monto %s está presente; se omite el filtro.",
            MONTO_COLUMNS_EXCLUDE_ZERO,
        )
        return df

    filas_antes = len(df)
    condicion = pd.Series(True, index=df.index)
    for col in cols_presentes:
        condicion &= df[col] != 0
    df = df[condicion]
    metricas.filas_excluidas_monto_cero = filas_antes - len(df)
    return df


def limpiar_licitaciones(
    dataframes_crudos: list[pd.DataFrame],
) -> tuple[pd.DataFrame, MetricasLimpieza]:
    """
    Punto de entrada del ETL. Recibe una lista de DataFrames ya leídos
    desde los CSV extraídos de cada .7z (uno por año/semestre) y aplica
    las mismas reglas de negocio del script original sobre el conjunto
    concatenado.

    Esto reemplaza la línea original:
        df = pd.read_csv(FILE_PATH, sep=';', low_memory=False, encoding='latin-1')
    El leer-desde-CSV-fijo ahora vive en loader.py (lectura de cada CSV
    extraído); aquí solo entra la data ya en memoria.
    """
    if not dataframes_crudos:
        raise ValueError("No hay DataFrames para limpiar (lista vacía).")

    df = pd.concat(dataframes_crudos, ignore_index=True)

    metricas = MetricasLimpieza(
        filas_iniciales=len(df),
        columnas_iniciales=df.shape[1],
    )

    df = _eliminar_columnas_con_muchos_nulos(df, metricas)
    df = _imputar_nulos(df, metricas)
    df = _eliminar_duplicados(df, metricas)
    df = _reconvertir_numericos_mal_tipados(df, metricas)
    df = _excluir_montos_cero(df, metricas)

    metricas.filas_finales = len(df)
    metricas.columnas_finales = df.shape[1]

    logger.info(
        "Limpieza completa: %d -> %d filas, %d -> %d columnas",
        metricas.filas_iniciales,
        metricas.filas_finales,
        metricas.columnas_iniciales,
        metricas.columnas_finales,
    )

    return df, metricas
