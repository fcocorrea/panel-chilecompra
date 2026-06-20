"""
Descompresión de los .7z descargados hacia STAGING_DIR.

Usa py7zr (puro Python, sin dependencias del sistema como el binario `7z`).
Cada .7z se descomprime a su propia subcarpeta en staging para no mezclar
archivos de distintos año/semestre si llegan a tener nombres iguales
(ej. todos podrían traer un "Municipalidades.csv" interno).
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

import py7zr

from app.config import RAW_DIR, STAGING_DIR

logger = logging.getLogger("pipeline.extractor")


@dataclass
class ExtraccionResultado:
    archivo_origen: str
    carpeta_destino: Path
    estado: str  # "extraido" | "ya_extraido" | "error"
    detalle: str = ""


def _carpeta_destino_para(archivo_7z: Path) -> Path:
    return STAGING_DIR / archivo_7z.stem  # Municipalidades_2024_Sem1


def extraer_archivo(archivo_7z: Path) -> ExtraccionResultado:
    destino = _carpeta_destino_para(archivo_7z)

    # Idempotencia: si ya fue extraído y tiene contenido, no repetir trabajo.
    if destino.exists() and any(destino.iterdir()):
        return ExtraccionResultado(archivo_7z.name, destino, "ya_extraido")

    destino.mkdir(parents=True, exist_ok=True)

    try:
        with py7zr.SevenZipFile(archivo_7z, mode="r") as archivo:
            archivo.extractall(path=destino)
    except py7zr.exceptions.Bad7zFile as e:
        logger.error("Archivo .7z corrupto: %s — %s", archivo_7z.name, e)
        shutil.rmtree(destino, ignore_errors=True)
        return ExtraccionResultado(archivo_7z.name, destino, "error", detalle=str(e))
    except Exception as e:  # noqa: BLE001 — queremos capturar cualquier fallo de extracción
        logger.error("Error extrayendo %s: %s", archivo_7z.name, e)
        shutil.rmtree(destino, ignore_errors=True)
        return ExtraccionResultado(archivo_7z.name, destino, "error", detalle=str(e))

    logger.info("Extraído: %s -> %s", archivo_7z.name, destino)
    return ExtraccionResultado(archivo_7z.name, destino, "extraido")


def extraer_pendientes() -> list[ExtraccionResultado]:
    """Extrae todos los .7z presentes en RAW_DIR que aún no tengan staging."""
    resultados = []
    for archivo_7z in sorted(RAW_DIR.glob("*.7z")):
        resultados.append(extraer_archivo(archivo_7z))
    return resultados


def limpiar_staging(carpeta: Path) -> None:
    """Borra una carpeta de staging ya cargada a DuckDB para no acumular disco."""
    shutil.rmtree(carpeta, ignore_errors=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for r in extraer_pendientes():
        print(f"{r.archivo_origen}: {r.estado} ({r.detalle})")
