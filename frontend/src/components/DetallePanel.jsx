import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { EstadoCargando, EstadoError } from './EstadosCarga'
import { scoreColor, formatoMonto, formatoFecha } from '../utils/format'

const FLAGS_DEFINICION = [
  { key: 'oferente_unico', label: 'Oferente único' },
  { key: 'plazo_corto', label: 'Plazo corto' },
  { key: 'evaluacion_express', label: 'Eval. express' },
  { key: 'publicada_finde', label: 'Pub. finde' },
  { key: 'monto_redondo_millon', label: 'Monto redondo' },
  { key: 'ratio_cercano_a_1', label: 'Ratio ≈ 1' },
  { key: 'justificacion_vacia', label: 'Just. vacía' },
]

export default function DetallePanel({ nroLicitacion, onClose }) {
  const [licitacion, setLicitacion] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const cargarDetalle = () => {
    if (!nroLicitacion) return
    setLoading(true)
    setError(null)
    api
      .detalleLicitacion(nroLicitacion)
      .then(setLicitacion)
      .catch(setError)
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    cargarDetalle()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nroLicitacion])

  if (!nroLicitacion) {
    return (
      <div className="detail-panel">
        <div className="empty-detail">
          <i className="ti ti-file-search" aria-hidden="true" />
          <p>Selecciona una licitación</p>
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="detail-panel">
        <EstadoCargando mensaje="Cargando detalle..." />
      </div>
    )
  }

  if (error) {
    return (
      <div className="detail-panel">
        <EstadoError error={error} onRetry={cargarDetalle} />
      </div>
    )
  }

  const lic = licitacion
  const score = lic.score_fraude_v3 ?? 0
  const sc = scoreColor(score)
  const flagsActivos = FLAGS_DEFINICION.filter((f) => lic[f.key])
  const pctFlags = Math.round((flagsActivos.length / FLAGS_DEFINICION.length) * 100)

  const scoreReglas = lic.score_reglas ?? 0
  const scoreIso = lic.iso_score_temporal ?? 0
  // Aproximación visual de la contribución de cada componente (60/40), igual
  // que el mock — el cálculo exacto vive en el backend, aquí solo se ilustra.
  const aporteReglas = Math.min(score * 0.6, 100)
  const aporteIso = Math.min(score * 0.4, 100)

  return (
    <div className="detail-panel">
      <div className="detail-header">
        <div>
          <div className="detail-id">{lic.NroLicitacion}</div>
          <div className="detail-inst">{lic.Institucion}</div>
          <div className="detail-prov">{lic.proveedor_adjudicado || 'Sin proveedor adjudicado'}</div>
        </div>
        <div className="detail-close" onClick={onClose}>
          <i className="ti ti-x" aria-hidden="true" />
        </div>
      </div>

      <div className="detail-score-block">
        <div className="detail-score-label">Score de fraude v3</div>
        <div className="detail-score-big" style={{ color: sc }}>
          {score.toFixed(1)}
        </div>
        <div className="detail-score-bar">
          <div className="detail-score-fill" style={{ width: `${score}%`, background: sc }} />
        </div>
        <div className="detail-score-sub">
          {flagsActivos.length} de {FLAGS_DEFINICION.length} flags activos · {pctFlags}%
        </div>
      </div>

      <div className="detail-section">
        <div className="detail-section-title">Datos clave</div>
        <DetailRow label="Monto adjudicado" value={formatoMonto(lic.monto_adjudicado)} />
        <DetailRow
          label="N° oferentes"
          value={lic.n_oferentes}
          destacar={lic.n_oferentes === 1}
        />
        <DetailRow
          label="Días pub. → cierre"
          value={lic.dias_pub_cierre != null ? `${Math.round(lic.dias_pub_cierre)} días` : '—'}
          destacar={lic.dias_pub_cierre != null && lic.dias_pub_cierre < 5}
        />
        <DetailRow
          label="Ratio adj./estimado"
          value={lic.ratio_adj_estimado != null ? lic.ratio_adj_estimado.toFixed(2) : '—'}
          destacar={lic.ratio_adj_estimado != null && lic.ratio_adj_estimado >= 0.98}
        />
        <DetailRow
          label="Share prov. en unidad"
          value={
            lic.share_unidad_para_proveedor_t != null
              ? `${Math.round(lic.share_unidad_para_proveedor_t * 100)}%`
              : '—'
          }
          destacar={lic.share_unidad_para_proveedor_t > 0.8}
          advertencia={lic.share_unidad_para_proveedor_t > 0.6 && lic.share_unidad_para_proveedor_t <= 0.8}
        />
        <DetailRow label="N° adjudicaciones previas (par)" value={lic.n_pares_t ?? 0} />
        <DetailRow
          label="Delta similitud (TF-IDF)"
          value={lic.delta_sim_tfidf != null ? lic.delta_sim_tfidf.toFixed(2) : '—'}
          destacar={lic.delta_sim_tfidf != null && lic.delta_sim_tfidf > 0.3}
        />
        <DetailRow label="Fecha publicación" value={formatoFecha(lic.FechaPublicacion)} />
      </div>

      <div className="detail-section">
        <div className="detail-section-title">Componentes del score</div>
        <div className="score-components">
          <ScoreComponente label="Reglas heurísticas (60%)" valor={aporteReglas} color={sc} />
          <ScoreComponente label="Isolation Forest (40%)" valor={aporteIso} color="var(--info)" />
        </div>
      </div>

      <div className="detail-section">
        <div className="detail-section-title">Flags activos ({flagsActivos.length})</div>
        <div className="flags-grid">
          {FLAGS_DEFINICION.map((f) => (
            <div key={f.key} className={`flag-item ${lic[f.key] ? 'on' : 'off'}`}>
              <div className="flag-dot" />
              <span>{f.label}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="detail-section detail-section-last">
        <a
          className="btn primary full-width"
          href={`https://www.mercadopublico.cl/Procurement/Modules/RFB/StepsProcessSearch.aspx?qs=${lic.NroLicitacion}`}
          target="_blank"
          rel="noreferrer"
        >
          <i className="ti ti-external-link" aria-hidden="true" /> Ver en ChileCompra
        </a>
      </div>
    </div>
  )
}

function DetailRow({ label, value, destacar, advertencia }) {
  let color = 'inherit'
  if (destacar) color = 'var(--danger)'
  else if (advertencia) color = 'var(--warning)'

  return (
    <div className="detail-row">
      <span className="label">{label}</span>
      <span className="value" style={{ color }}>
        {value ?? '—'}
      </span>
    </div>
  )
}

function ScoreComponente({ label, valor, color }) {
  return (
    <div className="score-comp-item">
      <div className="score-comp-label">
        <span className="name">{label}</span>
        <span className="val" style={{ color }}>
          {valor.toFixed(1)}
        </span>
      </div>
      <div className="score-comp-bar">
        <div className="score-comp-fill" style={{ width: `${Math.min(valor, 100)}%`, background: color }} />
      </div>
    </div>
  )
}
