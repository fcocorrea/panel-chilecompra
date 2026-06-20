"""
Feature engineering refactorizado desde 'Deteccion_Licitaciones_Dirigidas.py'.

Cubre las secciones 4-9 y 19 del notebook original:
  - Cambio de granularidad: de (licitación × proveedor × ítem) a una fila
    por licitación (cabecera + métricas + ganador + perdedoras).
  - Tier 1: red flags estructurales (oferente único, plazo corto, etc.)
  - Tier 2 temporal: concentración comprador-proveedor calculada SOLO con
    histórico anterior a la fecha de publicación de cada licitación (sin
    leakage). El notebook original primero calculaba un snapshot global
    (sección 7) y luego lo corregía en la sección 19; aquí vamos directo
    a la versión correcta porque es la que sirve para producción.

Todo en Polars, igual que el notebook, por rendimiento con ~90k+ filas.
"""
from __future__ import annotations

import polars as pl

from app.config import SENTINELAS_TEXTO

COLS_CABECERA = [
    "NroLicitacion", "TipoLicitacion", "MontoEstimadoLicitacion",
    "MontoEstimadoVisible", "BaseEstimacionMontoLicitacion", "FuenteFinanciamiento",
    "JustificacionMontoEstimado", "FechaPublicacion", "FechaCierre",
    "FechaAdjudicacion", "EstadoLicitacion", "TipoConvocatoria",
    "NroEtapasLicitacion", "SubContratacion", "TomaRazonContraloria",
    "PublicidadOfertasTecnicas", "TipoPago", "UnidadCompra", "UnidadCompraRUT",
    "Institucion", "RubroN1", "RubroN2", "ContemplaObrasPublicas",
]


def construir_dataset_licitaciones(df: pl.DataFrame) -> pl.DataFrame:
    """
    Cambia la granularidad de (licitación × proveedor × ítem) a una fila
    por licitación. Replica la sección 5 del notebook.
    """
    cols_cabecera = [c for c in COLS_CABECERA if c in df.columns]
    cabecera = df.unique(subset=["NroLicitacion"], maintain_order=True).select(cols_cabecera)

    metricas = df.group_by("NroLicitacion").agg([
        pl.col("ProveedorRUT").n_unique().alias("n_oferentes"),
        pl.col("NombreItem").n_unique().alias("n_items"),
        pl.col("CantidadItem").sum().alias("cant_total_items"),
    ])

    ganador = (
        df.filter(pl.col("ResultadoOferta") == "Ganadora")
        .group_by("NroLicitacion")
        .agg([
            pl.col("ProveedorRUT").first().alias("rut_adjudicado"),
            pl.col("Proveedor").first().alias("proveedor_adjudicado"),
            pl.col("TamanoProveedor").first().alias("tamano_adjudicado"),
            pl.col("ActividadProveedor").first().alias("actividad_adjudicado"),
            pl.col("MontoTotalOferta").sum().alias("monto_adjudicado"),
        ])
    )

    perdedoras = (
        df.filter((pl.col("ResultadoOferta") == "Perdedora") & (pl.col("MontoTotalOferta") > 0))
        .group_by("NroLicitacion")
        .agg([
            pl.col("MontoTotalOferta").min().alias("monto_2do_lugar"),
            pl.col("MontoTotalOferta").mean().alias("monto_perdedoras_avg"),
        ])
    )

    lic = (
        cabecera
        .join(metricas, on="NroLicitacion", how="left")
        .join(ganador, on="NroLicitacion", how="left")
        .join(perdedoras, on="NroLicitacion", how="left")
    )
    return lic


def parsear_fechas(df: pl.DataFrame) -> pl.DataFrame:
    """Convierte las columnas Fecha* (texto) a datetime real. Sección 4."""
    fecha_cols = [c for c in df.columns if c.startswith("Fecha")]
    return df.with_columns([
        pl.col(c).str.strptime(pl.Datetime, format="%d-%m-%Y %H:%M:%S", strict=False).alias(c)
        for c in fecha_cols
    ])


