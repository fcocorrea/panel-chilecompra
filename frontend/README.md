# Panel de auditoría ChileCompra — Frontend

Panel React (Vite) que consume la API de scoring del backend FastAPI. Reemplaza al
prototipo con datos mock que se probó primero como artifact en el chat.

## Arranque

Necesitas el backend corriendo primero (ver `backend/README.md`). Luego:

```bash
cd frontend
cp .env.example .env   # ajusta VITE_API_BASE_URL si el backend no está en localhost:8000
npm install
npm run dev
```

Por defecto el panel corre en `http://localhost:5173` y el backend en
`http://localhost:8000`. Si cambias el puerto del backend, actualiza
`VITE_API_BASE_URL` en `.env`.

## Estructura

```
src/
├── api/client.js          fetch tipado a /api/scored/*, maneja errores y 503
├── hooks/useApiData.js     hook reutilizable: loading / error / data / refetch
├── components/
│   ├── Sidebar.jsx          navegación entre vistas
│   ├── DetallePanel.jsx     panel lateral con desglose de score y flags
│   └── EstadosCarga.jsx     loading / error / vacío, compartidos por todas las vistas
├── pages/
│   ├── VistaRanking.jsx      tabla principal con filtros y paginación
│   ├── VistaInstituciones.jsx
│   ├── VistaPares.jsx
│   └── VistaRed.jsx          placeholder — el grafo D3 real queda pendiente
├── utils/format.js          color de score, montos, fechas
└── styles/                   CSS global + por componente (paleta light institucional)
```

## Qué falta

- **Vista de Red**: el backend ya calcula el grafo bipartito y las comunidades
  (Louvain), pero la visualización interactiva con D3 todavía no está construida
  en este panel — hoy es un placeholder honesto.
- **CORS del backend**: mientras esté en `allow_origins=["*"]` esto funciona desde
  cualquier origen. Antes de exponer el backend fuera de tu máquina, restringe el
  origin en `backend/app/main.py` al dominio real donde viva este frontend.
