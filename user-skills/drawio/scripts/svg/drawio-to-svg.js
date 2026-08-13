/**
 * drawio-to-svg.js
 * Converts draw.io mxGraphModel XML to standalone SVG
 * Uses shared XML utilities from ../shared/xml-utils.js
 */

import {
  attr,
  decodeEntities,
  escapeXml,
  extractCells,
  extractGraphAttrs,
  parseStyle
} from '../shared/xml-utils.js'

/**
 * Parse mxGraphModel XML into a structured object
 * @param {string} xml
 * @returns {{ graph: object, cells: object[] }}
 */
function parseDrawioXml(xml) {
  const graph = extractGraphAttrs(xml)
  const cells = extractCells(xml)
  return { graph, cells }
}

// ============================================================================
// Shape Classification
// ============================================================================

/**
 * Determine the shape type from a parsed style map
 * @param {Map<string, string>} style
 * @returns {string}
 */
function classifyShape(style) {
  const shape = style.get('shape')
  if (style.has('umlClass') || shape === 'umlClass') return 'umlClass'
  if (shape === 'cylinder3' || shape === 'cylinder') return 'cylinder'
  if (shape === 'parallelogram') return 'parallelogram'
  if (shape === 'document') return 'document'
  if (shape === 'cloud') return 'cloud'
  if (shape === 'switch') return 'switch'
  if (shape === 'hexagon') return 'hexagon'
  if (shape === 'mxgraph.cisco.firewalls.firewall') return 'firewall'
  if (shape === 'mxgraph.cisco.wireless.access_point') return 'wirelessAp'
  if (style.has('rhombus')) return 'rhombus'
  if (style.has('ellipse')) return 'ellipse'
  const rounded = style.get('rounded')
  const arcSize = Number(style.get('arcSize')) || 0
  if (rounded === '1' && arcSize >= 50) return 'stadium'
  if (rounded === '1') return 'roundedRect'
  return 'rect'
}

// ============================================================================
// Arrow Marker Definitions
// ============================================================================

const ARROW_TYPES = ['block', 'open', 'classic', 'diamond']

function normalizeArrowType(arrowType) {
  if (arrowType === 'diamondThin') return 'diamond'
  return arrowType
}

/**
 * Build SVG <defs> with arrow markers
 * @returns {string}
 */
function buildMarkerDefs() {
  const markers = []

  // block arrow (filled triangle)
  markers.push(
    '<marker id="arrow-block" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">',
    '  <path d="M 0 0 L 10 5 L 0 10 Z" fill="currentColor"/>',
    '</marker>'
  )

  // block arrow (hollow triangle)
  markers.push(
    '<marker id="arrow-block-open" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">',
    '  <path d="M 0 0 L 10 5 L 0 10 Z" fill="#FFFFFF" stroke="currentColor" stroke-width="1.2"/>',
    '</marker>'
  )

  // open arrow (chevron)
  markers.push(
    '<marker id="arrow-open" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">',
    '  <path d="M 0 0 L 10 5 L 0 10" fill="none" stroke="currentColor" stroke-width="1.5"/>',
    '</marker>'
  )

  // classic arrow (filled arrow)
  markers.push(
    '<marker id="arrow-classic" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">',
    '  <path d="M 0 0 L 10 5 L 0 10 L 3 5 Z" fill="currentColor"/>',
    '</marker>'
  )

  // diamond
  markers.push(
    '<marker id="arrow-diamond" viewBox="0 0 12 12" refX="12" refY="6" markerWidth="10" markerHeight="10" orient="auto-start-reverse">',
    '  <path d="M 0 6 L 6 0 L 12 6 L 6 12 Z" fill="currentColor"/>',
    '</marker>'
  )

  // hollow diamond
  markers.push(
    '<marker id="arrow-diamond-open" viewBox="0 0 12 12" refX="12" refY="6" markerWidth="10" markerHeight="10" orient="auto-start-reverse">',
    '  <path d="M 0 6 L 6 0 L 12 6 L 6 12 Z" fill="#FFFFFF" stroke="currentColor" stroke-width="1.2"/>',
    '</marker>'
  )

  return `<defs>\n${markers.join('\n')}\n</defs>`
}

