import { useEffect, useRef, useState } from 'react'
import * as d3 from 'd3'
import { api } from '../api/client'
import { useApiData } from '../hooks/useApiData'
import { EstadoCargando, EstadoError, EstadoVacio } from '../components/EstadosCarga'
import { formatoMonto, truncar } from '../utils/format'

const PALETA_COMUNIDADES = [
  '#2a78d6', // blue
  '#1baf7a', // aqua
  '#eda100', // yellow
  '#008300', // green
  '#4a3aa7', // violet
  '#e34948', // red
  '#e87ba4', // magenta
]
const COLOR_OTRAS = '#c3c2b7'
const COLOR_PROVEEDOR = '#9e9d99'
const MAX_COMUNIDADES_NOMBRADAS = PALETA_COMUNIDADES.length

export default function VistaRed() {
  const [minAdj, setMinAdj] = useState(3)
  const { data, loading, error, refetch } = useApiData(
    () => api.redInstitucionProveedor({ minAdjudicaciones: minAdj }),
    [minAdj],
  )

  if (loading) return <EstadoCargando mensaje="Cargando red institución-proveedor..." />
  if (error) return <EstadoError error={error} onRetry={refetch} />

  const nodos = data?.nodos ?? []
  const aristas = data?.aristas ?? []

  return (
    <div className="table-card red-card">
      <div className="table-header">
        <i className="ti ti-chart-dots-3" aria-hidden="true" style={{ fontSize: 16, color: 'var(--text-hint)' }} />
        <span>Red institución — proveedor</span>
        <div className="ml-auto filters-bar" style={{ padding: 0, border: 0 }}>
          <span className="filter-label">Mín. adjudicaciones por par</span>
          <select className="filter-select" value={minAdj} onChange={(e) => setMinAdj(Number(e.target.value))}>
            <option value={2}>≥ 2</option>
            <option value={3}>≥ 3</option>
            <option value={5}>≥ 5</option>
            <option value={10}>≥ 10</option>
          </select>
        </div>
      </div>

      {nodos.length === 0 ? (
        <EstadoVacio mensaje="No hay pares con suficientes adjudicaciones para graficar la red." />
      ) : (
        <GrafoRed nodos={nodos} aristas={aristas} />
      )}
    </div>
  )
}

function comunidadesOrdenadas(nodos) {
  const conteo = new Map()
  for (const n of nodos) {
    if (n.tipo === 'institucion' && n.comunidad_id != null && n.comunidad_id >= 0) {
      conteo.set(n.comunidad_id, (conteo.get(n.comunidad_id) || 0) + 1)
    }
  }
  return [...conteo.entries()].sort((a, b) => b[1] - a[1]).map(([id]) => id)
}

function colorDeNodo(nodo, colorPorComunidad) {
  if (nodo.tipo === 'proveedor') return COLOR_PROVEEDOR
  if (nodo.comunidad_id == null || nodo.comunidad_id < 0) return COLOR_OTRAS
  return colorPorComunidad.get(nodo.comunidad_id) || COLOR_OTRAS
}

