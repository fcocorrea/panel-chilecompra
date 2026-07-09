# Backend — Pipeline de ingesta + scoring ChileCompra

## Arranque

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Al arrancar conviven **tres schedulers independientes** (`app/main.py` lifespan):

### 1. Ingesta masiva (`.7z`, una vez al día, 03:00 hora de Chile)
Revisa `state.json`. Descarga y carga en `licitaciones_clean` el histórico desde `ANIO_INICIO` (2020).

- Hora fija vía `CronTrigger` (`America/Santiago`), tolerante a fallos (`INGESTION_MIN_HOURS_BETWEEN_RUNS`): si el proceso estuvo apagado durante la ventana, corre apenas vuelve a levantar.
- **Limitación conocida**: `descargar_licitaciones()` (`pipeline/downloader.py`) descarga **un solo archivo `.7z`** (el semestre más reciente disponible) y se detiene — quedó así deliberadamente para pruebas locales (ver comentario `ponytail:` en el código). Como resultado, hoy `licitaciones_clean` solo tiene datos de 2025-2026; para poblar años anteriores hay que ampliar el loop para que seguir bajando semestres más antiguos en corridas sucesivas, respetando `MAX_DOWNLOAD_BYTES_PER_RUN` por corrida.

### 2. Ingesta incremental vía API (`pipeline/api_loader.py`, cada hora)
Revisa `realtime_state.json`. A diferencia de la masiva (snapshot completo por archivo), hace **upsert** por `NroLicitacion`: solo consulta el detalle de licitaciones nuevas o cuyo estado cambió desde la corrida anterior, y actualiza el estado de las que dejaron de estar "Publicada". Requiere credenciales (`TICKET_API`/`API_KEY` en `backend/.env`) — si no están configuradas, el scheduler simplemente no se registra en vez de fallar cada hora.

### 3. Scoring (una vez a la semana, lunes 03:00 hora de Chile)
Revisa `scoring_state.json`. Corre el modelo de detección de licitaciones dirigidas (`Deteccion_Licitaciones_Dirigidas.py` refactorizado) sobre `licitaciones_clean` y escribe `licitaciones_scored_v3`.

- Hora fija vía `CronTrigger` con zona horaria `America/Santiago` (resuelve correctamente el cambio de horario de verano/invierno).
- Tolerante a fallos: si pasó más de una semana (con margen) desde la última corrida exitosa, corre de inmediato al arrancar en vez de esperar al próximo lunes.
- Usa un lock de archivo (`scoring.lock`) para evitar corridas superpuestas si el proceso se reinicia cerca de las 03:00.
- Reentrena el Isolation Forest completo en cada corrida (split temporal train/test, corte en `ANIO_CORTE_TRAIN_TEST`), pero **cachea los embeddings densos por hash de texto** en DuckDB — solo se recalculan para textos nuevos, no para todo el histórico cada vez.
- Si no hay acceso a internet para descargar el modelo de embeddings (`sentence-transformers`), el pipeline degrada automáticamente a solo TF-IDF en vez de fallar completo.
- Las fechas (`FechaPublicacion`, etc.) llegan como texto ISO (`%Y-%m-%d %H:%M:%S`) desde `licitaciones_clean`; `parsear_fechas()` en `scoring/features.py` debe usar ese formato exacto — un mismatch aquí falla en silencio (`strict=False`) y deja `anio_publicacion` y las features derivadas de fecha en `null` para toda la tabla sin ningún error visible.

## Endpoints

### Ingesta / datos limpios
- `GET /health` — chequeo simple.
- `GET /api/licitaciones?institucion=&anio=&limit=&offset=` — listado paginado de `licitaciones_clean`.
- `GET /api/licitaciones/{nro_licitacion}` — detalle.
- `GET /api/ingestion/status` — últimas 10 corridas del pipeline de ingesta.

### Scoring (consumidos por el panel React)
- `GET /api/scored/licitaciones?institucion=&anio=&score_min=&solo_activas=&limit=&offset=` — ranking ordenado por `score_fraude_v3` descendente.
- `GET /api/scored/licitaciones/{nro_licitacion}` — detalle con todas las features y flags.
- `GET /api/scored/instituciones?min_licitaciones=` — ranking por institución (score promedio, % oferente único, etc.).
- `GET /api/scored/pares?min_adjudicaciones=` — pares institución-proveedor de alta concentración.
- `GET /api/scored/red?min_adjudicaciones=&limit_aristas=` — nodos y aristas del grafo bipartito institución↔proveedor (agregado desde `licitaciones_scored_v3`, incluye `comunidad_id` de Louvain), consumido por la vista de red del panel.
- `GET /api/scoring/status` — últimas 10 corridas del pipeline de scoring.

Mientras no haya corrido la primera ingesta o el primer scoring, los endpoints correspondientes responden `503` con un mensaje explícito.

**Bug conocido**: `GET /api/licitaciones?anio=` responde `500` porque `FechaPublicacion` en `licitaciones_clean` es texto (nunca se convierte a `TIMESTAMP` en `cleaning.py`), y `EXTRACT(YEAR FROM FechaPublicacion)` en la query falla contra una columna VARCHAR. El endpoint equivalente `GET /api/scored/licitaciones?anio=` sí funciona porque usa la columna ya derivada `anio_publicacion` (numérica) de `licitaciones_scored_v3`.

## Pendiente de integrar

1. **Backfill histórico de la ingesta masiva**: ver limitación del downloader arriba — solo 2025/2026 están cargados hoy.
2. **Fix del filtro de año en `/api/licitaciones`**: ver bug conocido arriba.
3. **CORS**: actualmente abierto (`allow_origins=["*"]`). Restringir a un origin explícito antes de exponer fuera de localhost.
4. **Primera corrida real prolongada en producción**: la ingesta masiva y el scoring con embeddings densos se probaron contra MercadoPúblico y HuggingFace reales, pero conviene observar varias corridas seguidas del scheduler 24/7 con logs abiertos antes de confiar en la tolerancia a fallos sin supervisión.

## Decisiones de diseño relevantes

- **DuckDB en vez de Postgres/SQLite**: un solo archivo, sin servidor que administrar, y ya es el motor que usa el notebook de detección.
- **APScheduler embebido en el proceso de FastAPI** (no Celery): no se necesita un broker separado para un solo nodo.
- **Tres schedulers, no uno**: ingesta masiva, ingesta incremental y scoring tienen semánticas de tiempo distintas (diaria vs. horaria vs. semanal) y costos computacionales muy distintos (scoring carga modelos pesados, el resto es liviano). Acoplarlos habría obligado a que cada corrida liviana cargara también el modelo de embeddings, sin necesidad.
- **`CREATE OR REPLACE TABLE` en la ingesta masiva y en el scoring** (`licitaciones_clean` completo y `licitaciones_scored_v3`): snapshot completo, no append. Evita duplicar histórico.
- **`DELETE` + `INSERT` (upsert) en la ingesta incremental**, no `CREATE OR REPLACE`: a diferencia de la masiva, esta corre cada hora y solo trae un puñado de licitaciones nuevas o con cambio de estado — reemplazar toda la tabla en cada corrida borraría el histórico cargado por la vía masiva sin necesidad.
- **Caché de embeddings por hash de texto, persistente en DuckDB** (`embeddings_cache`): evita recalcular vectores para licitaciones cuyo texto ya fue embebido en una corrida anterior — el ahorro crece con el tiempo a medida que el histórico se estabiliza.
- **El split temporal train/test sigue exactamente al notebook** (train `< 2024`, test `>= 2024`): el modelo nunca ve el futuro durante el entrenamiento, ni siquiera en producción.
