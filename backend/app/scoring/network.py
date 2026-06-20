"""
Análisis de red refactorizado desde la sección 15 del notebook.

Construye el grafo bipartito institución ↔ proveedor, detecta comunidades
con Louvain, y devuelve features de comunidad a nivel licitación
(comunidad_id, comunidad_score_promedio, comunidad_pct_oferente_unico).

Nota: en esta primera corrida de producción, comunidad_score_promedio se
calcula sobre score_reglas (la única señal disponible antes de tener el
score combinado final) — igual al notebook, que ya tenía score_fraude
calculado en este punto del pipeline porque corría v1 antes de la
sección 15. Aquí seguimos el mismo orden.
"""
from __future__ import annotations

import logging

import networkx as nx
import pandas as pd
import polars as pl
from networkx.algorithms.community import louvain_communities

logger = logging.getLogger("scoring.network")


def construir_grafo_institucion_proveedor(lic: pl.DataFrame, score_col: str) -> tuple[nx.Graph, pd.DataFrame]:
    adj_pares = (
        lic.filter(pl.col("rut_adjudicado").is_not_null())
        .group_by(["UnidadCompraRUT", "rut_adjudicado"])
        .agg([
            pl.len().alias("n_adj"),
            pl.col("monto_adjudicado").sum().alias("monto_total"),
            pl.col(score_col).mean().alias("score_promedio"),
            pl.col("Institucion").first().alias("institucion"),
            pl.col("proveedor_adjudicado").first().alias("proveedor"),
        ])
    ).to_pandas()

    G = nx.Graph()
    for _, row in adj_pares.iterrows():
        inst_node = ("I", row["UnidadCompraRUT"])
        prov_node = ("P", row["rut_adjudicado"])
        G.add_node(inst_node, kind="institucion", label=str(row["institucion"])[:50])
        G.add_node(prov_node, kind="proveedor", label=str(row["proveedor"])[:50])
        G.add_edge(
            inst_node, prov_node,
            weight=float(row["n_adj"]),
            monto=float(row["monto_total"]),
            score=float(row["score_promedio"]),
        )
    return G, adj_pares


def detectar_comunidades_y_features(lic: pl.DataFrame, score_col: str) -> pl.DataFrame:
    """
    Devuelve lic con tres columnas nuevas: comunidad_id,
    comunidad_score_promedio, comunidad_pct_oferente_unico.
    """
    G, _ = construir_grafo_institucion_proveedor(lic, score_col)

    logger.info(
        "Grafo construido: %d nodos, %d aristas, densidad %.6f",
        G.number_of_nodes(), G.number_of_edges(), nx.density(G),
    )

    comunidades = louvain_communities(G, weight="weight", resolution=1.0, seed=42)
    comunidades = sorted(comunidades, key=len, reverse=True)
    logger.info("Comunidades detectadas: %d", len(comunidades))

    node_to_comm: dict = {}
    for i, comm in enumerate(comunidades):
        for n in comm:
            node_to_comm[n] = i

    inst_to_comm = {k[1]: v for k, v in node_to_comm.items() if k[0] == "I"}

    lic_pd = lic.to_pandas()
    lic_pd["comunidad_id"] = lic_pd["UnidadCompraRUT"].map(inst_to_comm).fillna(-1).astype(int)

    comm_stats = (
        lic_pd[lic_pd["comunidad_id"] >= 0]
        .groupby("comunidad_id")
        .agg(
            n_licitaciones=("NroLicitacion", "count"),
            comunidad_score_promedio=(score_col, "mean"),
            pct_oferente_unico=("oferente_unico", "mean"),
        )
        .reset_index()
        .rename(columns={"pct_oferente_unico": "comunidad_pct_oferente_unico"})
    )

    lic_pd = lic_pd.merge(
        comm_stats[["comunidad_id", "comunidad_score_promedio", "comunidad_pct_oferente_unico"]],
        on="comunidad_id", how="left",
    )

    return pl.from_pandas(lic_pd)
