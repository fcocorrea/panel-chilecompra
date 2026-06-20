"""
Similitud textual refactorizada desde las secciones 16 y 21 del notebook.

Dos señales:
  1. TF-IDF (1-2 gramas): pesca copia literal de tokens entre
     DescripcionItem y EspecificacionesProveedor.
  2. Embeddings densos (sentence-transformers, MiniLM multilingüe):
     pesca paráfrasis que TF-IDF no detecta.

Cambio respecto al notebook (justificado en la conversación con el
usuario): los embeddings se cachean en DuckDB por hash del texto. El
notebook ya cacheaba por valor único DENTRO de una corrida (no
recalculaba por fila), pero acá además persistimos esos vectores entre
corridas del scheduler — si el 95% de las licitaciones de hoy ya
tenían su texto embebido ayer, no se vuelve a pasar por el modelo.
"""
from __future__ import annotations

import hashlib
import logging

import numpy as np
import pandas as pd
import polars as pl
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize as l2_normalize

from app.config import EMBEDDINGS_BATCH_SIZE, EMBEDDINGS_MODEL_NAME, TABLE_EMBEDDINGS_CACHE
from app.db import get_connection

logger = logging.getLogger("scoring.text_similarity")

TEXTO_A = "DescripcionItem"
TEXTO_B = "EspecificacionesProveedor"


