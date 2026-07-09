import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { EstadoCargando, EstadoError, EstadoVacio } from '../components/EstadosCarga'
import { scoreColor, formatoMonto, truncar } from '../utils/format'

const PAGE_SIZE = 8

const FLAGS_BADGE = [
  { key: 'oferente_unico', label: 'Oferente único' },
  { key: 'plazo_corto', label: 'Plazo corto' },
  { key: 'flag_proveedor_cautivo', label: 'Proveedor cautivo' },
  { key: 'flag_bases_dirigidas', label: 'Bases dirigidas' },
  { key: 'ratio_cercano_a_1', label: 'Ratio ≈ 1' },
]

export default function VistaRanking({ onSelectLicitacion, selectedId, onTotalChange }) {
  const [filtroInst, setFiltroInst] = useState('')
  const [filtroScore, setFiltroScore] = useState(0)
  const [soloActivas, setSoloActivas] = useState(true)
  const [page, setPage] = useState(0)
  const [sortAsc, setSortAsc] = useState(false)

  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const cargar = () => {
    setLoading(true)
    setError(null)
    api
      .listarLicitaciones({
        institucion: filtroInst || undefined,
        scoreMin: filtroScore || undefined,
        soloActivas,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      })
      .then((resultado) => {
        setData(resultado)
        onTotalChange?.(resultado.total)
      })
      .catch(setError)
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    cargar()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtroInst, filtroScore, soloActivas, page])

  useEffect(() => {
    setPage(0)
  }, [filtroInst, filtroScore, soloActivas])

  if (loading && !data) return <EstadoCargando mensaje="Cargando licitaciones..." />
  if (error) return <EstadoError error={error} onRetry={cargar} />

  const licitaciones = data?.resultados ?? []
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const ordenadas = sortAsc ? [...licitaciones].reverse() : licitaciones

  return (
    <>
      <KpiRow licitaciones={licitaciones} total={total} />

      <div className="filters-bar">
        <i className="ti ti-filter" aria-hidden="true" style={{ fontSize: 16, color: 'var(--text-hint)' }} />
        <span className="filter-label">Institución</span>
        <input
          className="filter-input"
          type="text"
          placeholder="Buscar municipio..."
          value={filtroInst}
          onChange={(e) => setFiltroInst(e.target.value)}
        />
        <span className="filter-label">Score mín.</span>
        <select className="filter-select" value={filtroScore} onChange={(e) => setFiltroScore(Number(e.target.value))}>
          <option value={0}>Todos</option>
          <option value={50}>≥ 50</option>
          <option value={70}>≥ 70</option>
          <option value={85}>≥ 85</option>
        </select>
        <label className="filter-label" style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={soloActivas}
            onChange={(e) => setSoloActivas(e.target.checked)}
          />
          Solo en proceso
        </label>
        <div className="ml-auto result-count">
          {total} resultado{total !== 1 ? 's' : ''}
        </div>
      </div>

      <div className="table-card">
        <div className="table-header">
          <i className="ti ti-table" aria-hidden="true" style={{ fontSize: 16, color: 'var(--text-hint)' }} />
          <span>Licitaciones</span>
        </div>

        {licitaciones.length === 0 ? (
          <EstadoVacio mensaje="No hay licitaciones que calcen con estos filtros." />
        ) : (
          <>
            <table>
              <thead>
                <tr>
                  <th style={{ width: 110 }}>N° licitación</th>
                  <th style={{ width: 180 }}>Institución</th>
                  <th style={{ width: 130 }}>Proveedor adj.</th>
                  <th style={{ width: 80, textAlign: 'right' }}>Monto</th>
                  <th style={{ width: 50, textAlign: 'right' }}>Ofer.</th>
                  <th style={{ width: 140 }}>Flags</th>
                  <th style={{ width: 100 }} onClick={() => setSortAsc((s) => !s)}>
                    Score {sortAsc ? '↑' : '↓'}
                  </th>
                </tr>
              </thead>
              <tbody>
                {ordenadas.map((lic, i) => (
                  <FilaLicitacion
                    key={`${lic.NroLicitacion}-${i}`}
                    lic={lic}
                    selected={lic.NroLicitacion === selectedId}
                    onClick={() => onSelectLicitacion(lic.NroLicitacion)}
                  />
                ))}
              </tbody>
            </table>
            <Paginacion page={page} totalPages={totalPages} onChange={setPage} />
          </>
        )}
      </div>
    </>
  )
}