/**
 * Resolve an arrow type name to a marker URL reference
 * @param {string} arrowType
 * @param {'start'|'end'} position
 * @returns {string} marker-start or marker-end attribute, or empty string
 */
function markerRef(arrowType, position, filled = true) {
  const normalizedArrowType = normalizeArrowType(arrowType)
  if (!normalizedArrowType || normalizedArrowType === 'none') return ''
  const suffix = !filled && ['block', 'diamond'].includes(normalizedArrowType) ? '-open' : ''
  const id = ARROW_TYPES.includes(normalizedArrowType) ? `arrow-${normalizedArrowType}${suffix}` : 'arrow-block'
  const attrName = position === 'start' ? 'marker-start' : 'marker-end'
  return ` ${attrName}="url(#${id})"`
}

// ============================================================================
// Shape SVG Renderers
// ============================================================================

function htmlSectionToLines(section) {
  if (!section) return []
  return section
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/div>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .split(/\r?\n/)
    .map(line => decodeEntities(line.trim()))
    .filter(Boolean)
}

function parseUmlClassSections(value) {
  const decoded = decodeEntities(value)
  const sections = decoded.split(/<hr\b[^>]*\/?>/i)
  return {
    title: htmlSectionToLines(sections[0]),
    attributes: htmlSectionToLines(sections[1]),
    operations: htmlSectionToLines(sections[2])
  }
}

function renderTextLine({ x, y, text, fontFamily, fontSize, fontColor, anchor = 'start', weight = null }) {
  const weightAttr = weight ? ` font-weight="${weight}"` : ''
  return (
    `<text x="${formatPoint(x)}" y="${formatPoint(y)}" text-anchor="${anchor}" dominant-baseline="auto" ` +
    `font-family="${escapeXml(fontFamily)}" font-size="${fontSize}" fill="${fontColor}"${weightAttr}>` +
    `${escapeXml(text)}</text>`
  )
}

function renderUmlClassVertex(cell, style, baseAttrs, strokeColor, strokeWidth, fontColor, fontSize, fontFamily) {
  const geo = cell.geometry || { x: 0, y: 0, width: 180, height: 120 }
  const { x, y, width, height } = geo
  const sections = parseUmlClassSections(cell.value)
  const titleLines = sections.title.length ? sections.title : [decodeEntities(cell.value)]
  const lineHeight = fontSize + 6
  const titleHeight = Math.max(28, titleLines.length * lineHeight + 6)
  const attributeHeight = sections.attributes.length > 0
    ? sections.attributes.length * lineHeight + 6
    : 0
  const firstDividerY = y + titleHeight
  const secondDividerY = Math.min(y + height - 20, firstDividerY + attributeHeight)

  const parts = [
    `<rect x="${x}" y="${y}" width="${width}" height="${height}" ${baseAttrs}/>`
  ]

  if (sections.attributes.length > 0 || sections.operations.length > 0) {
    parts.push(`<line x1="${x}" y1="${formatPoint(firstDividerY)}" x2="${x + width}" y2="${formatPoint(firstDividerY)}" stroke="${strokeColor}" stroke-width="${strokeWidth}"/>`)
  }
  if (sections.operations.length > 0) {
    parts.push(`<line x1="${x}" y1="${formatPoint(secondDividerY)}" x2="${x + width}" y2="${formatPoint(secondDividerY)}" stroke="${strokeColor}" stroke-width="${strokeWidth}"/>`)
  }

  const titleStartY = y + Math.max(18, (titleHeight - (titleLines.length - 1) * lineHeight) / 2 + 4)
  titleLines.forEach((line, index) => {
    parts.push(renderTextLine({
      x: x + width / 2,
      y: titleStartY + index * lineHeight,
      text: line,
      fontFamily,
      fontSize,
      fontColor,
      anchor: 'middle',
      weight: index === titleLines.length - 1 ? '700' : null
    }))
  })

  sections.attributes.forEach((line, index) => {
    parts.push(renderTextLine({
      x: x + 8,
      y: firstDividerY + 17 + index * lineHeight,
      text: line,
      fontFamily,
      fontSize,
      fontColor
    }))
  })

  sections.operations.forEach((line, index) => {
    parts.push(renderTextLine({
      x: x + 8,
      y: secondDividerY + 17 + index * lineHeight,
      text: line,
      fontFamily,
      fontSize,
      fontColor
    }))
  })

  return parts.join('\n')
}

