import { api } from '../api/client'
import { useApiData } from '../hooks/useApiData'
import { EstadoCargando, EstadoError, EstadoVacio } from '../components/EstadosCarga'
import { scoreColor } from '../utils/format'

export default function VistaInstituciones() {
  const { data, loading, error, refetch } = useApiData(() => api.rankingInstituciones(), [])

  if (loading) return <EstadoCargando mensaje="Cargando ranking de instituciones..." />
  if (error) return <EstadoError error={error} onRetry={refetch} />

  const instituciones = data?.resultados ?? []

  if (instituciones.length === 0) {
    return <EstadoVacio mensaje="No hay instituciones con suficientes licitaciones para mostrar." />
  }

  return (
    <div className="table-card">
      <div className="table-header">
        <i className="ti ti-building-bank" aria-hidden="true" style={{ fontSize: 16, color: 'var(--text-hint)' }} />
        <span>Instituciones por score promedio</span>
      </div>
      <table>
        <thead>
          <tr>
            <th style={{ width: 220 }}>Institución</th>
            <th style={{ width: 70, textAlign: 'right' }}>N° lic.</th>
            <th style={{ width: 70, textAlign: 'right' }}>Alto riesgo</th>
            <th style={{ width: 80, textAlign: 'right' }}>% of. único</th>
            <th style={{ width: 80, textAlign: 'right' }}>% plazo corto</th>
            <th style={{ width: 120 }}>Score promedio</th>
          </tr>
        </thead>
        <tbody>
          {instituciones.map((inst) => {
            const sc = scoreColor(inst.score_avg)
            return (
              <tr key={inst.inst}>
                <td>{(inst.inst || '').replace('Municipalidad de ', '')}</td>
                <td style={{ textAlign: 'right', fontFamily: 'var(--font-mono)' }}>
                  {inst.n_lic.toLocaleString('es-CL')}
                </td>
                <td style={{ textAlign: 'right', fontWeight: 500, color: 'var(--danger)' }}>{inst.n_alto}</td>
                <td
                  style={{
                    textAlign: 'right',
                    color: inst.pct_oferente > 60 ? 'var(--danger)' : 'var(--text-primary)',
                  }}
                >
                  {inst.pct_oferente}%
                </td>
                <td style={{ textAlign: 'right' }}>{inst.pct_plazo}%</td>
                <td>
                  <div className="score-bar-wrap">
                    <div className="score-bar">
                      <div className="score-fill" style={{ width: `${inst.score_avg}%`, background: sc }} />
                    </div>
                    <span className="score-num" style={{ color: sc }}>
                      {inst.score_avg.toFixed(1)}
                    </span>
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
