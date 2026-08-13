/**
 * drawio-to-svg.test.js
 * Unit tests for the draw.io XML to SVG converter
 * Uses Node.js built-in test runner
 */

import { describe, it } from 'node:test'
import assert from 'node:assert'
import { drawioToSvg } from './drawio-to-svg.js'

// ============================================================================
// Test Fixtures
// ============================================================================

const BASIC_TWO_NODES_ONE_EDGE = `
<mxGraphModel dx="0" dy="0" pageWidth="800" pageHeight="600">
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="Node A" style="rounded=1;fillColor=#DBEAFE;strokeColor=#2563EB;" vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="120" height="60" as="geometry"/>
    </mxCell>
    <mxCell id="3" value="Node B" style="rounded=1;fillColor=#D1FAE5;strokeColor=#059669;" vertex="1" parent="1">
      <mxGeometry x="400" y="100" width="120" height="60" as="geometry"/>
    </mxCell>
    <mxCell id="4" style="endArrow=block;" edge="1" source="2" target="3" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`

const SERVICE_NODE = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="API Gateway" style="rounded=1;fillColor=#DBEAFE;strokeColor=#2563EB;" vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="120" height="60" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`

const DATABASE_NODE = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="PostgreSQL" style="shape=cylinder3;fillColor=#D1FAE5;strokeColor=#059669;" vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="100" height="80" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`

const EDGE_WITH_BLOCK_ARROW = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="A" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="50" y="50" width="80" height="40" as="geometry"/>
    </mxCell>
    <mxCell id="3" value="B" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="250" y="50" width="80" height="40" as="geometry"/>
    </mxCell>
    <mxCell id="4" style="endArrow=block;" edge="1" source="2" target="3" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`

const NODE_WITH_LABEL = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="Hello" style="rounded=0;" vertex="1" parent="1">
      <mxGeometry x="50" y="50" width="100" height="50" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`

const NODE_WITH_COLOR = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="Red Box" style="fillColor=#FF0000;strokeColor=#CC0000;" vertex="1" parent="1">
      <mxGeometry x="50" y="50" width="100" height="50" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`

const UML_CLASS_NODE = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="&lt;b&gt;Customer&lt;/b&gt;&lt;hr size=&quot;1&quot;/&gt;+ id: String&lt;br/&gt;+ name: String&lt;hr size=&quot;1&quot;/&gt;+ placeOrder(): Order" style="umlClass=1;rounded=0;html=1;whiteSpace=wrap;overflow=fill;fillColor=#FFFFFF;strokeColor=#000000;fontColor=#000000;fontSize=12;fontFamily=Helvetica;verticalAlign=top;align=left;" vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="180" height="120" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`

const SWITCH_NODE = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="Core Switch" style="shape=switch;fillColor=#DBEAFE;strokeColor=#2563EB;" vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="120" height="60" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`

const LOAD_BALANCER_NODE = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="Reverse Proxy" style="shape=hexagon;perimeter=hexagonPerimeter2;fillColor=#DBEAFE;strokeColor=#2563EB;" vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="140" height="80" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`

const FIREWALL_NODE = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="Firewall" style="shape=mxgraph.cisco.firewalls.firewall;fillColor=#FDE68A;strokeColor=#B45309;" vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="120" height="80" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`

const AP_NODE = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="Wireless AP" style="shape=mxgraph.cisco.wireless.access_point;fillColor=#DBEAFE;strokeColor=#2563EB;" vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="100" height="100" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`