/**
 * Render a vertex cell to SVG elements
 * @param {object} cell - parsed cell
 * @param {Map<string, string>} style - parsed style
 * @returns {string} SVG markup
 */
function renderVertex(cell, style) {
  const geo = cell.geometry || { x: 0, y: 0, width: 120, height: 60 }
  const { x, y, width, height } = geo

  const fillColor = style.get('fillColor') || '#FFFFFF'
  const strokeColor = style.get('strokeColor') || '#000000'
  const strokeWidth = Number(style.get('strokeWidth')) || 1
  const fontColor = style.get('fontColor') || '#000000'
  const fontSize = Number(style.get('fontSize')) || 12
  const fontFamily = style.get('fontFamily') || 'sans-serif'

  let dashAttr = ''
  if (style.get('dashed') === '1') {
    const pattern = style.get('dashPattern') || '3 3'
    dashAttr = ` stroke-dasharray="${pattern}"`
  }

  const shapeType = classifyShape(style)
  const parts = []
  const baseAttrs = `fill="${fillColor}" stroke="${strokeColor}" stroke-width="${strokeWidth}"${dashAttr}`

  switch (shapeType) {
    case 'umlClass': {
      parts.push(renderUmlClassVertex(cell, style, baseAttrs, strokeColor, strokeWidth, fontColor, fontSize, fontFamily))
      break
    }

    case 'roundedRect': {
      const rx = Number(style.get('arcSize')) || 8
      parts.push(`<rect x="${x}" y="${y}" width="${width}" height="${height}" rx="${rx}" ${baseAttrs}/>`)
      break
    }

    case 'stadium': {
      const rx = height / 2
      parts.push(`<rect x="${x}" y="${y}" width="${width}" height="${height}" rx="${rx}" ${baseAttrs}/>`)
      break
    }

    case 'cylinder': {
      const ellipseRY = Math.min(12, height * 0.15)
      // Body rectangle
      parts.push(`<rect x="${x}" y="${y + ellipseRY}" width="${width}" height="${height - ellipseRY * 2}" ${baseAttrs}/>`)
      // Bottom ellipse
      parts.push(`<ellipse cx="${x + width / 2}" cy="${y + height - ellipseRY}" rx="${width / 2}" ry="${ellipseRY}" ${baseAttrs}/>`)
      // Top ellipse (drawn last so it's on top)
      parts.push(`<ellipse cx="${x + width / 2}" cy="${y + ellipseRY}" rx="${width / 2}" ry="${ellipseRY}" ${baseAttrs}/>`)
      // Side lines connecting top and bottom ellipses
      parts.push(`<line x1="${x}" y1="${y + ellipseRY}" x2="${x}" y2="${y + height - ellipseRY}" stroke="${strokeColor}" stroke-width="${strokeWidth}"/>`)
      parts.push(`<line x1="${x + width}" y1="${y + ellipseRY}" x2="${x + width}" y2="${y + height - ellipseRY}" stroke="${strokeColor}" stroke-width="${strokeWidth}"/>`)
      break
    }

    case 'rhombus': {
      const cx = x + width / 2
      const cy = y + height / 2
      const points = `${cx},${y} ${x + width},${cy} ${cx},${y + height} ${x},${cy}`
      parts.push(`<polygon points="${points}" ${baseAttrs}/>`)
      break
    }

    case 'ellipse': {
      const cx = x + width / 2
      const cy = y + height / 2
      parts.push(`<ellipse cx="${cx}" cy="${cy}" rx="${width / 2}" ry="${height / 2}" ${baseAttrs}/>`)
      break
    }

    case 'parallelogram': {
      const skew = width * 0.2
      const points = `${x + skew},${y} ${x + width},${y} ${x + width - skew},${y + height} ${x},${y + height}`
      parts.push(`<polygon points="${points}" ${baseAttrs}/>`)
      break
    }

    case 'hexagon': {
      const inset = Math.min(width * 0.22, 24)
      const points = [
        `${x + inset},${y}`,
        `${x + width - inset},${y}`,
        `${x + width},${y + height / 2}`,
        `${x + width - inset},${y + height}`,
        `${x + inset},${y + height}`,
        `${x},${y + height / 2}`
      ].join(' ')
      parts.push(`<polygon points="${points}" ${baseAttrs}/>`)
      break
    }

    case 'switch': {
      const inset = Math.min(width * 0.18, 18)
      const d = [
        `M ${x + inset} ${y}`,
        `L ${x + width - inset} ${y}`,
        `L ${x + width} ${y + height / 2}`,
        `L ${x + width - inset} ${y + height}`,
        `L ${x + inset} ${y + height}`,
        `L ${x} ${y + height / 2}`,
        'Z'
      ].join(' ')
      const portY1 = y + height * 0.35
      const portY2 = y + height * 0.65
      parts.push(`<path d="${d}" ${baseAttrs}/>`)
      parts.push(`<line x1="${x + inset}" y1="${portY1}" x2="${x + width - inset}" y2="${portY1}" stroke="${strokeColor}" stroke-width="${strokeWidth}"/>`)
      parts.push(`<line x1="${x + inset}" y1="${portY2}" x2="${x + width - inset}" y2="${portY2}" stroke="${strokeColor}" stroke-width="${strokeWidth}"/>`)
      break
    }

    case 'document': {
      const waveH = height * 0.1
      const d = [
        `M ${x} ${y}`,
        `L ${x + width} ${y}`,
        `L ${x + width} ${y + height - waveH}`,
        `Q ${x + width * 0.75} ${y + height + waveH} ${x + width / 2} ${y + height - waveH}`,
        `Q ${x + width * 0.25} ${y + height - waveH * 3} ${x} ${y + height - waveH}`,
        'Z'
      ].join(' ')
      parts.push(`<path d="${d}" ${baseAttrs}/>`)
      break
    }

    case 'cloud': {
      // Simplified cloud: overlapping circles
      const cx = x + width / 2
      const cy = y + height / 2
      const rx = width * 0.45
      const ry = height * 0.35
      const d = [
        `M ${x + width * 0.25} ${cy + ry * 0.5}`,
        `A ${rx * 0.5} ${ry * 0.6} 0 0 1 ${x + width * 0.15} ${cy - ry * 0.2}`,
        `A ${rx * 0.5} ${ry * 0.6} 0 0 1 ${x + width * 0.35} ${cy - ry * 0.8}`,
        `A ${rx * 0.5} ${ry * 0.5} 0 0 1 ${cx} ${y + height * 0.15}`,
        `A ${rx * 0.5} ${ry * 0.5} 0 0 1 ${x + width * 0.7} ${cy - ry * 0.7}`,
        `A ${rx * 0.6} ${ry * 0.7} 0 0 1 ${x + width * 0.85} ${cy}`,
        `A ${rx * 0.5} ${ry * 0.6} 0 0 1 ${x + width * 0.75} ${cy + ry * 0.7}`,
        `A ${rx * 0.6} ${ry * 0.4} 0 0 1 ${x + width * 0.5} ${cy + ry * 0.8}`,
        `A ${rx * 0.5} ${ry * 0.4} 0 0 1 ${x + width * 0.25} ${cy + ry * 0.5}`,
        'Z'
      ].join(' ')
      parts.push(`<path d="${d}" ${baseAttrs}/>`)
      break
    }

    case 'firewall': {
      const archHeight = height * 0.18
      const bodyTop = y + archHeight
      const brickWidth = width / 4
      const brickHeight = (height - archHeight) / 3
      const outer = [
        `M ${x} ${bodyTop}`,
        `Q ${x + width / 2} ${y - archHeight * 0.2} ${x + width} ${bodyTop}`,
        `L ${x + width} ${y + height}`,
        `L ${x} ${y + height}`,
        'Z'
      ].join(' ')
      const mortar = [
        `M ${x + brickWidth} ${bodyTop} L ${x + brickWidth} ${y + height}`,
        `M ${x + brickWidth * 2} ${bodyTop} L ${x + brickWidth * 2} ${y + height}`,
        `M ${x + brickWidth * 3} ${bodyTop} L ${x + brickWidth * 3} ${y + height}`,
        `M ${x} ${bodyTop + brickHeight} L ${x + width} ${bodyTop + brickHeight}`,
        `M ${x} ${bodyTop + brickHeight * 2} L ${x + width} ${bodyTop + brickHeight * 2}`
      ].join(' ')
      parts.push(`<path d="${outer}" ${baseAttrs}/>`)
      parts.push(`<path d="${mortar}" fill="none" stroke="${strokeColor}" stroke-width="${Math.max(strokeWidth * 0.8, 1)}"/>`)
      break
    }

    case 'wirelessAp': {
      const cx = x + width / 2
      const cy = y + height / 2
      const baseRy = height * 0.12
      const baseY = y + height * 0.78
      const arc1 = [
        `M ${cx - width * 0.16} ${cy + height * 0.02}`,
        `Q ${cx} ${cy - height * 0.18} ${cx + width * 0.16} ${cy + height * 0.02}`
      ].join(' ')
      const arc2 = [
        `M ${cx - width * 0.28} ${cy + height * 0.1}`,
        `Q ${cx} ${cy - height * 0.32} ${cx + width * 0.28} ${cy + height * 0.1}`
      ].join(' ')
      parts.push(`<ellipse cx="${cx}" cy="${baseY}" rx="${width * 0.16}" ry="${baseRy}" ${baseAttrs}/>`)
      parts.push(`<line x1="${cx}" y1="${baseY - baseRy}" x2="${cx}" y2="${cy + height * 0.12}" stroke="${strokeColor}" stroke-width="${strokeWidth}"/>`)
      parts.push(`<path d="${arc1}" fill="none" stroke="${strokeColor}" stroke-width="${strokeWidth}"/>`)
      parts.push(`<path d="${arc2}" fill="none" stroke="${strokeColor}" stroke-width="${strokeWidth}"/>`)
      break
    }

    default: {
      // Plain rectangle
      parts.push(`<rect x="${x}" y="${y}" width="${width}" height="${height}" ${baseAttrs}/>`)
      break
    }
  }

  // Text label
  const label = shapeType === 'umlClass' ? '' : decodeEntities(cell.value)
  if (label) {
    const textX = x + width / 2
    const textY = y + height / 2
    parts.push(
      `<text x="${textX}" y="${textY}" text-anchor="middle" dominant-baseline="central" ` +
      `font-family="${escapeXml(fontFamily)}" font-size="${fontSize}" fill="${fontColor}">` +
      `${escapeXml(label)}</text>`
    )
  }

  return parts.join('\n')
}

