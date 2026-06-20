import { useState } from 'react'
import Sidebar from './components/Sidebar'
import DetallePanel from './components/DetallePanel'
import VistaRanking from './pages/VistaRanking'
import VistaInstituciones from './pages/VistaInstituciones'
import VistaPares from './pages/VistaPares'
import VistaRed from './pages/VistaRed'

const TITULOS = {
  ranking: ['Ranking de licitaciones sospechosas', 'Ordenadas por score de fraude descendente'],
  instituciones: ['Ranking por institución', 'Municipios con mayor concentración de licitaciones de riesgo'],
  pares: ['Pares institución — proveedor', 'Relaciones de alta concentración y score elevado'],
  red: ['Red institucional', 'Grafo bipartito institución ↔ proveedor'],
}

const TABS = [
  { id: 'ranking', label: 'Licitaciones', icon: 'ti-list-numbers' },
  { id: 'instituciones', label: 'Instituciones', icon: 'ti-building-bank' },
  { id: 'pares', label: 'Pares', icon: 'ti-link' },
  { id: 'red', label: 'Red', icon: 'ti-chart-dots-3' },
]

export default function App() {
  const [activeTab, setActiveTab] = useState('ranking')
  const [selectedId, setSelectedId] = useState(null)
  const [totalLicitaciones, setTotalLicitaciones] = useState(null)

  const handleChangeTab = (tab) => {
    setActiveTab(tab)
    if (tab !== 'ranking') setSelectedId(null)
  }

  const [titulo, subtitulo] = TITULOS[activeTab]

  return (
    <div className="app">
      <Sidebar activeTab={activeTab} onChangeTab={handleChangeTab} totalLicitaciones={totalLicitaciones} />

      <div className="main">
        <div className="topbar">
          <div>
            <h1>{titulo}</h1>
            <div className="topbar-sub">{subtitulo}</div>
          </div>
        </div>

        <div className="tab-bar">
          {TABS.map((tab) => (
            <div
              key={tab.id}
              className={`tab ${activeTab === tab.id ? 'active' : ''}`}
              onClick={() => handleChangeTab(tab.id)}
            >
              <i className={`ti ${tab.icon}`} aria-hidden="true" /> {tab.label}
            </div>
          ))}
        </div>

        <div className="content">
          {activeTab === 'ranking' && (
            <VistaRanking
              onSelectLicitacion={setSelectedId}
              selectedId={selectedId}
              onTotalChange={setTotalLicitaciones}
            />
          )}
          {activeTab === 'instituciones' && <VistaInstituciones />}
          {activeTab === 'pares' && <VistaPares />}
          {activeTab === 'red' && <VistaRed />}
        </div>
      </div>

      {activeTab === 'ranking' && (
        <DetallePanel nroLicitacion={selectedId} onClose={() => setSelectedId(null)} />
      )}
    </div>
  )
}
