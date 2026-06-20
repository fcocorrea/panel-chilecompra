const NAV_ITEMS = [
  { id: 'ranking', label: 'Ranking sospechosos', icon: 'ti-alert-triangle', section: 'Vistas' },
  { id: 'instituciones', label: 'Por institución', icon: 'ti-building', section: 'Vistas' },
  { id: 'pares', label: 'Pares unidad-prov.', icon: 'ti-arrows-exchange', section: 'Vistas' },
  { id: 'red', label: 'Red institucional', icon: 'ti-affiliate', section: 'Análisis' },
]

export default function Sidebar({ activeTab, onChangeTab, totalLicitaciones }) {
  let lastSection = null

  return (
    <div className="sidebar">
      <div className="logo">
        <div className="logo-badge">ChileCompra</div>
        <div className="logo-title">Panel de auditoría</div>
        <div className="logo-sub">Municipalidades · v3</div>
      </div>
      <nav className="nav">
        {NAV_ITEMS.map((item) => {
          const showSectionHeader = item.section !== lastSection
          lastSection = item.section
          return (
            <div key={item.id}>
              {showSectionHeader && <div className="nav-section">{item.section}</div>}
              <div
                className={`nav-item ${activeTab === item.id ? 'active' : ''}`}
                onClick={() => onChangeTab(item.id)}
              >
                <i className={`ti ${item.icon}`} aria-hidden="true" />
                {item.label}
                {item.id === 'ranking' && (
                  <span className="count">
                    {totalLicitaciones === null ? '—' : totalLicitaciones}
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </nav>
    </div>
  )
}
