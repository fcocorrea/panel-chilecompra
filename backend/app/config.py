"""
Configuración central del backend.

Todas las rutas y constantes de negocio viven aquí para que el resto del
código nunca tenga rutas o números mágicos hardcodeados.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # lee backend/.env si existe; en producción se puede setear a nivel de proceso/servicio

# --- Rutas base -------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"            # .7z descargados, uno por año-semestre
STAGING_DIR = DATA_DIR / "staging"    # descomprimidos temporales (se limpian post-carga)
DUCKDB_PATH = DATA_DIR / "warehouse.duckdb"
STATE_PATH = BASE_DIR / "state.json"  # estado del scheduler tolerante a fallos
REALTIME_STATE_PATH = BASE_DIR / "realtime_state.json"  # estado del scheduler de ingesta API

for d in (RAW_DIR, STAGING_DIR):
    d.mkdir(parents=True, exist_ok=True)

# --- Descarga -----------------------------------------------------------
MERCADO_PUBLICO_BASE_URL = "https://chc-lic-files.mercadopublico.cl/sector"
ANIO_INICIO = 2020
SEMESTRES = ("Sem1", "Sem2")

# Tope de descarga por ejecución completa del job (no por archivo individual).
# Pensado para el entorno local de pruebas; en el servidor 24/7 esto se sube
# o se elimina vía variable de entorno (ver MAX_DOWNLOAD_BYTES_OVERRIDE abajo).
MAX_DOWNLOAD_BYTES_PER_RUN = 1 * 1024 ** 3  # 1 GiB

# --- API pública de Mercado Público (licitaciones recientes/en proceso) ---
# Credenciales por variable de entorno (backend/.env, ver .env.example) — nunca hardcodeadas.
MP_API_TICKET = os.getenv("TICKET_API") or os.getenv("API_KEY")
MP_API_USUARIO = os.getenv("USUARIO_API")
MP_API_BASE_URL = "https://api.mercadopublico.cl/servicios/v1/publico/licitaciones.json"
MP_API_TIMEOUT_SECONDS = 20
MP_API_MAX_REINTENTOS = 3
MP_API_ESPERA_ENTRE_REINTENTOS_SEG = 2.0
MP_API_ESPERA_ENTRE_LLAMADAS_SEG = 2.0  # observado: <1 req/seg dispara 429 en el primer intento (no documentado)
# Tope de licitaciones detalladas a consultar por corrida (cupo diario documentado: 10.000 tickets/día).
MP_API_MAX_DETALLES_POR_CORRIDA = 5000
# Filtro de sector: mismo alcance que la descarga histórica (solo licitaciones municipales).
MP_FILTRO_INSTITUCION_KEYWORD = "MUNICIPALIDAD"
# Estado (texto, tal como lo devuelve la API en el campo "Estado") considerado "en proceso" para el panel.
ESTADO_LICITACION_EN_PROCESO = "Publicada"

# --- Scheduler ------------------------------------------------------------
# Ingesta de datos: una vez al día, hora fija de baja carga en horario de
# Chile (mismo patrón CronTrigger + ventana de tolerancia que scoring).
INGESTION_TIMEZONE = "America/Santiago"
INGESTION_HOUR = 3
INGESTION_MINUTE = 0
INGESTION_MIN_HOURS_BETWEEN_RUNS = 20  # tolerancia: evita doble corrida si el proceso se reinicia cerca de las 03:00

# Ingesta API: más frecuente que la bulk porque cada corrida solo procesa
# licitaciones nuevas o con cambio de estado (ver pipeline/api_loader.py).
REALTIME_INTERVAL_HOURS = 1

# Scoring (reentrenamiento): una vez a la semana, misma hora fija de baja
# carga en horario de Chile. Chile cambia de horario (verano/invierno), así
# que se resuelve la zona con zoneinfo (requiere tzdata) en vez de
# hardcodear un offset UTC fijo.
SCORING_TIMEZONE = "America/Santiago"
SCORING_HOUR = 3
SCORING_MINUTE = 0
SCORING_DAY_OF_WEEK = "mon"
SCORING_MIN_HOURS_BETWEEN_RUNS = 24 * 7 - 4  # tolerancia semanal: evita doble corrida si el proceso se reinicia cerca de las 03:00 del lunes

# --- Limpieza (ETL) -------------------------------------------------------
CSV_SEPARATOR = ";"
CSV_ENCODING = "latin-1"
NULL_DROP_THRESHOLD_PCT = 90.0  # columnas con más de esto % de nulos se eliminan
ID_COLUMN_DEDUP = "NroLicitacion"
MONTO_COLUMNS_EXCLUDE_ZERO = ("MontoNetoOferta", "MontoTotalOferta")

# --- DuckDB -----------------------------------------------------------
TABLE_LICITACIONES_CLEAN = "licitaciones_clean"
TABLE_INGESTION_LOG = "ingestion_log"

# --- Scoring (detección de licitaciones dirigidas) ------------------------
TABLE_LICITACIONES_SCORED = "licitaciones_scored_v3"
TABLE_EMBEDDINGS_CACHE = "embeddings_cache"
TABLE_SCORING_LOG = "scoring_log"
SCORING_LOCK_PATH = BASE_DIR / "scoring.lock"

SENTINELAS_TEXTO = ("Sin información", "Sin fecha", "")

# Split temporal: train con licitaciones publicadas antes de este año,
# test con las publicadas desde este año en adelante (igual que el notebook).
ANIO_CORTE_TRAIN_TEST = 2024

# Pesos del score por reglas heurísticas (idénticos al notebook v1/v2)
PESOS_REGLAS = {
    "oferente_unico": 3.0,
    "plazo_corto": 2.0,
    "evaluacion_express": 1.0,
    "publicada_finde": 1.0,
    "monto_redondo_millon": 0.5,
    "ratio_cercano_a_1": 2.5,
    "justificacion_vacia": 0.5,
}
PESO_PROVEEDOR_CAUTIVO = 2.0
PESO_RELACION_INTENSA = 1.5
PESO_COMUNIDAD_ALTO_SCORE = 1.5
PESO_BASES_DIRIGIDAS = 2.0
UMBRAL_SHARE_CAUTIVO = 0.8
UMBRAL_N_PARES_RELACION_INTENSA = 10
UMBRAL_DELTA_SIM_BASES_DIRIGIDAS = 0.2

# Isolation Forest
ISO_CONTAMINATION = 0.05
ISO_N_ESTIMATORS = 200
ISO_RANDOM_STATE = 42

# Embeddings densos
EMBEDDINGS_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDINGS_BATCH_SIZE = 64