// ============================================================================
// Edge Rendering
// ============================================================================

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max)
}

function cellBounds(cell) {
  const geo = cell.geometry || { x: 0, y: 0, width: 120, height: 60 }
  return {
    x: geo.x,
    y: geo.y,
    width: geo.width || 120,
    height: geo.height || 60
  }
}

/**
 * Compute center point of a cell's geometry.
 * @param {object} cell
 * @returns {{ x: number, y: number }}
 */
function cellCenter(cell) {
  const geo = cellBounds(cell)
  return {
    x: geo.x + geo.width / 2,
    y: geo.y + geo.height / 2
  }
}

/**
 * Resolve a fixed draw.io connection point from edge style.
 * @param {object} cell
 * @param {Map<string, string>} style
 * @param {string} xKey
 * @param {string} yKey
 * @returns {{ x: number, y: number } | null}
 */
function styledConnectionPoint(cell, style, xKey, yKey, dxKey, dyKey) {
  if (!cell) return null
  const xRatio = Number(style.get(xKey))
  const yRatio = Number(style.get(yKey))
  if (!Number.isFinite(xRatio) || !Number.isFinite(yRatio)) return null

  const geo = cellBounds(cell)
  const dx = Number(style.get(dxKey))
  const dy = Number(style.get(dyKey))
  return {
    x: geo.x + geo.width * xRatio + (Number.isFinite(dx) ? dx : 0),
    y: geo.y + geo.height * yRatio + (Number.isFinite(dy) ? dy : 0)
  }
}