const EDGE_WITH_START_ARROW = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="X" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="50" y="50" width="80" height="40" as="geometry"/>
    </mxCell>
    <mxCell id="3" value="Y" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="250" y="50" width="80" height="40" as="geometry"/>
    </mxCell>
    <mxCell id="4" style="startArrow=diamond;endArrow=block;" edge="1" source="2" target="3" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`

const MULTI_EDGE_TRIANGLE = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="A" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="50" y="50" width="80" height="40" as="geometry"/>
    </mxCell>
    <mxCell id="3" value="B" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="250" y="50" width="80" height="40" as="geometry"/>
    </mxCell>
    <mxCell id="4" value="C" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="150" y="200" width="80" height="40" as="geometry"/>
    </mxCell>
    <mxCell id="5" style="endArrow=block;" edge="1" source="2" target="3" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
    <mxCell id="6" style="endArrow=block;" edge="1" source="3" target="4" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
    <mxCell id="7" style="endArrow=block;" edge="1" source="4" target="2" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`

const MODULE_WITH_CHILD = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="Module" style="rounded=1;fillColor=#F8FAFC;strokeColor=#E2E8F0;" vertex="1" parent="1">
      <mxGeometry x="20" y="20" width="300" height="200" as="geometry"/>
    </mxCell>
    <mxCell id="3" value="Child Node" style="rounded=1;fillColor=#DBEAFE;strokeColor=#2563EB;" vertex="1" parent="2">
      <mxGeometry x="40" y="60" width="120" height="60" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`

const EDGE_WITH_OPEN_ARROW = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="X" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="50" y="50" width="80" height="40" as="geometry"/>
    </mxCell>
    <mxCell id="3" value="Y" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="250" y="50" width="80" height="40" as="geometry"/>
    </mxCell>
    <mxCell id="4" style="endArrow=open;" edge="1" source="2" target="3" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`

const EDGE_WITH_NO_ARROW = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="P" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="50" y="50" width="80" height="40" as="geometry"/>
    </mxCell>
    <mxCell id="3" value="Q" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="250" y="50" width="80" height="40" as="geometry"/>
    </mxCell>
    <mxCell id="4" style="endArrow=none;" edge="1" source="2" target="3" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`

const EDGE_WITH_LABEL = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="Src" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="50" y="50" width="80" height="40" as="geometry"/>
    </mxCell>
    <mxCell id="3" value="Dst" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="250" y="50" width="80" height="40" as="geometry"/>
    </mxCell>
    <mxCell id="4" value="connects" style="endArrow=block;" edge="1" source="2" target="3" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`

// ============================================================================
// Tests
// ============================================================================

