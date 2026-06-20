# Backend — Pipeline de ingesta + scoring ChileCompra

## Arranque

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Al arrancar conviven **dos schedulers independientes**:

### 1. Ingesta (cada 5 horas, sin hora fija)
Revisa `state.json`. Si nunca corrió o ya pasaron 5+ horas desde la última corrida exitosa, ejecuta de inmediato; si no, programa la siguiente corrida para el tiempo exacto que falta.

### 2. Scoring (una vez al día, 03:00 hora de Chile)
Revisa `scoring_state.json`. Corre el modelo de detección de licitaciones dirigidas (`Deteccion_Licitaciones_Dirigidas.py` refactorizado) sobre `licitaciones_clean` y escribe `licitaciones_scored_v3`.

- Hora fija vía `CronTrigger` con zona horaria `America/Santiago` (resuelve correctamente el cambio de horario de verano/invierno).
- Tolerante a fallos: si pasaron ≥20 horas desde la última corrida exitosa (el servidor estuvo apagado durante la ventana nocturna), corre de inmediato al arrancar en vez de esperar a la noche siguiente.
- Usa un lock de archivo (`scoring.lock`) para evitar corridas superpuestas si el proceso se reinicia cerca de las 03:00.
- Reentrena el Isolation Forest completo en cada corrida (split temporal train/test), pero **cachea los embeddings densos por hash de texto** en DuckDB — solo se recalculan para textos nuevos, no para todo el histórico cada vez.
- Si no hay acceso a internet para descargar el modelo de embeddings (`sentence-transformers`), el pipeline degrada automáticamente a solo TF-IDF en vez de fallar completo.

## Endpoints

### Ingesta / datos limpios
- `GET /health` — chequeo simple.
- `GET /api/licitaciones?institucion=&anio=&limit=&offset=` — listado paginado de `licitaciones_clean`.
- `GET /api/licitaciones/{nro_licitacion}` — detalle.
- `GET /api/ingestion/status` — últimas 10 corridas del pipeline de ingesta.

### Scoring (consumidos por el panel React)
- `GET /api/scored/licitaciones?institucion=&anio=&score_min=&limit=&offset=` — ranking ordenado por `score_fraude_v3` descendente.
- `GET /api/scored/licitaciones/{nro_licitacion}` — detalle con todas las features y flags.
- `GET /api/scored/instituciones?min_licitaciones=` — ranking por institución (score promedio, % oferente único, etc.).
- `GET /api/scored/pares?min_adjudicaciones=` — pares institución-proveedor de alta concentración.
- `GET /api/scoring/status` — últimas 10 corridas del pipeline de scoring.

Mientras no haya corrido la primera ingesta o el primer scoring, los endpoints correspondientes responden `503` con un mensaje explícito.

## Pendiente de integrar

1. **Conexión real a MercadoPúblico y a HuggingFace**: en el sandbox de desarrollo no hubo acceso de red a ninguno de los dos dominios, así que ambas piezas se probaron con datos sintéticos / degradando a TF-IDF. Conviene observar la primera corrida real en el servidor con logs abiertos.
2. **CORS**: actualmente abierto (`allow_origins=["*"]`). Restringir a un origin explícito antes de exponer fuera de localhost.
3. **Panel React**: reemplazar los arrays mock (`LICITACIONES`, `INSTITUCIONES`, `PARES`) por `fetch()` a `/api/scored/*`.

## Decisiones de diseño relevantes

- **DuckDB en vez de Postgres/SQLite**: un solo archivo, sin servidor que administrar, y ya es el motor que usa el notebook de detección.
- **APScheduler embebido en el proceso de FastAPI** (no Celery): no se necesita un broker separado para un solo nodo.
- **Dos schedulers, no uno**: ingesta y scoring tienen semánticas de tiempo distintas (intervalo relativo vs. hora fija diaria) y costos computacionales muy distintos (ingesta es liviana, scoring carga modelos pesados). Acoplarlos habría obligado a que cada corrida de ingesta de 5h cargara también el modelo de embeddings, sin necesidad.
- **`CREATE OR REPLACE TABLE` en cada corrida** (tanto `licitaciones_clean` como `licitaciones_scored_v3`): snapshot completo, no append. Evita duplicar histórico.
- **Caché de embeddings por hash de texto, persistente en DuckDB** (`embeddings_cache`): evita recalcular vectores para licitaciones cuyo texto ya fue embebido en una corrida anterior — el ahorro crece con el tiempo a medida que el histórico se estabiliza.
- **El split temporal train/test sigue exactamente al notebook** (train `< 2024`, test `>= 2024`): el modelo nunca ve el futuro durante el entrenamiento, ni siquiera en producción.
