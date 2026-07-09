"""
Endpoints REST que consume el panel React.

Estos leen directamente de licitaciones_clean en DuckDB. Nota importante:
esta tabla es la salida del ETL de limpieza (Licitaciones_públicas.py
refactorizado), NO todavía las columnas de score de fraude del notebook
de detección (Deteccion_Licitaciones_Dirigidas.py). Ese segundo pipeline
de modelado se conecta como una etapa posterior que lee de esta misma
tabla y escribe sus resultados en una tabla aparte
(licitaciones_scored_v3) — pendiente de integrar cuando el modelo esté
corriendo en este mismo backend.

Por ahora estos endpoints expuestos sirven la data limpia tal cual,
para no bloquear el frontend mientras se conecta el modelo.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from app.config import (
    ESTADO_LICITACION_EN_PROCESO,
    TABLE_INGESTION_LOG,
    TABLE_LICITACIONES_CLEAN,
    TABLE_LICITACIONES_SCORED,
    TABLE_SCORING_LOG,
)
from app.db import get_connection

router = APIRouter(prefix="/api", tags=["licitaciones"])


def _df_a_registros(df: pd.DataFrame) -> list[dict]:
    """
    Convierte un DataFrame a una lista de dicts lista para JSON.

    Dos problemas reales que esto resuelve:
      1. NaN/Inf (frecuentes en columnas como ratio_adj_estimado o
         delta_sim_denso cuando faltan embeddings) no son válidos en
         JSON estándar — json.dumps de FastAPI los rechaza con
         ValueError en vez de devolver un 500 silencioso o un null
         razonable.
      2. Columnas datetime64 (FechaPublicacion, FechaCierre, etc.)
         quedan como pd.Timestamp dentro del dict, y el encoder JSON
         por defecto no sabe serializarlas.
    """
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%dT%H:%M:%S")
        elif pd.api.types.is_float_dtype(df[col]):
            df[col] = df[col].replace([np.inf, -np.inf], np.nan)
        df[col] = df[col].astype(object).where(df[col].notnull(), None)
    return df.to_dict(orient="records")


def _tabla_existe(nombre_tabla: str) -> bool:
    con = get_connection()
    resultado = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
        [nombre_tabla],
    ).fetchone()
    return resultado[0] > 0


@router.get("/licitaciones")
def listar_licitaciones(
    institucion: str | None = Query(None, description="Filtro parcial por nombre de institución"),
    anio: int | None = Query(None),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
):
    if not _tabla_existe(TABLE_LICITACIONES_CLEAN):
        raise HTTPException(
            status_code=503,
            detail="Aún no hay datos cargados. Esperando la primera corrida del pipeline de ingesta.",
        )

    con = get_connection()
    condiciones = []
    parametros: list = []

    if institucion:
        condiciones.append("NombreInstitucion ILIKE ?")
        parametros.append(f"%{institucion}%")
    if anio:
        condiciones.append("EXTRACT(YEAR FROM FechaPublicacion) = ?")
        parametros.append(anio)

    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""

    total = con.execute(
        f"SELECT count(*) FROM {TABLE_LICITACIONES_CLEAN} {where}", parametros
    ).fetchone()[0]

    filas = con.execute(
        f"SELECT * FROM {TABLE_LICITACIONES_CLEAN} {where} LIMIT ? OFFSET ?",
        parametros + [limit, offset],
    ).fetchdf()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "resultados": _df_a_registros(filas),
    }


@router.get("/licitaciones/{nro_licitacion}")
def detalle_licitacion(nro_licitacion: str):
    if not _tabla_existe(TABLE_LICITACIONES_CLEAN):
        raise HTTPException(status_code=503, detail="Aún no hay datos cargados.")

    con = get_connection()
    fila = con.execute(
        f"SELECT * FROM {TABLE_LICITACIONES_CLEAN} WHERE NroLicitacion = ? LIMIT 1",
        [nro_licitacion],
    ).fetchdf()

    if fila.empty:
        raise HTTPException(status_code=404, detail="Licitación no encontrada")

    return _df_a_registros(fila)[0]


@router.get("/ingestion/status")
def estado_ingesta():
    """Para que el panel muestre cuándo fue la última actualización de datos."""
    if not _tabla_existe(TABLE_INGESTION_LOG):
        return {"corridas": []}

    con = get_connection()
    filas = con.execute(
        f"SELECT * FROM {TABLE_INGESTION_LOG} ORDER BY ejecutado_en DESC LIMIT 10"
    ).fetchdf()
    return {"corridas": _df_a_registros(filas)}


@router.get("/scoring/status")
def estado_scoring():
    """Cuándo corrió el scoring por última vez, y con qué métricas."""
    if not _tabla_existe(TABLE_SCORING_LOG):
        return {"corridas": []}

    con = get_connection()
    filas = con.execute(
        f"SELECT * FROM {TABLE_SCORING_LOG} ORDER BY ejecutado_en DESC LIMIT 10"
    ).fetchdf()
    return {"corridas": _df_a_registros(filas)}


@router.get("/scored/licitaciones")
def listar_licitaciones_scored(
    institucion: str | None = Query(None, description="Filtro parcial por nombre de institución"),
    anio: int | None = Query(None),
    score_min: float | None = Query(None, ge=0, le=100),
    solo_activas: bool = Query(
        True, description="Si es true (default), solo muestra licitaciones en proceso (no adjudicadas/cerradas)."
    ),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
):
    """Ranking de licitaciones con score_fraude_v3 — alimenta la vista principal del panel."""
    if not _tabla_existe(TABLE_LICITACIONES_SCORED):
        raise HTTPException(
            status_code=503,
            detail="Aún no hay scoring calculado. Esperando la primera corrida del pipeline de scoring (03:00 hora Chile).",
        )

    con = get_connection()
    condiciones = []
    parametros: list = []

    if institucion:
        condiciones.append("Institucion ILIKE ?")
        parametros.append(f"%{institucion}%")
    if anio:
        condiciones.append("anio_publicacion = ?")
        parametros.append(anio)
    if score_min is not None:
        condiciones.append("score_fraude_v3 >= ?")
        parametros.append(score_min)
    if solo_activas:
        condiciones.append("EstadoLicitacion = ?")
        parametros.append(ESTADO_LICITACION_EN_PROCESO)

    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""

    total = con.execute(
        f"SELECT count(*) FROM {TABLE_LICITACIONES_SCORED} {where}", parametros
    ).fetchone()[0]

    filas = con.execute(
        f"SELECT * FROM {TABLE_LICITACIONES_SCORED} {where} "
        f"ORDER BY score_fraude_v3 DESC LIMIT ? OFFSET ?",
        parametros + [limit, offset],
    ).fetchdf()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "resultados": _df_a_registros(filas),
    }


@router.get("/scored/licitaciones/{nro_licitacion}")
def detalle_licitacion_scored(nro_licitacion: str):
    if not _tabla_existe(TABLE_LICITACIONES_SCORED):
        raise HTTPException(status_code=503, detail="Aún no hay scoring calculado.")

    con = get_connection()
    fila = con.execute(
        f"SELECT * FROM {TABLE_LICITACIONES_SCORED} WHERE NroLicitacion = ? LIMIT 1",
        [nro_licitacion],
    ).fetchdf()

    if fila.empty:
        raise HTTPException(status_code=404, detail="Licitación no encontrada")

    return _df_a_registros(fila)[0]


@router.get("/scored/instituciones")
def ranking_instituciones(min_licitaciones: int = Query(10, ge=1)):
    """Ranking por institución: score promedio, % oferente único, n° de alto riesgo."""
    if not _tabla_existe(TABLE_LICITACIONES_SCORED):
        raise HTTPException(status_code=503, detail="Aún no hay scoring calculado.")

    con = get_connection()
    filas = con.execute(
        f"""
        SELECT
            Institucion AS inst,
            count(*) AS n_lic,
            round(avg(score_fraude_v3), 1) AS score_avg,
            sum(CASE WHEN score_fraude_v3 >= 70 THEN 1 ELSE 0 END) AS n_alto,
            round(avg(oferente_unico) * 100, 1) AS pct_oferente,
            round(avg(plazo_corto) * 100, 1) AS pct_plazo
        FROM {TABLE_LICITACIONES_SCORED}
        GROUP BY Institucion
        HAVING count(*) >= ?
        ORDER BY score_avg DESC
        """,
        [min_licitaciones],
    ).fetchdf()
    return {"resultados": _df_a_registros(filas)}


@router.get("/scored/red")
def red_institucion_proveedor(
    min_adjudicaciones: int = Query(3, ge=1, description="Mínimo de adjudicaciones por par para incluir la arista"),
    limit_aristas: int = Query(200, ge=10, le=1000),
):
    """Grafo bipartito institución-proveedor (nodos + aristas) para la vista de red."""
    if not _tabla_existe(TABLE_LICITACIONES_SCORED):
        raise HTTPException(status_code=503, detail="Aún no hay scoring calculado.")

    con = get_connection()
    aristas_df = con.execute(
        f"""
        SELECT
            UnidadCompraRUT AS id_inst,
            Institucion AS label_inst,
            rut_adjudicado AS id_prov,
            proveedor_adjudicado AS label_prov,
            count(*) AS n_adj,
            sum(monto_adjudicado) AS monto_total,
            round(avg(score_fraude_v3), 1) AS score_avg,
            max(comunidad_id) AS comunidad_id
        FROM {TABLE_LICITACIONES_SCORED}
        WHERE rut_adjudicado IS NOT NULL AND UnidadCompraRUT IS NOT NULL
        GROUP BY UnidadCompraRUT, Institucion, rut_adjudicado, proveedor_adjudicado
        HAVING count(*) >= ?
        ORDER BY n_adj DESC
        LIMIT ?
        """,
        [min_adjudicaciones, limit_aristas],
    ).fetchdf()

    if aristas_df.empty:
        return {"nodos": [], "aristas": []}

    nodos: dict[str, dict] = {}
    aristas = []
    for row in aristas_df.itertuples(index=False):
        inst_id = f"I:{row.id_inst}"
        prov_id = f"P:{row.id_prov}"

        if inst_id not in nodos:
            nodos[inst_id] = {
                "id": inst_id,
                "tipo": "institucion",
                "label": row.label_inst,
                "comunidad_id": int(row.comunidad_id) if pd.notnull(row.comunidad_id) else None,
                "n_adj": 0,
            }
        nodos[inst_id]["n_adj"] += int(row.n_adj)

        if prov_id not in nodos:
            nodos[prov_id] = {
                "id": prov_id,
                "tipo": "proveedor",
                "label": row.label_prov,
                "comunidad_id": None,
                "n_adj": 0,
            }
        nodos[prov_id]["n_adj"] += int(row.n_adj)

        aristas.append({
            "source": inst_id,
            "target": prov_id,
            "n_adj": int(row.n_adj),
            "monto_total": float(row.monto_total) if pd.notnull(row.monto_total) else 0.0,
            "score_avg": float(row.score_avg) if pd.notnull(row.score_avg) else 0.0,
        })

    return {"nodos": list(nodos.values()), "aristas": aristas}


@router.get("/scored/pares")
def ranking_pares(min_adjudicaciones: int = Query(5, ge=1)):
    """Pares institución-proveedor de alta concentración (proveedores cautivos)."""
    if not _tabla_existe(TABLE_LICITACIONES_SCORED):
        raise HTTPException(status_code=503, detail="Aún no hay scoring calculado.")

    con = get_connection()
    filas = con.execute(
        f"""
        SELECT
            Institucion AS inst,
            proveedor_adjudicado AS prov,
            count(*) AS n_adj,
            sum(monto_adjudicado) AS monto,
            round(avg(score_fraude_v3), 1) AS score,
            round(max(share_unidad_para_proveedor_t), 3) AS share
        FROM {TABLE_LICITACIONES_SCORED}
        WHERE rut_adjudicado IS NOT NULL
        GROUP BY Institucion, proveedor_adjudicado
        HAVING count(*) >= ?
        ORDER BY score DESC, n_adj DESC
        """,
        [min_adjudicaciones],
    ).fetchdf()
    return {"resultados": _df_a_registros(filas)}
