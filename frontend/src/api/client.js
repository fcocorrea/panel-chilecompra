const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

class ApiError extends Error {
  constructor(message, status, payload) {
    super(message)
    this.status = status
    this.payload = payload
  }
}

async function request(path, params = {}) {
  const url = new URL(`${API_BASE_URL}${path}`)
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      url.searchParams.set(key, value)
    }
  })

  let response
  try {
    response = await fetch(url.toString())
  } catch (networkError) {
    throw new ApiError(
      'No se pudo conectar con el backend. ¿Está corriendo en ' + API_BASE_URL + '?',
      0,
      null,
    )
  }

  if (!response.ok) {
    let payload = null
    try {
      payload = await response.json()
    } catch {
      // el cuerpo no era JSON, se ignora
    }
    throw new ApiError(
      payload?.detail || `Error ${response.status} al consultar ${path}`,
      response.status,
      payload,
    )
  }

  return response.json()
}

export const api = {
  listarLicitaciones: ({ institucion, anio, scoreMin, soloActivas = true, limit = 50, offset = 0 } = {}) =>
    request('/api/scored/licitaciones', {
      institucion,
      anio,
      score_min: scoreMin,
      solo_activas: soloActivas,
      limit,
      offset,
    }),

  detalleLicitacion: (nroLicitacion) =>
    request(`/api/scored/licitaciones/${encodeURIComponent(nroLicitacion)}`),

  rankingInstituciones: ({ minLicitaciones = 10 } = {}) =>
    request('/api/scored/instituciones', { min_licitaciones: minLicitaciones }),

  rankingPares: ({ minAdjudicaciones = 5 } = {}) =>
    request('/api/scored/pares', { min_adjudicaciones: minAdjudicaciones }),

  redInstitucionProveedor: ({ minAdjudicaciones = 3, limitAristas = 200 } = {}) =>
    request('/api/scored/red', { min_adjudicaciones: minAdjudicaciones, limit_aristas: limitAristas }),

  estadoScoring: () => request('/api/scoring/status'),

  estadoIngesta: () => request('/api/ingestion/status'),
}

export { ApiError, API_BASE_URL }
