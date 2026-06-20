export function EstadoCargando({ mensaje = 'Cargando datos...' }) {
  return (
    <div className="estado-card">
      <i className="ti ti-loader-2 spin" aria-hidden="true" />
      <p>{mensaje}</p>
    </div>
  )
}

export function EstadoError({ error, onRetry }) {
  const esScoringPendiente = error?.status === 503

  return (
    <div className="estado-card estado-error">
      <i
        className={`ti ${esScoringPendiente ? 'ti-clock-hour-3' : 'ti-plug-connected-x'}`}
        aria-hidden="true"
      />
      <p className="estado-titulo">
        {esScoringPendiente ? 'El scoring aún no se ha calculado' : 'No se pudo cargar la información'}
      </p>
      <p className="estado-detalle">{error?.message}</p>
      {!esScoringPendiente && (
        <p className="estado-hint">
          Verifica que el backend esté corriendo (<code>uvicorn app.main:app</code>) y que la
          URL configurada en <code>VITE_API_BASE_URL</code> sea correcta.
        </p>
      )}
      {esScoringPendiente && (
        <p className="estado-hint">
          El pipeline de scoring corre todos los días a las 03:00 (hora de Chile). Si recién
          levantaste el backend, debería ejecutarse de inmediato — espera unos minutos y
          reintenta.
        </p>
      )}
      <button className="btn" onClick={onRetry}>
        <i className="ti ti-refresh" aria-hidden="true" /> Reintentar
      </button>
    </div>
  )
}

export function EstadoVacio({ mensaje = 'No hay resultados para los filtros actuales.' }) {
  return (
    <div className="estado-card">
      <i className="ti ti-mood-empty" aria-hidden="true" />
      <p>{mensaje}</p>
    </div>
  )
}
