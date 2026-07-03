"""
Aplana el detalle JSON de la API de Mercado Público a las columnas planas
que usa licitaciones_clean (mismos nombres que el CSV histórico, donde existen
— confirmados contra el esquema real de la tabla, no solo contra la lista de
columnas que lee scoring/features.py).

ponytail: el detalle de la API trae ~50 campos anidados (Comprador, Fechas,
Items...); solo se mapean correspondencias 1:1 claras. Campos de detalle de
pago/contrato con nombre ambiguo o sin equivalente evidente en la API
(TipoAdjudicacion, RubroN1-3, CodigoProductoONU...) se dejan fuera — el
upsert en api_loader.py ya recorta a las columnas que la tabla realmente
tiene, así que agregar más adelante es seguro y no requiere tocar el loader.

Una licitación "en proceso" (Publicada) todavía no tiene ofertas ni
adjudicación, así que las columnas a nivel oferta del CSV histórico
(ProveedorRUT, ResultadoOferta, MontoTotalOferta, etc.) quedan ausentes acá
a propósito: se genera UNA fila por licitación (con el primer ítem como
resumen), no una por ítem/oferta — construir_dataset_licitaciones() en
scoring/features.py ya tolera eso (n_oferentes/n_items quedan en 0 hasta que
la licitación se adjudique y el dato llegue por la vía de ingesta masiva).
"""
from __future__ import annotations

from app.config import MP_FILTRO_INSTITUCION_KEYWORD


def es_municipal(detalle: dict) -> bool:
    """Mismo alcance de sector que la descarga histórica (solo municipalidades)."""
    nombre_organismo = (detalle.get("Comprador") or {}).get("NombreOrganismo") or ""
    return MP_FILTRO_INSTITUCION_KEYWORD in nombre_organismo.upper()


def aplanar_detalle(detalle: dict) -> dict:
    """Convierte el detalle anidado de una licitación en un dict de columnas planas."""
    comprador = detalle.get("Comprador") or {}
    fechas = detalle.get("Fechas") or {}
    items = (detalle.get("Items") or {}).get("Listado") or []
    primer_item = items[0] if items else {}

    monto_estimado = detalle.get("MontoEstimado")
    visibilidad_monto = detalle.get("VisibilidadMonto")

    return {
        "NroLicitacion": detalle.get("CodigoExterno"),
        "NombreLicitacion": detalle.get("Nombre"),
        "TipoLicitacion": detalle.get("Tipo"),
        "Descripcion": detalle.get("Descripcion"),
        "MonedaLicitacion": detalle.get("Moneda"),
        "MontoEstimadoLicitacion": monto_estimado,
        "MontoEstimadoVisible": monto_estimado if visibilidad_monto == 1 else None,
        "BaseEstimacionMontoLicitacion": detalle.get("Estimacion"),
        "FuenteFinanciamiento": detalle.get("FuenteFinanciamiento"),
        "JustificacionMontoEstimado": detalle.get("JustificacionMontoEstimado"),
        "FechaPublicacion": fechas.get("FechaPublicacion"),
        "FechaInicioPreguntas": fechas.get("FechaInicio"),
        "FechaFinalPreguntas": fechas.get("FechaFinal"),
        "FechaPublicacionRespuestas": fechas.get("FechaPubRespuestas"),
        "FechaActoAperturaTecnica": fechas.get("FechaActoAperturaTecnica"),
        "FechaActoAperturaEconomica": fechas.get("FechaActoAperturaEconomica"),
        "FechaCierre": fechas.get("FechaCierre"),
        "FechaAdjudicacion": fechas.get("FechaAdjudicacion"),
        "UnidadTiempoEvaluacion": detalle.get("UnidadTiempoEvaluacion"),
        "EstadoLicitacion": detalle.get("Estado"),
        "ContemplaObrasPublicas": detalle.get("Obras"),
        "LicitacionInformada": detalle.get("Informada"),
        "TipoConvocatoria": detalle.get("TipoConvocatoria"),
        "NroEtapasLicitacion": detalle.get("Etapas"),
        "SubContratacion": detalle.get("SubContratacion"),
        "ProhibicionSubContratacion": detalle.get("ProhibicionContratacion"),
        "TomaRazonContraloria": detalle.get("TomaRazon"),
        "PublicidadOfertasTecnicas": detalle.get("EstadoPublicidadOfertas"),
        "Contrato": detalle.get("Contrato"),
        "TiempoDuracionContrato": detalle.get("TiempoDuracionContrato"),
        "UnidadTiempoDuracionContrato": detalle.get("UnidadTiempoDuracionContrato"),
        "ValorTiempoRenovacion": detalle.get("ValorTiempoRenovacion"),
        "TipoPago": detalle.get("TipoPago"),
        "ObservacionContrato": detalle.get("ObservacionContract"),
        "ExtensionPlazo": detalle.get("ExtensionPlazo"),
        "UnidadCompra": comprador.get("NombreUnidad"),
        "UnidadCompraRUT": comprador.get("RutUnidad"),
        "entCode": comprador.get("CodigoOrganismo"),
        "Institucion": comprador.get("NombreOrganismo"),
        "Sector": "Municipalidades",  # mismo alcance que la descarga histórica, ver es_municipal()
        "NombreItem": primer_item.get("NombreProducto"),
        "DescripcionItem": primer_item.get("Descripcion"),
        "UnidadMedida": primer_item.get("UnidadMedida"),
        "CantidadItem": primer_item.get("Cantidad"),
    }


if __name__ == "__main__":
    _muestra = {
        "CodigoExterno": "1003-15-LE26",
        "Nombre": "ALCANTARILLA HDPE",
        "Estado": "Publicada",
        "Comprador": {"NombreOrganismo": "I. MUNICIPALIDAD DE COYHAIQUE", "NombreUnidad": "DOM"},
        "Fechas": {"FechaPublicacion": "2026-07-01T14:12:22", "FechaCierre": "2026-07-10T13:00:00"},
        "MontoEstimado": 26000000.0,
        "VisibilidadMonto": 1,
        "Obras": "0",
        "Items": {"Listado": [{"Correlativo": 1}, {"Correlativo": 2}]},
    }
    assert es_municipal(_muestra) is True
    _plano = aplanar_detalle(_muestra)
    assert _plano["NroLicitacion"] == "1003-15-LE26"
    assert _plano["Institucion"] == "I. MUNICIPALIDAD DE COYHAIQUE"
    assert _plano["MontoEstimadoVisible"] == 26000000.0
    assert _plano["Sector"] == "Municipalidades"
    assert "NombreInstitucion" not in _plano  # esa columna no existe en licitaciones_clean

    _no_municipal = {"Comprador": {"NombreOrganismo": "MINISTERIO DE OBRAS PUBLICAS"}}
    assert es_municipal(_no_municipal) is False
    print("OK: api_mapping self-check pasó")
