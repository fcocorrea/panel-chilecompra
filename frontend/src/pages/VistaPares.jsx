import { api } from '../api/client'
import { useApiData } from '../hooks/useApiData'
import { EstadoCargando, EstadoError, EstadoVacio } from '../components/EstadosCarga'
import { scoreColor, truncar } from '../utils/format'

export default function VistaPares() {
  const { data, loading, error, refetch } = useApiData(() => api.rankingPares(), [])

  if (loading) return <EstadoCargando mensaje="Cargando pares institución-proveedor..." />
  if (error) return <EstadoError error={error} onRetry={refetch} />

  const pares = data?.resultados ?? []

  if (pares.length === 0) {
    return <EstadoVacio mensaje="No hay pares con suficientes adjudicaciones para mostrar." />
  }

  return (
    <div className="table-card">
      <div className="table-header">
        <i className="ti ti-arrows-exchange" aria-hidden="true" style={{ fontSize: 16, color: 'var(--text-hint)' }} />
        <span>Pares institución — proveedor de alta concentración</span>
      </div>
      <table>
        <thead>
          <tr>
            <th style={{ width: 175 }}>Institución</th>
            <th style={{ width: 165 }}>Proveedor adjudicado</th>
            <th style={{ width: 60, textAlign: 'right' }}>N° adj.</th>
            <th style={{ width: 90, textAlign: 'right' }}>Monto total</th>
            <th style={{ width: 70, textAlign: 'right' }}>Share prov.</th>
            <th style={{ width: 100 }}>Score</th>
          </tr>
        </thead>
        <tbody>
          {pares.map((p, i) => {
            const sc = scoreColor(p.score)
            const shareAlto = p.share > 0.8
            return (
              <tr key={`${p.inst}-${p.prov}-${i}`}>
                <td title={p.inst}>{(p.inst || '').replace('Municipalidad de ', '')}</td>
                <td title={p.prov} style={{ color: 'var(--text-secondary)' }}>
                  {truncar(p.prov, 24)}
                </td>
                <td style={{ textAlign: 'right', fontWeight: 500, fontFamily: 'var(--font-mono)' }}>{p.n_adj}</td>
                <td style={{ textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
                  {(p.monto / 1e9).toFixed(2)}B
                </td>
                <td style={{ textAlign: 'right' }}>
                  <span className={`badge ${shareAlto ? 'badge-red' : 'badge-amber'}`}>
                    {Math.round((p.share || 0) * 100)}%
                  </span>
                </td>
                <td>
                  <div className="score-bar-wrap">
                    <div className="score-bar">
                      <div className="score-fill" style={{ width: `${p.score}%`, background: sc }} />
                    </div>
                    <span className="score-num" style={{ color: sc }}>
                      {p.score.toFixed(1)}
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
