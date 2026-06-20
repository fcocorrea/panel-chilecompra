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

1. **Ingesta** (cada 5h): descarga `.7z` desde `chc-lic-files.mercadopublico.cl`, extrae, limpia y carga en la tabla `licitaciones_clean`.
2. **Scoring** (diario 03:00 hora Chile): lee `licitaciones_clean`, calcula el score de riesgo v3 y escribe en `licitaciones_scored_v3`.
3. **Frontend**: consume los endpoints `/api/scored/*` y visualiza rankings, instituciones y pares institución-proveedor.

Ambos schedulers son tolerantes a fallos: persisten la última corrida exitosa en `state.json` / `scoring_state.json` y se recuperan automáticamente si el proceso estuvo apagado.

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
│   │   ├── main.py              FastAPI app + lifespan
│   │   ├── config.py            Constantes y rutas (editar aquí)
│   │   ├── db.py                Conexión DuckDB singleton
│   │   ├── scheduler.py         Scheduler de ingesta
│   │   ├── pipeline/            downloader · extractor · cleaning · loader
│   │   ├── scoring/             scheduler · pipeline · features · network · text_similarity · model
│   │   └── api/routes.py        Endpoints REST
│   ├── data/                    (ignorado en git — se genera en runtime)
│   └── requirements.txt
└── frontend/
    ├── src/
    │   ├── api/client.js
    │   ├── hooks/useApiData.js
    │   ├── components/          Sidebar · DetallePanel · EstadosCarga
    │   ├── pages/               VistaRanking · VistaInstituciones · VistaPares · VistaRed
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

En la primera ejecución, el scheduler de ingesta descarga datos desde MercadoPúblico automáticamente (~23 MB el archivo más reciente). El panel muestra `503` hasta que al menos una corrida de ingesta y una de scoring completen.

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
| GET | `/api/scoring/status` | Últimas 10 corridas de scoring |
| GET | `/api/ingestion/status` | Últimas 10 corridas de ingesta |

Parámetros de `/api/scored/licitaciones`: `institucion`, `anio`, `score_min`, `limit`, `offset`.

## Notas

- **VistaRed** es un placeholder — el endpoint de grafo y la visualización D3 están pendientes.
- **CORS abierto** (`allow_origins=["*"]`) — restringir antes de exponer fuera de localhost.
- Los embeddings densos (sentence-transformers) requieren PyTorch. Si hay problemas de DLL en Windows, el pipeline degrada automáticamente a TF-IDF.
