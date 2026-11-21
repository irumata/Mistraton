// Pixel-art brand components in Mistral AI style (crisp squares, yellow->red gradient).

// The Mistral "M" as a 5x5 pixel grid; each row takes the next brand color (top yellow -> bottom red).
const M_ROWS = [
  [1, 0, 0, 0, 1],
  [1, 1, 0, 1, 1],
  [1, 0, 1, 0, 1],
  [1, 0, 0, 0, 1],
  [1, 0, 0, 0, 1],
]
const M_COLORS = ['#FFD800', '#FFAF00', '#FF8205', '#FA500F', '#E10500']

export function MistralPixelLogo({ size = 40, title = 'Mistral' }) {
  const cell = 8
  const w = M_ROWS[0].length * cell
  const h = M_ROWS.length * cell
  return (
    <svg
      className="px-logo"
      width={size}
      height={(size * h) / w}
      viewBox={`0 0 ${w} ${h}`}
      shapeRendering="crispEdges"
      role="img"
      aria-label={title}
      style={{ imageRendering: 'pixelated' }}
    >
      <rect width={w} height={h} fill="#0b0b0b" />
      {M_ROWS.map((row, y) =>
        row.map((v, x) =>
          v ? <rect key={`${x}-${y}`} x={x * cell} y={y * cell} width={cell} height={cell} fill={M_COLORS[y]} /> : null,
        ),
      )}
    </svg>
  )
}

// Pixel shield used for the SAFETRAL (Shieldstral) guard layer.
const SHIELD = [
  [1, 1, 1, 1, 1, 1, 1],
  [1, 1, 1, 1, 1, 1, 1],
  [1, 1, 1, 1, 1, 1, 1],
  [1, 1, 1, 1, 1, 1, 1],
  [1, 1, 1, 1, 1, 1, 1],
  [0, 1, 1, 1, 1, 1, 0],
  [0, 0, 1, 1, 1, 0, 0],
  [0, 0, 0, 1, 0, 0, 0],
]

export function SafetralShield({ size = 18, color = '#2f9e44', title = 'SAFETRAL verified' }) {
  const cell = 4
  const w = SHIELD[0].length * cell
  const h = SHIELD.length * cell
  return (
    <svg
      className="px-shield"
      width={size}
      height={(size * h) / w}
      viewBox={`0 0 ${w} ${h}`}
      shapeRendering="crispEdges"
      role="img"
      aria-label={title}
      title={title}
      style={{ imageRendering: 'pixelated' }}
    >
      {SHIELD.map((row, y) =>
        row.map((v, x) =>
          v ? (
            <rect key={`${x}-${y}`} x={x * cell} y={y * cell} width={cell} height={cell} fill={color} stroke="#0b0b0b" strokeWidth="0.6" />
          ) : null,
        ),
      )}
    </svg>
  )
}

// Pixel check / cross marks for chain stages.
export function PixelMark({ ok, size = 12 }) {
  const cell = 4
  const grid = ok
    ? [[0,0,0,0,0,1,1],[0,0,0,0,1,0,1],[1,0,0,1,0,0,1],[1,1,1,0,0,0,0],[0,1,1,0,0,0,0],[0,0,1,1,0,0,0]]
    : [[1,0,0,0,0,1],[0,1,0,0,1,0],[0,0,1,1,0,0],[0,0,1,1,0,0],[0,1,0,0,1,0],[1,0,0,0,0,1]]
  const w = grid[0].length * cell
  const h = grid.length * cell
  return (
    <svg width={size} height={(size * h) / w} viewBox={`0 0 ${w} ${h}`} shapeRendering="crispEdges" aria-hidden="true">
      {grid.map((row, y) =>
        row.map((v, x) =>
          v ? <rect key={`${x}-${y}`} x={x * cell} y={y * cell} width={cell} height={cell} fill={ok ? '#1a7f37' : '#E10500'} /> : null,
        ),
      )}
    </svg>
  )
}