/**
 * Find the point where a center-to-target line exits a rectangular cell.
 * @param {object} cell
 * @param {{ x: number, y: number }} toward
 * @returns {{ x: number, y: number }}
 */
function boundaryPoint(cell, toward) {
  const geo = cellBounds(cell)
  const center = cellCenter(cell)
  const dx = toward.x - center.x
  const dy = toward.y - center.y
  if (Math.abs(dx) < 0.0001 && Math.abs(dy) < 0.0001) return center

  const halfWidth = geo.width / 2
  const halfHeight = geo.height / 2
  const tx = Math.abs(dx) > 0.0001 ? halfWidth / Math.abs(dx) : Infinity
  const ty = Math.abs(dy) > 0.0001 ? halfHeight / Math.abs(dy) : Infinity
  const t = Math.min(tx, ty)

  return {
    x: center.x + dx * t,
    y: center.y + dy * t
  }
}

function edgePathPoints(cell, style, cellMap) {
  const sourceCell = cell.source ? cellMap.get(cell.source) : null
  const targetCell = cell.target ? cellMap.get(cell.target) : null
  const waypoints = cell.geometry?.points || []

  const sourceFallback = waypoints[0] || (targetCell ? cellCenter(targetCell) : { x: 100, y: 100 })
  const targetFallback = waypoints[waypoints.length - 1] || (sourceCell ? cellCenter(sourceCell) : { x: 0, y: 0 })

  const sourcePoint = styledConnectionPoint(sourceCell, style, 'exitX', 'exitY', 'exitDx', 'exitDy') ||
    (sourceCell ? boundaryPoint(sourceCell, sourceFallback) : { x: 0, y: 0 })
  const targetPoint = styledConnectionPoint(targetCell, style, 'entryX', 'entryY', 'entryDx', 'entryDy') ||
    (targetCell ? boundaryPoint(targetCell, targetFallback) : { x: 100, y: 100 })

  return [sourcePoint, ...waypoints, targetPoint]
}

