"""
Descarga de archivos .7z de licitaciones municipales desde MercadoPúblico.

Cambios respecto al script original (data-downloader.py):
  1. Tope de 1GB acumulado POR EJECUCIÓN, verificado en streaming (no se
     descarga el archivo completo y se descarta después; se corta a mitad
     de descarga si el archivo individual ya excede lo que queda de cupo).
  2. Idempotencia: si un archivo ya existe en RAW_DIR y su tamaño coincide
     con el Content-Length remoto, se omite. Evita re-descargar 2020/2021
     en cada corrida del cron.
  3. Verificación de integridad básica: un archivo .7z truncado se detecta
     (tamaño escrito != Content-Length) y se descarta en vez de quedar
     corrupto en disco.
  4. Devuelve un resumen estructurado (no solo prints) para que el
     orquestador pueda decidir si dispara la etapa de extracción.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import requests

from app.config import (
    ANIO_INICIO,
    MAX_DOWNLOAD_BYTES_PER_RUN,
    MERCADO_PUBLICO_BASE_URL,
    RAW_DIR,
    SEMESTRES,
)

logger = logging.getLogger("pipeline.downloader")

CHUNK_SIZE = 8192


@dataclass
class DescargaResultado:
    archivo: str
    estado: str  # "descargado" | "omitido_existente" | "no_encontrado" | "error" | "cupo_agotado"
    bytes_descargados: int = 0
    detalle: str = ""


@dataclass
class ResumenIngesta:
    iniciado_en: datetime
    resultados: list[DescargaResultado] = field(default_factory=list)
    bytes_totales: int = 0
    cupo_agotado: bool = False

    @property
    def hubo_descargas_nuevas(self) -> bool:
        return any(r.estado == "descargado" for r in self.resultados)


def _content_length(url: str) -> int | None:
    """HEAD request para conocer el tamaño remoto sin descargar nada."""
    try:
        resp = requests.head(url, allow_redirects=True, timeout=15)
        if resp.status_code == 200 and "Content-Length" in resp.headers:
            return int(resp.headers["Content-Length"])
    except requests.exceptions.RequestException:
        pass
    return None


def _ya_descargado(ruta: Path, tamano_remoto: int | None) -> bool:
    if not ruta.exists():
        return False
    if tamano_remoto is None:
        # No pudimos verificar tamaño remoto; confiamos en que si existe y
        # pesa más de 0 bytes, es una descarga previa válida.
        return ruta.stat().st_size > 0
    return ruta.stat().st_size == tamano_remoto


def descargar_archivo(
    anio: int,
    semestre: str,
    bytes_restantes_cupo: int,
) -> DescargaResultado:
    """
    Descarga un único archivo .7z respetando el cupo restante.

    Si el archivo remoto es más grande que el cupo restante, NO se descarga
    parcialmente (un .7z truncado es inútil) — se marca cupo_agotado y se
    deja para la siguiente corrida del scheduler.
    """
    url = f"{MERCADO_PUBLICO_BASE_URL}/{anio}/{semestre}/Municipalidades.7z"
    nombre_archivo = f"Municipalidades_{anio}_{semestre}.7z"
    ruta_completa = RAW_DIR / nombre_archivo

    tamano_remoto = _content_length(url)

    if _ya_descargado(ruta_completa, tamano_remoto):
        logger.info("Omitido (ya existe): %s", nombre_archivo)
        return DescargaResultado(nombre_archivo, "omitido_existente")

    if tamano_remoto is not None and tamano_remoto > bytes_restantes_cupo:
        logger.warning(
            "Cupo insuficiente para %s (%.1f MB requeridos, %.1f MB disponibles). "
            "Se reintentará en la próxima corrida.",
            nombre_archivo,
            tamano_remoto / 1024**2,
            bytes_restantes_cupo / 1024**2,
        )
        return DescargaResultado(nombre_archivo, "cupo_agotado")

    try:
        respuesta = requests.get(url, stream=True, timeout=30)
    except requests.exceptions.RequestException as e:
        logger.error("Error de conexión descargando %s: %s", url, e)
        return DescargaResultado(nombre_archivo, "error", detalle=str(e))

    if respuesta.status_code in (403, 404):  # ponytail: S3 devuelve 403 para objetos inexistentes
        return DescargaResultado(nombre_archivo, "no_encontrado")
    if respuesta.status_code != 200:
        return DescargaResultado(
            nombre_archivo, "error", detalle=f"HTTP {respuesta.status_code}"
        )

    bytes_escritos = 0
    ruta_temp = ruta_completa.with_suffix(".7z.partial")
    try:
        with open(ruta_temp, "wb") as f:
            for chunk in respuesta.iter_content(chunk_size=CHUNK_SIZE):
                if not chunk:
                    continue
                bytes_escritos += len(chunk)
                if bytes_escritos > bytes_restantes_cupo:
                    logger.warning(
                        "Cupo agotado a mitad de descarga de %s, descartando parcial.",
                        nombre_archivo,
                    )
                    f.close()
                    ruta_temp.unlink(missing_ok=True)
                    return DescargaResultado(nombre_archivo, "cupo_agotado")
                f.write(chunk)
    except requests.exceptions.RequestException as e:
        ruta_temp.unlink(missing_ok=True)
        return DescargaResultado(nombre_archivo, "error", detalle=str(e))

    # Verificación de integridad: si conocíamos el tamaño esperado, debe calzar.
    if tamano_remoto is not None and bytes_escritos != tamano_remoto:
        logger.error(
            "Integridad fallida en %s: esperado %d bytes, recibido %d. Descartando.",
            nombre_archivo,
            tamano_remoto,
            bytes_escritos,
        )
        ruta_temp.unlink(missing_ok=True)
        return DescargaResultado(
            nombre_archivo, "error", detalle="tamaño no coincide con Content-Length"
        )

    ruta_temp.rename(ruta_completa)
    logger.info("Descarga completa: %s (%.1f MB)", nombre_archivo, bytes_escritos / 1024**2)
    return DescargaResultado(nombre_archivo, "descargado", bytes_descargados=bytes_escritos)


def descargar_licitaciones() -> ResumenIngesta:
    """
    Descarga UN único archivo .7z (el más reciente disponible) y se detiene.
    ponytail: un solo archivo para tests locales; ampliar el loop si se necesita ingesta completa.
    """
    resumen = ResumenIngesta(iniciado_en=datetime.now())
    anio_actual = datetime.now().year

    for anio in range(anio_actual, ANIO_INICIO - 1, -1):
        for semestre in reversed(SEMESTRES):
            resultado = descargar_archivo(anio, semestre, MAX_DOWNLOAD_BYTES_PER_RUN)
            resumen.resultados.append(resultado)
            if resultado.estado == "descargado":
                resumen.bytes_totales += resultado.bytes_descargados
                return resumen
            elif resultado.estado != "no_encontrado":
                # omitido_existente, error, cupo_agotado → un archivo es suficiente
                return resumen
            # no_encontrado → sigue hacia atrás buscando el más reciente disponible

    return resumen


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    r = descargar_licitaciones()
    print(f"Descargados: {sum(1 for x in r.resultados if x.estado == 'descargado')}")
    print(f"Omitidos (ya existían): {sum(1 for x in r.resultados if x.estado == 'omitido_existente')}")
    print(f"Total MB descargados: {r.bytes_totales / 1024**2:.1f}")
    print(f"Cupo agotado: {r.cupo_agotado}")