def agregar_features_tier1(lic: pl.DataFrame) -> pl.DataFrame:
    """Red flags estructurales. Sección 6 del notebook, sin cambios de lógica."""
    lic = lic.with_columns([
        ((pl.col("FechaCierre") - pl.col("FechaPublicacion")).dt.total_seconds() / 86400).alias("dias_pub_cierre"),
        ((pl.col("FechaAdjudicacion") - pl.col("FechaCierre")).dt.total_seconds() / 86400).alias("dias_eval"),
        pl.col("FechaPublicacion").dt.weekday().alias("dia_publicacion"),
        pl.col("FechaPublicacion").dt.month().alias("mes_publicacion"),
        pl.col("FechaPublicacion").dt.year().alias("anio_publicacion"),
    ])

    lic = lic.with_columns([
        (pl.col("n_oferentes") == 1).cast(pl.Int8).alias("oferente_unico"),
        (pl.col("dias_pub_cierre") < 5).cast(pl.Int8).alias("plazo_corto"),
        (pl.col("dias_eval") < 1).cast(pl.Int8).alias("evaluacion_express"),
        pl.col("dia_publicacion").is_in([6, 7]).cast(pl.Int8).alias("publicada_finde"),
        pl.when(pl.col("monto_adjudicado") > 0)
        .then(((pl.col("monto_adjudicado") % 1_000_000) == 0).cast(pl.Int8))
        .otherwise(pl.lit(0).cast(pl.Int8))
        .alias("monto_redondo_millon"),
    ])

    lic = lic.with_columns([
        pl.when((pl.col("MontoEstimadoVisible") == "Si") & (pl.col("MontoEstimadoLicitacion") > 0))
        .then(pl.col("monto_adjudicado") / pl.col("MontoEstimadoLicitacion"))
        .otherwise(None)
        .alias("ratio_adj_estimado"),
        (
            pl.col("JustificacionMontoEstimado").is_null()
            | pl.col("JustificacionMontoEstimado").is_in(list(SENTINELAS_TEXTO))
        ).cast(pl.Int8).alias("justificacion_vacia"),
    ])

    lic = lic.with_columns([
        pl.when((pl.col("ratio_adj_estimado") >= 0.99) & (pl.col("ratio_adj_estimado") <= 1.01))
        .then(1).otherwise(0).cast(pl.Int8).alias("ratio_cercano_a_1"),
        pl.when((pl.col("monto_2do_lugar").is_not_null()) & (pl.col("monto_adjudicado") > 0))
        .then((pl.col("monto_2do_lugar") - pl.col("monto_adjudicado")) / pl.col("monto_adjudicado"))
        .otherwise(None)
        .alias("margen_2do_vs_ganador"),
    ])
    return lic


def agregar_features_concentracion_temporal(lic: pl.DataFrame) -> pl.DataFrame:
    """
    Tier 2 SIN leakage. Sección 19 del notebook: para cada licitación,
    las features de concentración (cuántas veces este proveedor le ganó
    a esta unidad, cuántas veces en total) se calculan usando solo
    licitaciones anteriores en el tiempo — nunca futuras.

    A diferencia del notebook (que primero calculaba un snapshot global
    en la sección 7 y lo corregía después), aquí vamos directo a la
    versión temporal porque es la única válida para producción: una
    licitación nueva no puede usar información que todavía no existe.
    """
    lic_temp = (
        lic.filter(pl.col("rut_adjudicado").is_not_null())
        .sort("FechaPublicacion")
        .with_columns([
            (pl.col("NroLicitacion").cum_count().over(["UnidadCompraRUT", "rut_adjudicado"]) - 1).alias("n_pares_t"),
            (pl.col("NroLicitacion").cum_count().over("rut_adjudicado") - 1).alias("n_prev_proveedor_t"),
            (pl.col("NroLicitacion").cum_count().over("UnidadCompraRUT") - 1).alias("n_prev_unidad_t"),
        ])
        .with_columns([
            pl.when(pl.col("n_prev_proveedor_t") > 0)
            .then(pl.col("n_pares_t") / pl.col("n_prev_proveedor_t"))
            .otherwise(0.0)
            .alias("share_unidad_para_proveedor_t"),
            pl.when(pl.col("n_prev_unidad_t") > 0)
            .then(pl.col("n_pares_t") / pl.col("n_prev_unidad_t"))
            .otherwise(0.0)
            .alias("share_proveedor_para_unidad_t"),
        ])
        .select([
            "NroLicitacion",
            "n_pares_t", "n_prev_proveedor_t", "n_prev_unidad_t",
            "share_unidad_para_proveedor_t", "share_proveedor_para_unidad_t",
        ])
    )
    return lic.join(lic_temp, on="NroLicitacion", how="left")
