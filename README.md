# Panel de Auditoría ChileCompra

Panel interactivo para detección de licitaciones municipales potencialmente dirigidas en datos de ChileCompra (mercadopublico.cl). Proyecto académico para el Magíster en Ciencia de Datos (Seminario II).

## Stack

| Capa | Tecnología |
|------|------------|
| Backend | FastAPI + DuckDB + APScheduler |
| Frontend | React 19 + Vite |
| Modelo | Reglas heurísticas (60%) + Isolation Forest (40%) |

Sin base de datos externa — DuckDB en un solo archivo local. Sin broker — APScheduler embebido en el proceso FastAPI.

## Cómo funciona

1. **Ingesta masiva** (diaria, 03:00 hora Chile): descarga un `.7z` desde `chc-lic-files.mercadopublico.cl`, extrae, limpia y carga en la tabla `licitaciones_clean`. Descarga un solo archivo (el semestre más reciente) por corrida y se detiene — hoy no hace backfill de años anteriores.
2. **Ingesta incremental por API** (cada hora): consulta la API pública de MercadoPúblico y hace upsert de licitaciones nuevas o con cambio de estado en `licitaciones_clean`, sin tocar el histórico cargado por la vía masiva. Requiere credenciales (`TICKET_API`/`API_KEY`).
3. **Scoring** (semanal, lunes 03:00 hora Chile): lee `licitaciones_clean`, calcula el score de riesgo v3 y escribe en `licitaciones_scored_v3`.
4. **Frontend**: consume los endpoints `/api/scored/*` y visualiza rankings, instituciones, pares institución-proveedor y la red bipartita.

Los tres schedulers son tolerantes a fallos: persisten la última corrida exitosa en `state.json` / `realtime_state.json` / `scoring_state.json` y se recuperan automáticamente si el proceso estuvo apagado.

## Modelo de scoring (v3)

**Tier 1 — flags heurísticas**
`oferente_unico`, `plazo_corto`, `evaluacion_express`, `publicada_finde`, `monto_redondo_millon`, `ratio_cercano_a_1`, `justificacion_vacia`

**Tier 2 — features relacionales**
- Concentración comprador-proveedor (`share_unidad_para_proveedor_t`)
- Comunidades en grafo bipartito (Louvain)
- Similitud de texto entre `DescripcionItem` y `EspecificacionesProveedor` (TF-IDF + embeddings densos cacheados en DuckDB)

**Split temporal**: entrenamiento con datos anteriores a 2024, evaluación con 2024 en adelante.

## Estructura

```
panel-chilecompra/
├── backend/
│   ├── app/
│   │   ├── main.py              FastAPI app + lifespan (arranca los tres schedulers)
│   │   ├── config.py            Constantes y rutas (editar aquí)
│   │   ├── db.py                Conexión DuckDB singleton
│   │   ├── scheduler.py         Scheduler de ingesta masiva
│   │   ├── realtime_scheduler.py  Scheduler de ingesta incremental por API
│   │   ├── pipeline/            downloader · extractor · cleaning · loader · api_client · api_mapping · api_loader
│   │   ├── scoring/             scheduler · pipeline · features · network · text_similarity · model
│   │   └── api/routes.py        Endpoints REST
│   ├── data/                    (ignorado en git — se genera en runtime)
│   └── requirements.txt
└── frontend/
    ├── src/
    │   ├── api/client.js
    │   ├── hooks/useApiData.js
    │   ├── components/          Sidebar · DetallePanel · EstadosCarga
    │   ├── pages/               VistaRanking · VistaInstituciones · VistaPares · VistaRed (grafo D3)
    │   └── utils/format.js
    └── package.json
```

## Instalación y arranque

Se requieren dos terminales. El backend debe estar activo antes de abrir el frontend.

### Backend

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000 --reload-dir app
```

En la primera ejecución, el scheduler de ingesta masiva descarga un `.7z` desde MercadoPúblico automáticamente (el semestre más reciente disponible). El panel muestra `503` hasta que al menos una corrida de ingesta y una de scoring completen.

### Frontend

```powershell
cd frontend
cp .env.example .env   # configura VITE_API_BASE_URL=http://localhost:8000
npm install
npm run dev
```

Accede en `http://localhost:5173`.

## API

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/api/scored/licitaciones` | Ranking paginado con score |
| GET | `/api/scored/licitaciones/{nro}` | Detalle de una licitación |
| GET | `/api/scored/instituciones` | Ranking por institución |
| GET | `/api/scored/pares` | Pares institución-proveedor de alta concentración |
| GET | `/api/scored/red` | Grafo bipartito institución-proveedor (nodos + aristas, con comunidad Louvain) |
| GET | `/api/scoring/status` | Últimas 10 corridas de scoring |
| GET | `/api/ingestion/status` | Últimas 10 corridas de ingesta |

Parámetros de `/api/scored/licitaciones`: `institucion`, `anio`, `score_min`, `solo_activas`, `limit`, `offset`.
Parámetros de `/api/scored/red`: `min_adjudicaciones`, `limit_aristas`.

## Notas

- **Backfill histórico pendiente**: la ingesta masiva solo descarga el `.7z` más reciente por corrida y se detiene, así que hoy `licitaciones_clean` solo tiene datos 2025-2026 aunque `ANIO_INICIO = 2020`. Por eso se quitó el filtro de Año del panel (la API ya lo soporta vía `anio=`, basta con reponer el filtro cuando haya más años cargados).
- **`GET /api/licitaciones?anio=` responde 500**: `FechaPublicacion` en `licitaciones_clean` es texto, no `TIMESTAMP`, y el `EXTRACT(YEAR FROM ...)` de esa ruta falla contra la columna VARCHAR. `/api/scored/licitaciones?anio=` sí funciona porque usa la columna numérica ya derivada `anio_publicacion`.
- **Sin link directo a la ficha de ChileCompra**: la ficha real requiere un token opaco que el sitio genera al buscar, no derivable desde `NroLicitacion` — el panel ofrece copiar el código + un link a la búsqueda general.
- **CORS abierto** (`allow_origins=["*"]`) — restringir antes de exponer fuera de localhost.
- Los embeddings densos (sentence-transformers) requieren PyTorch. Si hay problemas de DLL en Windows, el pipeline degrada automáticamente a TF-IDF.
