# Guía de Integración: API de Mercado Público (ChileCompra)

Documento técnico para reactivar la sincronización automatizada de licitaciones públicas entre el Panel de Auditoría ChileCompra y la API oficial de Mercado Público.

## 1. Requisitos previos

1. **Registro en el Centro de Desarrolladores**: crear cuenta en https://api.mercadopublico.cl y solicitar acceso al servicio de "Licitaciones".
2. **Ticket de autenticación (API Key)**: tras la aprobación, se entrega un `ticket` (string alfanumérico) que debe enviarse como parámetro en cada request. No hay OAuth ni headers de autorización — todo va por query string.
3. **Límites de cuota (rate limits)**:
   - Máximo de requests diarios asociado al ticket (históricamente ~ miles/día; se debe validar el valor vigente en el portal de desarrolladores al momento del registro, ChileCompra lo ajusta sin previo aviso).
   - Respuestas `HTTP 429` o cuerpo con código de error indican cuota agotada — el pipeline debe manejar este caso sin reintentar agresivamente (ver sección 5).
4. **Almacenar el ticket como secreto**: nunca hardcodear en el repo. Agregar `MERCADOPUBLICO_TICKET` a `backend/.env` (no versionado) y cargarlo vía `config.py`, siguiendo el patrón ya usado para otras constantes del proyecto.

## 2. Endpoints clave

Base URL: `https://api.mercadopublico.cl/servicios/v1/publico/`

| Endpoint | Propósito |
|----------|-----------|
| `licitaciones.json` | Búsqueda de licitaciones por fecha, estado o código |
| `licitaciones.json?codigo={codigo}` | Detalle de una licitación específica |
| `ordenesdecompra.json` | Órdenes de compra (complementario, fuera de alcance actual) |

Para el flujo de ingesta diaria, el endpoint relevante es `licitaciones.json` consultado por fecha (`fecha`), que retorna todas las licitaciones publicadas ese día.

## 3. Parámetros de consulta

| Parámetro | Obligatorio | Descripción |
|-----------|--------------|--------------|
| `ticket` | Sí | Token de autenticación entregado al registrarse |
| `fecha` | Uno de fecha/codigo/estado | Formato `ddmmaaaa`, ej. `05062024` |
| `codigo` | Alternativo | Código de licitación específica (ej. `1057-95-LE24`) |
| `estado` | Opcional | `publicada`, `cerrada`, `adjudicada`, `revocada`, `desierta`, etc. |

No permite rango de fechas en un solo request — hay que iterar día por día para reconstruir un histórico.

## 4. Arquitectura del pipeline (ejemplo Python)

```python
import time
import requests

BASE_URL = "https://api.mercadopublico.cl/servicios/v1/publico/licitaciones.json"
TICKET = config.MERCADOPUBLICO_TICKET  # desde .env, nunca hardcodeado

def fetch_licitaciones_por_fecha(fecha: str) -> list[dict]:
    """fecha en formato ddmmaaaa"""
    resp = requests.get(BASE_URL, params={"ticket": TICKET, "fecha": fecha}, timeout=30)

    if resp.status_code == 429:
        raise RateLimitError(f"Cuota agotada consultando fecha={fecha}")
    resp.raise_for_status()

    data = resp.json()
    return data.get("Listado", [])  # la API no pagina este endpoint por fecha,
                                     # retorna el listado completo del día


def sincronizar_dia(fecha: str) -> int:
    licitaciones = fetch_licitaciones_por_fecha(fecha)
    if not licitaciones:
        return 0
    # reutilizar loader.py existente: mismo contrato de columnas que el ETL de .7z
    cleaning.procesar_e_insertar(licitaciones, fuente="api")
    return len(licitaciones)
```

La API de Mercado Público **no pagina** `licitaciones.json` por fecha (retorna el día completo en un solo JSON), a diferencia de la descarga masiva `.7z` actual. Esto simplifica el pipeline: no se necesita lógica de `offset`/`page`.

## 5. Estrategia de actualización

Dado que el proyecto ya tiene un `APScheduler` embebido (`backend/app/scheduler.py`), la opción más simple es **agregar un segundo job al scheduler existente**, no un cron externo ni un servicio nuevo:

- **Frecuencia**: diaria, consultando `fecha = hoy` (y opcionalmente `ayer` para capturar publicaciones tardías). No reemplaza la ingesta `.7z` — la complementa para tener datos del día sin esperar el próximo paquete `.7z` de ChileCompra.
- **Manejo de errores**:
  - Reintentos con backoff exponencial (2-3 intentos) solo ante error de red/timeout, no ante `429`.
  - Ante `429`: registrar en `state.json` y reintentar en el próximo ciclo, sin bloquear el proceso.
  - Persistir último `fecha` sincronizada exitosamente (mismo patrón fault-tolerant que ya usa `state.json`), para poder recuperar el rango si el proceso estuvo caído varios días.
- **Idempotencia**: usar el mismo criterio de dedup que `cleaning.py` (por `CodigoExterno`/número de licitación) para que reinsertar un día ya sincronizado no duplique filas.
- **Backfill histórico**: para poblar años anteriores, iterar `fecha` día por día respetando el rate limit (agregar `time.sleep` entre requests si el volumen lo requiere) — se ejecuta una sola vez, no como parte del scheduler recurrente.

## Resumen de cambios en el proyecto

- Nuevo módulo `backend/app/pipeline/api_client.py` con la función `fetch_licitaciones_por_fecha`.
- Nuevo job en `scheduler.py` (o uno separado si se prefiere aislar el fallo), reutilizando `cleaning.py` y `loader.py` ya existentes.
- Nueva variable de entorno `MERCADOPUBLICO_TICKET` en `config.py` y `.env.example`.
