export function scoreColor(score) {
  if (score >= 80) return '#E24B4A'
  if (score >= 60) return '#BA7517'
  return '#1D9E75'
}

export function formatoMonto(monto) {
  if (monto == null || Number.isNaN(monto)) return '—'
  return `$${(monto / 1_000_000).toFixed(1)}M`
}

export function formatoMontoCompleto(monto) {
  if (monto == null || Number.isNaN(monto)) return '—'
  return monto.toLocaleString('es-CL', { style: 'currency', currency: 'CLP', maximumFractionDigits: 0 })
}

export function formatoFecha(fechaIso) {
  if (!fechaIso) return '—'
  try {
    return new Date(fechaIso).toLocaleDateString('es-CL', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
    })
  } catch {
    return fechaIso
  }
}

export function truncar(texto, max) {
  if (!texto) return ''
  return texto.length > max ? `${texto.slice(0, max)}…` : texto
}