describe('drawioToSvg', () => {
  it('should convert basic 2-node 1-edge diagram to SVG', () => {
    const svg = drawioToSvg(BASIC_TWO_NODES_ONE_EDGE)
    assert.ok(svg.startsWith('<svg'), 'Output should start with <svg')
    assert.ok(svg.includes('</svg>'), 'Output should contain closing </svg>')
  })

  it('should render service node as rounded rect', () => {
    const svg = drawioToSvg(SERVICE_NODE)
    assert.ok(svg.includes('<rect'), 'Service node should produce a <rect element')
    assert.ok(svg.includes('rx='), 'Rounded rect should have rx attribute')
  })

  it('should render database shape as cylinder with ellipse', () => {
    const svg = drawioToSvg(DATABASE_NODE)
    assert.ok(
      svg.includes('<ellipse') || svg.includes('<path'),
      'Database/cylinder shape should contain <ellipse or <path'
    )
  })

  it('should render edges between nodes', () => {
    const svg = drawioToSvg(BASIC_TWO_NODES_ONE_EDGE)
    assert.ok(
      svg.includes('<line') || svg.includes('<path'),
      'Edge should produce a <line or <path element'
    )
  })

  it('should include arrow marker definition for endArrow=block', () => {
    const svg = drawioToSvg(EDGE_WITH_BLOCK_ARROW)
    assert.ok(svg.includes('<marker'), 'Output should contain <marker for arrow definition')
    assert.ok(svg.includes('arrow-block'), 'Output should contain arrow-block marker id')
  })

  it('should render text label from node value', () => {
    const svg = drawioToSvg(NODE_WITH_LABEL)
    assert.ok(svg.includes('>Hello<'), 'Output should contain the label text Hello')
  })

  it('should apply fill color from style', () => {
    const svg = drawioToSvg(NODE_WITH_COLOR)
    assert.ok(
      svg.includes('fill="#FF0000"') || svg.includes('fill: #FF0000'),
      'Output should contain the fill color #FF0000'
    )
  })

  it('should render UML class compartments without showing HTML tags', () => {
    const svg = drawioToSvg(UML_CLASS_NODE)
    assert.ok(svg.includes('>Customer<'), 'UML class title should render as text')
    assert.ok(svg.includes('>+ id: String<'), 'UML attributes should render as text')
    assert.ok(svg.includes('>+ placeOrder(): Order<'), 'UML operations should render as text')
    assert.ok(!svg.includes('&lt;hr'), 'UML class should not expose escaped HTML separators')
    assert.ok(!svg.includes('<hr'), 'UML class should not expose raw HTML separators')

    const horizontalLines = svg.match(/<line x1="100" y1="(?:128|170)" x2="280" y2="(?:128|170)"/g) || []
    assert.strictEqual(horizontalLines.length, 2, 'UML class should render two compartment divider lines')
  })

  it('should render UML stereotypes and generic type text as normal SVG text', () => {
    const xml = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="&lt;div style=&quot;text-align:center&quot;&gt;&amp;lt;&amp;lt;interface&amp;gt;&amp;gt;&lt;br/&gt;&lt;b&gt;Repository&lt;/b&gt;&lt;/div&gt;&lt;hr size=&quot;1&quot;/&gt;+ find(): List&amp;lt;Order&amp;gt;" style="umlClass=1;rounded=0;html=1;whiteSpace=wrap;overflow=fill;fillColor=#FFFFFF;strokeColor=#000000;fontColor=#000000;fontSize=12;fontFamily=Helvetica;verticalAlign=top;align=left;" vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="180" height="100" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`
    const svg = drawioToSvg(xml)
    assert.ok(svg.includes('&lt;&lt;interface&gt;&gt;'), 'Stereotype should be escaped for XML and display as <<interface>>')
    assert.ok(svg.includes('List&lt;Order&gt;'), 'Generic type should be escaped for XML and display as List<Order>')
    assert.ok(!svg.includes('&amp;lt;&amp;lt;interface'), 'Stereotype should not remain double-escaped')
  })

  it('should embed original XML as data-drawio attribute', () => {
    const svg = drawioToSvg(SERVICE_NODE)
    assert.ok(svg.includes('data-drawio='), 'Output should contain data-drawio attribute')
  })

  it('should throw on empty input', () => {
    assert.throws(() => drawioToSvg(''), Error, 'Empty string should throw Error')
    assert.throws(() => drawioToSvg('   '), Error, 'Whitespace-only string should throw Error')
  })

  it('should include marker-start when startArrow is specified', () => {
    const svg = drawioToSvg(EDGE_WITH_START_ARROW)
    assert.ok(
      svg.includes('marker-start'),
      'Output should contain marker-start attribute when startArrow is set'
    )
  })

  // --- Multi-edge and arrow type tests ---

  it('should render multi-edge diagram with 3+ edges', () => {
    const svg = drawioToSvg(MULTI_EDGE_TRIANGLE)
    assert.ok(svg.startsWith('<svg'), 'Output should start with <svg')
    const edgeMatches = svg.match(/<(line|path)\b/g) || []
    assert.ok(edgeMatches.length >= 3, `Should have at least 3 edge elements, found ${edgeMatches.length}`)
  })

  it('should render module/container and child node', () => {
    const svg = drawioToSvg(MODULE_WITH_CHILD)
    assert.ok(svg.includes('Module'), 'Output should contain module label')
    assert.ok(svg.includes('Child Node'), 'Output should contain child node label')
  })

  it('should handle open arrow type', () => {
    const svg = drawioToSvg(EDGE_WITH_OPEN_ARROW)
    assert.ok(svg.includes('<marker'), 'Output should contain marker for arrow')
    assert.ok(svg.includes('arrow-open'), 'Output should contain arrow-open marker id')
  })

  it('should handle none arrow type (no marker-end)', () => {
    const svg = drawioToSvg(EDGE_WITH_NO_ARROW)
    assert.ok(svg.startsWith('<svg'), 'Output should start with <svg')
    assert.ok(!svg.includes('marker-end'), 'endArrow=none should not produce marker-end attribute')
  })

  it('should render hollow UML triangle and diamond markers when fill is disabled', () => {
    const xml = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="Child" style="umlClass=1;rounded=0;" vertex="1" parent="1">
      <mxGeometry x="50" y="50" width="120" height="60" as="geometry"/>
    </mxCell>
    <mxCell id="3" value="Parent" style="umlClass=1;rounded=0;" vertex="1" parent="1">
      <mxGeometry x="250" y="50" width="120" height="60" as="geometry"/>
    </mxCell>
    <mxCell id="4" style="endArrow=block;endFill=0;startArrow=diamond;startFill=0;" edge="1" source="2" target="3" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`
    const svg = drawioToSvg(xml)
    assert.ok(svg.includes('id="arrow-block-open"'), 'Hollow triangle marker should be defined')
    assert.ok(svg.includes('id="arrow-diamond-open"'), 'Hollow diamond marker should be defined')
    assert.ok(svg.includes('marker-end="url(#arrow-block-open)"'), 'endFill=0 should select hollow triangle')
    assert.ok(svg.includes('marker-start="url(#arrow-diamond-open)"'), 'startFill=0 should select hollow diamond')
  })

  it('should normalize diamondThin markers to hollow diamond markers', () => {
    const xml = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="Whole" style="umlClass=1;rounded=0;" vertex="1" parent="1">
      <mxGeometry x="50" y="50" width="120" height="60" as="geometry"/>
    </mxCell>
    <mxCell id="3" value="Part" style="umlClass=1;rounded=0;" vertex="1" parent="1">
      <mxGeometry x="250" y="50" width="120" height="60" as="geometry"/>
    </mxCell>
    <mxCell id="4" style="startArrow=diamondThin;startFill=0;endArrow=diamondThin;endFill=0;" edge="1" source="2" target="3" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`
    const svg = drawioToSvg(xml)
    assert.ok(svg.includes('marker-start="url(#arrow-diamond-open)"'), 'diamondThin start arrow should render as hollow diamond')
    assert.ok(svg.includes('marker-end="url(#arrow-diamond-open)"'), 'diamondThin end arrow should render as hollow diamond')
    assert.ok(!svg.includes('url(#arrow-block)'), 'diamondThin should not fall back to block arrow')
  })

  it('should render edge label text', () => {
    const svg = drawioToSvg(EDGE_WITH_LABEL)
    assert.ok(svg.includes('>connects<'), 'Output should contain the edge label text')
  })

  it('should render edge strokes before vertices so nodes cover crossing lines', () => {
    const svg = drawioToSvg(BASIC_TWO_NODES_ONE_EDGE)
    const edgeIndex = svg.indexOf('<line')
    const vertexIndex = svg.indexOf('<rect')
    assert.ok(edgeIndex !== -1, 'Output should contain an edge line')
    assert.ok(vertexIndex !== -1, 'Output should contain a vertex rect')
    assert.ok(edgeIndex < vertexIndex, 'Edge strokes should be rendered before vertices')
  })

  it('should apply edge label offset from geometry', () => {
    const xml = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="Src" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="50" y="50" width="80" height="40" as="geometry"/>
    </mxCell>
    <mxCell id="3" value="Dst" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="250" y="50" width="80" height="40" as="geometry"/>
    </mxCell>
    <mxCell id="4" value="connects" style="endArrow=block;fontColor=#64748B;fontSize=11;" edge="1" source="2" target="3" parent="1">
      <mxGeometry x="0.5" relative="1" as="geometry">
        <mxPoint x="0" y="-12" as="offset"/>
      </mxGeometry>
    </mxCell>
  </root>
</mxGraphModel>`
    const svg = drawioToSvg(xml)
    assert.ok(
      svg.includes('<text x="190" y="58"'),
      'Edge label should use midpoint plus the y=-12 offset'
    )
  })

  it('should apply edge label offset regardless of mxPoint attribute order', () => {
    const xml = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="Src" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="50" y="50" width="80" height="40" as="geometry"/>
    </mxCell>
    <mxCell id="3" value="Dst" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="250" y="50" width="80" height="40" as="geometry"/>
    </mxCell>
    <mxCell id="4" value="connects" style="endArrow=block;fontColor=#64748B;fontSize=11;" edge="1" source="2" target="3" parent="1">
      <mxGeometry x="0.5" relative="1" as="geometry">
        <mxPoint as="offset" x="0" y="-12"/>
      </mxGeometry>
    </mxCell>
  </root>
</mxGraphModel>`
    const svg = drawioToSvg(xml)
    assert.ok(
      svg.includes('<text x="190" y="58"'),
      'Edge label should use offset even when as="offset" is the first attribute'
    )
  })

  it('should apply connection point dx and dy offsets', () => {
    const xml = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="Src" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="50" y="50" width="80" height="40" as="geometry"/>
    </mxCell>
    <mxCell id="3" value="Dst" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="250" y="50" width="80" height="40" as="geometry"/>
    </mxCell>
    <mxCell id="4" style="endArrow=block;exitX=1;exitY=0.5;exitDx=10;exitDy=5;entryX=0;entryY=0.5;entryDx=-10;entryDy=-5;" edge="1" source="2" target="3" parent="1">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`
    const svg = drawioToSvg(xml)
    assert.ok(
      svg.includes('<line x1="140" y1="75" x2="240" y2="65"'),
      'Connection point dx/dy offsets should be applied to SVG edge endpoints'
    )
  })

  it('should expand the SVG bounds to include edge waypoints', () => {
    const xml = `
<mxGraphModel pageWidth="100" pageHeight="100">
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="A" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="10" y="10" width="40" height="30" as="geometry"/>
    </mxCell>
    <mxCell id="3" value="B" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="60" y="10" width="40" height="30" as="geometry"/>
    </mxCell>
    <mxCell id="4" style="endArrow=block;" edge="1" source="2" target="3" parent="1">
      <mxGeometry relative="1" as="geometry">
        <Array as="points">
          <mxPoint x="1000" y="1000"/>
        </Array>
      </mxGeometry>
    </mxCell>
  </root>
</mxGraphModel>`
    const svg = drawioToSvg(xml)
    const width = Number(svg.match(/width="(\d+(?:\.\d+)?)"/)[1])
    const height = Number(svg.match(/height="(\d+(?:\.\d+)?)"/)[1])
    assert.ok(width >= 1020, `SVG width should include waypoint bounds, got ${width}`)
    assert.ok(height >= 1020, `SVG height should include waypoint bounds, got ${height}`)
  })

  it('should expand the SVG viewBox to include negative edge waypoints', () => {
    const xml = `
<mxGraphModel pageWidth="100" pageHeight="100">
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="A" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="10" y="10" width="40" height="30" as="geometry"/>
    </mxCell>
    <mxCell id="3" value="B" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="60" y="10" width="40" height="30" as="geometry"/>
    </mxCell>
    <mxCell id="4" style="endArrow=block;" edge="1" source="2" target="3" parent="1">
      <mxGeometry relative="1" as="geometry">
        <Array as="points">
          <mxPoint x="-200" y="-100"/>
        </Array>
      </mxGeometry>
    </mxCell>
  </root>
</mxGraphModel>`
    const svg = drawioToSvg(xml)
    const [, minX, minY, width, height] = svg.match(/viewBox="(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?) (\d+(?:\.\d+)?) (\d+(?:\.\d+)?)"/).map(Number)
    assert.ok(minX <= -200, `viewBox minX should include negative waypoint, got ${minX}`)
    assert.ok(minY <= -100, `viewBox minY should include negative waypoint, got ${minY}`)
    assert.ok(width >= 320, `viewBox width should include negative waypoint span, got ${width}`)
    assert.ok(height >= 220, `viewBox height should include negative waypoint span, got ${height}`)
  })

  // --- Shape type rendering tests ---

  it('should render rhombus shape as <polygon>', () => {
    const xml = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="Decision" style="rhombus;fillColor=#FEF9C3;strokeColor=#CA8A04;" vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="120" height="80" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`
    const svg = drawioToSvg(xml)
    assert.ok(svg.includes('<polygon'), 'Rhombus shape should render as <polygon>')
  })

  it('should render ellipse shape as <ellipse>', () => {
    const xml = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="Oval" style="ellipse;fillColor=#DBEAFE;strokeColor=#2563EB;" vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="120" height="80" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`
    const svg = drawioToSvg(xml)
    assert.ok(svg.includes('<ellipse'), 'Ellipse shape should render as <ellipse>')
  })

  it('should render roundedRect with rx attribute on <rect>', () => {
    const xml = `
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="Box" style="rounded=1;fillColor=#DBEAFE;strokeColor=#2563EB;" vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="120" height="60" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`
    const svg = drawioToSvg(xml)
    assert.ok(svg.includes('<rect'), 'Rounded rect should render as <rect>')
    assert.ok(svg.includes('rx='), 'Rounded rect should have rx attribute')
  })

  it('should render cylinder shape with <ellipse> elements', () => {
    const svg = drawioToSvg(DATABASE_NODE)
    assert.ok(svg.includes('<ellipse'), 'Cylinder shape should contain <ellipse> for top/bottom caps')
  })

  it('should render switch shapes without falling back to plain rectangles', () => {
    const svg = drawioToSvg(SWITCH_NODE)
    assert.ok(svg.includes('<path'), 'Switch shape should render as a path-based stencil')
  })

  it('should render load balancer hexagons as polygons', () => {
    const svg = drawioToSvg(LOAD_BALANCER_NODE)
    assert.ok(svg.includes('<polygon'), 'Hexagon shape should render as <polygon>')
  })

  it('should render firewall stencils as composed SVG shapes', () => {
    const svg = drawioToSvg(FIREWALL_NODE)
    const pathCount = (svg.match(/<path\b/g) || []).length
    assert.ok(pathCount >= 2, 'Firewall shape should render multiple path elements')
  })

  it('should render wireless access points with antenna arcs', () => {
    const svg = drawioToSvg(AP_NODE)
    assert.ok(svg.includes('<ellipse'), 'AP shape should include a base ellipse')
    assert.ok(svg.includes('<path'), 'AP shape should include antenna arcs')
  })

  it('should render edge as <line> element', () => {
    const svg = drawioToSvg(EDGE_WITH_BLOCK_ARROW)
    assert.ok(svg.includes('<line'), 'Edge should render as <line> element')
  })

  it('should render background color from graph attributes', () => {
    const xml = `
<mxGraphModel background="#E0F2FE">
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="Node" style="rounded=1;" vertex="1" parent="1">
      <mxGeometry x="50" y="50" width="100" height="50" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>`
    const svg = drawioToSvg(xml)
    assert.ok(
      svg.includes('fill="#E0F2FE"'),
      'Background color should render as <rect> with matching fill'
    )
  })

  it('should throw Error on null input', () => {
    assert.throws(() => drawioToSvg(null), Error, 'null input should throw Error')
  })

  it('should throw Error on non-string input', () => {
    assert.throws(() => drawioToSvg(42), Error, 'Number input should throw Error')
    assert.throws(() => drawioToSvg(undefined), Error, 'undefined input should throw Error')
  })
})