function GrafoRed({ nodos, aristas }) {
  const svgRef = useRef(null)
  const contenedorRef = useRef(null)
  const tooltipRef = useRef(null)

  useEffect(() => {
    const contenedor = contenedorRef.current
    const width = contenedor.clientWidth
    const height = 440

    const comunidadesTop = comunidadesOrdenadas(nodos).slice(0, MAX_COMUNIDADES_NOMBRADAS)
    const colorPorComunidad = new Map(comunidadesTop.map((id, i) => [id, PALETA_COMUNIDADES[i]]))

    const maxAdj = d3.max(nodos, (n) => n.n_adj) || 1
    const radio = d3.scaleSqrt().domain([1, maxAdj]).range([6, 22])

    const nodosSim = nodos.map((n) => ({ ...n }))
    const aristasSim = aristas.map((a) => ({ ...a }))

    const svg = d3.select(svgRef.current).attr('viewBox', [0, 0, width, height])
    svg.selectAll('*').remove()

    const g = svg.append('g')

    svg.call(
      d3.zoom()
        .scaleExtent([0.3, 4])
        .on('zoom', (event) => g.attr('transform', event.transform)),
    )

    const tooltip = d3.select(tooltipRef.current)

    const mostrarTooltip = (event, html) => {
      tooltip
        .style('opacity', 1)
        .style('left', `${event.offsetX + 16}px`)
        .style('top', `${event.offsetY + 8}px`)
      tooltip.selectAll('*').remove()
      html(tooltip)
    }
    const ocultarTooltip = () => tooltip.style('opacity', 0)

    const link = g
      .append('g')
      .attr('stroke', 'var(--border-md)')
      .selectAll('line')
      .data(aristasSim)
      .join('line')
      .attr('stroke-width', (d) => Math.min(1 + Math.sqrt(d.n_adj), 8))
      .attr('stroke-opacity', 0.5)
      .style('cursor', 'pointer')
      .on('pointerenter', function (event, d) {
        d3.select(this).attr('stroke', 'var(--accent)').attr('stroke-opacity', 0.9)
        mostrarTooltip(event, (t) => {
          fila(t, 'Adjudicaciones', d.n_adj)
          fila(t, 'Monto total', formatoMonto(d.monto_total))
          fila(t, 'Score promedio', d.score_avg.toFixed(1))
        })
      })
      .on('pointermove', (event) => {
        tooltip.style('left', `${event.offsetX + 16}px`).style('top', `${event.offsetY + 8}px`)
      })
      .on('pointerleave', function () {
        d3.select(this).attr('stroke', 'var(--border-md)').attr('stroke-opacity', 0.5)
        ocultarTooltip()
      })

    const node = g
      .append('g')
      .selectAll('g')
      .data(nodosSim)
      .join('g')
      .style('cursor', 'grab')

    node
      .append('circle')
      .attr('r', (d) => Math.max(radio(d.n_adj), 6))
      .attr('fill', (d) => colorDeNodo(d, colorPorComunidad))
      .attr('stroke', '#fff')
      .attr('stroke-width', 1.5)

    node
      .append('circle')
      .attr('r', (d) => Math.max(radio(d.n_adj), 6) + 10)
      .attr('fill', 'transparent')
      .on('pointerenter', (event, d) => {
        mostrarTooltip(event, (t) => {
          fila(t, d.tipo === 'institucion' ? 'Institución' : 'Proveedor', truncar(d.label, 40))
          fila(t, 'Adjudicaciones (grado)', d.n_adj)
          if (d.tipo === 'institucion' && d.comunidad_id != null && d.comunidad_id >= 0) {
            fila(t, 'Comunidad', `#${d.comunidad_id}`)
          }
        })
      })
      .on('pointermove', (event) => {
        tooltip.style('left', `${event.offsetX + 16}px`).style('top', `${event.offsetY + 8}px`)
      })
      .on('pointerleave', ocultarTooltip)

    const sim = d3
      .forceSimulation(nodosSim)
      .force(
        'link',
        d3.forceLink(aristasSim).id((d) => d.id).distance(70).strength(0.25),
      )
      .force('charge', d3.forceManyBody().strength(-90))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collide', d3.forceCollide((d) => radio(d.n_adj) + 4))
      .on('tick', () => {
        link
          .attr('x1', (d) => d.source.x)
          .attr('y1', (d) => d.source.y)
          .attr('x2', (d) => d.target.x)
          .attr('y2', (d) => d.target.y)
        node.attr('transform', (d) => `translate(${d.x},${d.y})`)
      })

    node.call(arrastre(sim))

    function arrastre(simulacionActiva) {
      function iniciar(event, d) {
        if (!event.active) simulacionActiva.alphaTarget(0.2).restart()
        d.fx = d.x
        d.fy = d.y
      }
      function mover(event, d) {
        d.fx = event.x
        d.fy = event.y
      }
      function soltar(event, d) {
        if (!event.active) simulacionActiva.alphaTarget(0)
        d.fx = null
        d.fy = null
      }
      return d3.drag().on('start', iniciar).on('drag', mover).on('end', soltar)
    }

    return () => sim.stop()
  }, [nodos, aristas])

  const comunidadesTop = comunidadesOrdenadas(nodos).slice(0, MAX_COMUNIDADES_NOMBRADAS)

  return (
    <div className="red-graph-wrap" ref={contenedorRef}>
      <svg ref={svgRef} className="red-svg" />
      <div ref={tooltipRef} className="red-tooltip" />
      <div className="red-legend">
        {comunidadesTop.map((id, i) => (
          <LegendItem key={id} color={PALETA_COMUNIDADES[i]} label={`Comunidad #${id}`} />
        ))}
        <LegendItem color={COLOR_OTRAS} label="Otras comunidades" />
        <LegendItem color={COLOR_PROVEEDOR} label="Proveedor" />
      </div>
    </div>
  )
}

function LegendItem({ color, label }) {
  return (
    <div className="red-legend-item">
      <span className="red-legend-dot" style={{ background: color }} />
      <span>{label}</span>
    </div>
  )
}

function fila(tooltip, label, valor) {
  const row = tooltip.append('div').attr('class', 'red-tooltip-row')
  row.append('span').attr('class', 'red-tooltip-label').text(label)
  row.append('span').attr('class', 'red-tooltip-value').text(String(valor))
}