function KpiRow({ licitaciones, total }) {
  const alto = licitaciones.filter((l) => l.score_fraude_v3 >= 70).length
  const oferenteUnico = licitaciones.filter((l) => l.oferente_unico).length
  const cautivo = licitaciones.filter((l) => l.flag_proveedor_cautivo > 0).length

  return (
    <div className="kpi-row">
      <Kpi label="Total licitaciones" value={total.toLocaleString('es-CL')} hint="página actual" />
      <Kpi
        label="Score alto (>70)"
        value={alto}
        color="var(--danger)"
        hint={`${licitaciones.length ? Math.round((alto / licitaciones.length) * 100) : 0}% de esta página`}
      />
      <Kpi
        label="Oferente único"
        value={oferenteUnico}
        color="var(--warning)"
        hint={`${licitaciones.length ? Math.round((oferenteUnico / licitaciones.length) * 100) : 0}% de esta página`}
      />
      <Kpi label="Prov. cautivos" value={cautivo} color="var(--info)" hint="share > 0.8" />
    </div>
  )
}

function Kpi({ label, value, color, hint }) {
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value" style={{ color: color || 'inherit' }}>
        {value}
      </div>
      <div className="kpi-delta">{hint}</div>
    </div>
  )
}

function FilaLicitacion({ lic, selected, onClick }) {
  const score = lic.score_fraude_v3 ?? 0
  const sc = scoreColor(score)
  const activos = FLAGS_BADGE.filter((f) => lic[f.key])

  return (
    <tr className={selected ? 'selected' : ''} onClick={onClick}>
      <td style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-secondary)' }}>
        {lic.NroLicitacion}
      </td>
      <td title={lic.Institucion}>{(lic.Institucion || '').replace('Municipalidad de ', 'Mun. ')}</td>
      <td title={lic.proveedor_adjudicado} style={{ color: 'var(--text-secondary)' }}>
        {truncar(lic.proveedor_adjudicado, 22)}
      </td>
      <td style={{ textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
        {formatoMonto(lic.monto_adjudicado)}
      </td>
      <td
        style={{
          textAlign: 'right',
          fontWeight: 500,
          color: lic.n_oferentes === 1 ? 'var(--danger)' : 'var(--text-primary)',
        }}
      >
        {lic.n_oferentes}
      </td>
      <td>
        <div className="flags-cell">
          {activos.length === 0 ? (
            <span className="badge badge-green">Sin flags</span>
          ) : (
            <>
              {activos.slice(0, 2).map((f) => (
                <span key={f.key} className="badge badge-red">
                  {f.label}
                </span>
              ))}
              {activos.length > 2 && <span className="badge badge-amber">+{activos.length - 2}</span>}
            </>
          )}
        </div>
      </td>
      <td>
        <div className="score-bar-wrap">
          <div className="score-bar">
            <div className="score-fill" style={{ width: `${score}%`, background: sc }} />
          </div>
          <span className="score-num" style={{ color: sc }}>
            {score.toFixed(1)}
          </span>
        </div>
      </td>
    </tr>
  )
}

const PAGINAS_POR_BLOQUE = 10

function Paginacion({ page, totalPages, onChange }) {
  const inicioBloque = Math.floor(page / PAGINAS_POR_BLOQUE) * PAGINAS_POR_BLOQUE
  const finBloque = Math.min(inicioBloque + PAGINAS_POR_BLOQUE, totalPages)
  const paginas = Array.from({ length: finBloque - inicioBloque }, (_, i) => inicioBloque + i)

  return (
    <div className="pagination">
      Página {page + 1} de {totalPages}
      <div className="pagination-pages">
        {inicioBloque > 0 && (
          <div className="page-btn page-btn-nav" onClick={() => onChange(inicioBloque - PAGINAS_POR_BLOQUE)}>
            &lt;&lt;
          </div>
        )}
        {paginas.map((p) => (
          <div key={p} className={`page-btn ${p === page ? 'active' : ''}`} onClick={() => onChange(p)}>
            {p + 1}
          </div>
        ))}
        {finBloque < totalPages && (
          <div className="page-btn page-btn-nav" onClick={() => onChange(finBloque)}>
            &gt;&gt;
          </div>
        )}
      </div>
    </div>
  )
}