def _hash_texto(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _inicializar_tabla_cache() -> None:
    con = get_connection()
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_EMBEDDINGS_CACHE} (
            texto_hash VARCHAR PRIMARY KEY,
            modelo VARCHAR NOT NULL,
            embedding BLOB NOT NULL
        )
        """
    )


def _cargar_embeddings_cacheados(hashes: list[str]) -> dict[str, np.ndarray]:
    if not hashes:
        return {}
    con = get_connection()
    placeholders = ",".join("?" * len(hashes))
    filas = con.execute(
        f"SELECT texto_hash, embedding FROM {TABLE_EMBEDDINGS_CACHE} "
        f"WHERE modelo = ? AND texto_hash IN ({placeholders})",
        [EMBEDDINGS_MODEL_NAME] + hashes,
    ).fetchall()
    return {h: np.frombuffer(emb, dtype=np.float32) for h, emb in filas}


def _guardar_embeddings_en_cache(hashes: list[str], vectores: np.ndarray) -> None:
    con = get_connection()
    filas = [
        (h, EMBEDDINGS_MODEL_NAME, vectores[i].astype(np.float32).tobytes())
        for i, h in enumerate(hashes)
    ]
    con.executemany(
        f"INSERT OR REPLACE INTO {TABLE_EMBEDDINGS_CACHE} (texto_hash, modelo, embedding) VALUES (?, ?, ?)",
        filas,
    )


def calcular_similitud_tfidf(df_items: pl.DataFrame) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """
    Sección 16 del notebook. Recibe el DataFrame a nivel item (no
    licitación) con NroLicitacion, ProveedorRUT, ResultadoOferta,
    DescripcionItem, EspecificacionesProveedor.

    Devuelve (df_pd con columna similitud_desc_esp, unique_a, unique_b)
    — las series únicas se reutilizan después para embeddings densos sin
    recalcular qué textos son distintos.
    """
    df_pd = df_items.select(["NroLicitacion", "ProveedorRUT", "ResultadoOferta", TEXTO_A, TEXTO_B]).to_pandas()
    df_pd[TEXTO_A] = df_pd[TEXTO_A].fillna("").astype(str)
    df_pd[TEXTO_B] = df_pd[TEXTO_B].fillna("").astype(str)

    unique_a = pd.Series(df_pd[TEXTO_A].unique())
    unique_b = pd.Series(df_pd[TEXTO_B].unique())

    logger.info("Textos únicos %s: %d, %s: %d", TEXTO_A, len(unique_a), TEXTO_B, len(unique_b))

    vec = TfidfVectorizer(
        max_features=30000, ngram_range=(1, 2), min_df=3,
        lowercase=True, strip_accents="unicode",
    )
    vec.fit(pd.concat([unique_a, unique_b], ignore_index=True))

    mat_a = l2_normalize(vec.transform(unique_a))
    mat_b = l2_normalize(vec.transform(unique_b))

    idx_a = pd.Series(range(len(unique_a)), index=unique_a.values)
    idx_b = pd.Series(range(len(unique_b)), index=unique_b.values)

    df_pd["_idx_a"] = df_pd[TEXTO_A].map(idx_a).astype(int)
    df_pd["_idx_b"] = df_pd[TEXTO_B].map(idx_b).astype(int)

    vecs_a = mat_a[df_pd["_idx_a"].values]
    vecs_b = mat_b[df_pd["_idx_b"].values]
    sim_row = np.asarray(vecs_a.multiply(vecs_b).sum(axis=1)).ravel()
    df_pd["similitud_desc_esp"] = sim_row

    return df_pd, unique_a, unique_b


def agregar_delta_sim_a_licitacion(lic: pl.DataFrame, df_pd: pd.DataFrame, col_sim: str, sufijo: str) -> pl.DataFrame:
    """
    Genérico para TF-IDF y denso: agrega sim_ganador_{sufijo},
    sim_perdedoras_{sufijo}, delta_sim_{sufijo} a nivel licitación.
    """
    sim_ganador = (
        df_pd[df_pd["ResultadoOferta"] == "Ganadora"]
        .groupby("NroLicitacion")[col_sim].mean()
        .rename(f"sim_ganador_{sufijo}")
    )
    sim_perdedoras = (
        df_pd[df_pd["ResultadoOferta"] == "Perdedora"]
        .groupby("NroLicitacion")[col_sim].mean()
        .rename(f"sim_perdedoras_{sufijo}")
    )
    sim_lic = pd.concat([sim_ganador, sim_perdedoras], axis=1)
    sim_lic[f"delta_sim_{sufijo}"] = sim_lic[f"sim_ganador_{sufijo}"] - sim_lic[f"sim_perdedoras_{sufijo}"]
    sim_lic = sim_lic.reset_index()

    return lic.join(pl.from_pandas(sim_lic), on="NroLicitacion", how="left")


def calcular_embeddings_densos(unique_a: pd.Series, unique_b: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """
    Sección 21 del notebook, con caché persistente en DuckDB.

    Solo se le pasan al modelo de sentence-transformers los textos cuyo
    hash NO está ya en TABLE_EMBEDDINGS_CACHE. El resto se recupera de
    DuckDB directamente, evitando reprocesar texto idéntico entre
    corridas del scheduler (cada 5h).
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        raise ImportError(
            "Falta instalar sentence-transformers para calcular embeddings densos. "
            "pip install sentence-transformers"
        ) from e

    _inicializar_tabla_cache()

    def _resolver(textos: pd.Series) -> np.ndarray:
        hashes = [_hash_texto(t) for t in textos]
        cacheados = _cargar_embeddings_cacheados(hashes)

        faltantes_idx = [i for i, h in enumerate(hashes) if h not in cacheados]
        logger.info(
            "Embeddings: %d/%d ya en caché, %d a calcular",
            len(textos) - len(faltantes_idx), len(textos), len(faltantes_idx),
        )

        if faltantes_idx:
            model_emb = SentenceTransformer(EMBEDDINGS_MODEL_NAME)
            textos_faltantes = [textos.iloc[i] for i in faltantes_idx]
            nuevos_vectores = model_emb.encode(
                textos_faltantes,
                batch_size=EMBEDDINGS_BATCH_SIZE,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )
            hashes_faltantes = [hashes[i] for i in faltantes_idx]
            _guardar_embeddings_en_cache(hashes_faltantes, nuevos_vectores)
            for h, vec in zip(hashes_faltantes, nuevos_vectores):
                cacheados[h] = vec.astype(np.float32)

        dim = len(next(iter(cacheados.values())))
        resultado = np.zeros((len(textos), dim), dtype=np.float32)
        for i, h in enumerate(hashes):
            resultado[i] = cacheados[h]
        return resultado

    emb_a = _resolver(unique_a)
    emb_b = _resolver(unique_b)
    return emb_a, emb_b