function distance(a, b) {
  return Math.hypot(b.x - a.x, b.y - a.y)
}

function pointOnPath(points, ratio) {
  if (points.length === 0) return { x: 0, y: 0 }
  if (points.length === 1) return points[0]

  const total = points.slice(1).reduce((sum, point, index) => sum + distance(points[index], point), 0)
  if (total === 0) return points[0]

  const target = total * clamp(ratio, 0, 1)
  let walked = 0
  for (let i = 1; i < points.length; i++) {
    const start = points[i - 1]
    const end = points[i]
    const segment = distance(start, end)
    if (walked + segment >= target) {
      const localRatio = segment === 0 ? 0 : (target - walked) / segment
      return {
        x: start.x + (end.x - start.x) * localRatio,
        y: start.y + (end.y - start.y) * localRatio
      }
    }
    walked += segment
  }

  return points[points.length - 1]
}

function labelRatioFromGeometry(cell) {
  const raw = Number(cell.geometry?.labelX)
  if (!Number.isFinite(raw)) return 0.5
  if (raw >= 0 && raw <= 1) return raw
  return clamp((raw + 1) / 2, 0, 1)
}

function edgeLabelPoint(cell, style, cellMap) {
  const label = decodeEntities(cell.value)
  if (!label) return null

  const points = edgePathPoints(cell, style, cellMap)
  const base = pointOnPath(points, labelRatioFromGeometry(cell))
  const offset = cell.geometry?.offset || { x: 0, y: 0 }
  const labelY = Number(cell.geometry?.labelY)
  return {
    x: base.x + offset.x,
    y: base.y + offset.y + (Number.isFinite(labelY) ? labelY : 0)
  }
}

