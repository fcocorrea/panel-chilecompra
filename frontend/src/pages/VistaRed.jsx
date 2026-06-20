export default function VistaRed() {
  return (
    <div className="table-card vista-red-placeholder">
      <i className="ti ti-chart-dots-3" aria-hidden="true" />
      <div className="vista-red-titulo">Visualización de red</div>
      <div className="vista-red-detalle">
        El grafo bipartito institución ↔ proveedor (con detección de comunidades Louvain) ya se
        calcula en el backend, pero su visualización interactiva con D3 todavía no está
        construida en este panel.
      </div>
    </div>
  )
}