function formatPoint(value) {
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(2)))
}

/**
 * Render an edge stroke to SVG.
 * @param {object} cell - parsed edge cell
 * @param {Map<string, string>} style - parsed style
 * @param {Map<string, object>} cellMap - id to cell lookup
 * @returns {string} SVG markup
 */
function renderEdgePath(cell, style, cellMap) {
  const strokeColor = style.get('strokeColor') || '#000000'
  const strokeWidth = Number(style.get('strokeWidth')) || 1

  let dashAttr = ''
  if (style.get('dashed') === '1') {
    const pattern = style.get('dashPattern') || '3 3'
    dashAttr = ` stroke-dasharray="${pattern}"`
  }

  // Arrow markers
  const endArrow = style.get('endArrow') || 'classic'
  const startArrow = style.get('startArrow') || ''
  const endFill = style.get('endFill') !== '0'
  const startFill = style.get('startFill') !== '0'
  const endRef = markerRef(endArrow, 'end', endFill)
  const startRef = markerRef(startArrow, 'start', startFill)
  const colorStyle = ` style="color: ${strokeColor}"`
  const points = edgePathPoints(cell, style, cellMap)

  if (points.length === 2) {
    const [start, end] = points
    return (
      `<line x1="${formatPoint(start.x)}" y1="${formatPoint(start.y)}" ` +
      `x2="${formatPoint(end.x)}" y2="${formatPoint(end.y)}" ` +
      `stroke="${strokeColor}" stroke-width="${strokeWidth}"${dashAttr}` +
      `${endRef}${startRef}${colorStyle} fill="none"/>`
    )
  }

  const pointList = points.map(point => `${formatPoint(point.x)},${formatPoint(point.y)}`).join(' ')
  return (
    `<polyline points="${pointList}" ` +
    `stroke="${strokeColor}" stroke-width="${strokeWidth}"${dashAttr}` +
    `${endRef}${startRef}${colorStyle} fill="none"/>`
  )
}

/**
 * Render an edge label to SVG.
 * @param {object} cell - parsed edge cell
 * @param {Map<string, string>} style - parsed style
 * @param {Map<string, object>} cellMap - id to cell lookup
 * @returns {string} SVG markup
 */
function renderEdgeLabel(cell, style, cellMap) {
  const label = decodeEntities(cell.value)
  if (!label) return ''

  const fontColor = style.get('fontColor') || '#000000'
  const fontSize = Number(style.get('fontSize')) || 11
  const point = edgeLabelPoint(cell, style, cellMap)
  if (!point) return ''

  return (
    `<text x="${formatPoint(point.x)}" y="${formatPoint(point.y)}" text-anchor="middle" dominant-baseline="auto" ` +
    `font-size="${fontSize}" fill="${fontColor}">${escapeXml(label)}</text>`
  )
}

// ============================================================================
// Main Converter
// ============================================================================

/**
 * Convert draw.io mxGraphModel XML to standalone SVG
 * @param {string} xmlString - draw.io XML content
 * @returns {string} SVG markup
 * @throws {Error} if input is empty or not a string
 */
export function drawioToSvg(xmlString) {
  if (!xmlString || typeof xmlString !== 'string' || xmlString.trim().length === 0) {
    throw new Error('Input XML string must be non-empty')
  }

  const { graph, cells } = parseDrawioXml(xmlString)

  // Build cell lookup map
  const cellMap = new Map()
  for (const cell of cells) {
    if (cell.id) cellMap.set(cell.id, cell)
  }

  // Separate vertices and edges
  const vertices = cells.filter(c => c.vertex && c.parent !== '0')
  const edges = cells.filter(c => c.edge)

  // Calculate viewBox dimensions from vertices, edges, and labels.
  let minX = 0
  let minY = 0
  let maxX = graph.pageWidth
  let maxY = graph.pageHeight

  const expandBounds = ({ x, y }, padding = 20) => {
    if (!Number.isFinite(x) || !Number.isFinite(y)) return
    minX = Math.min(minX, x - padding)
    minY = Math.min(minY, y - padding)
    maxX = Math.max(maxX, x + padding)
    maxY = Math.max(maxY, y + padding)
  }

  // Expand viewBox if any shape extends beyond page bounds
  for (const v of vertices) {
    if (v.geometry) {
      expandBounds({ x: v.geometry.x, y: v.geometry.y }, 0)
      expandBounds({
        x: v.geometry.x + v.geometry.width,
        y: v.geometry.y + v.geometry.height
      })
    }
  }

  for (const e of edges) {
    const style = parseStyle(e.style)
    for (const point of edgePathPoints(e, style, cellMap)) {
      expandBounds(point)
    }

    const labelPoint = edgeLabelPoint(e, style, cellMap)
    if (labelPoint) {
      const label = decodeEntities(e.value)
      const fontSize = Number(style.get('fontSize')) || 11
      const estimatedLabelWidth = label.length * fontSize * 0.6
      expandBounds(labelPoint, Math.max(estimatedLabelWidth / 2, fontSize) + 20)
    }
  }

  const svgWidth = maxX - minX
  const svgHeight = maxY - minY

  // Encode original XML as base64 for round-trip editing
  const base64Xml = Buffer.from(xmlString, 'utf-8').toString('base64')

  // Build SVG
  const svgParts = []
  svgParts.push(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${formatPoint(svgWidth)}" height="${formatPoint(svgHeight)}" ` +
    `viewBox="${formatPoint(minX)} ${formatPoint(minY)} ${formatPoint(svgWidth)} ${formatPoint(svgHeight)}" data-drawio="${base64Xml}">`
  )

  // Defs (arrow markers)
  svgParts.push(buildMarkerDefs())

  // Background
  if (graph.background && graph.background !== 'none') {
    svgParts.push(
      `<rect x="${formatPoint(minX)}" y="${formatPoint(minY)}" ` +
      `width="${formatPoint(svgWidth)}" height="${formatPoint(svgHeight)}" fill="${graph.background}"/>`
    )
  }

  // Render edge strokes first, then vertices, then labels. This keeps connectors
  // from visually covering icons or shapes while preserving readable labels.
  for (const e of edges) {
    const style = parseStyle(e.style)
    svgParts.push(renderEdgePath(e, style, cellMap))
  }

  for (const v of vertices) {
    const style = parseStyle(v.style)
    svgParts.push(renderVertex(v, style))
  }

  for (const e of edges) {
    const style = parseStyle(e.style)
    const label = renderEdgeLabel(e, style, cellMap)
    if (label) svgParts.push(label)
  }

  svgParts.push('</svg>')
  return svgParts.join('\n')
}
