import { useEffect, useRef, useState } from 'react'
import { formatNumber, formatTime } from './investigationFormat.js'

const COLORS = {
  light: { grid: '#e4e6e0', label: '#5f6760', forecast: '#49716c', band: '#e0eeea', observed: '#875111', peak: '#bb4322' },
  dark: { grid: '#353c3b', label: '#afb8b4', forecast: '#88a8a0', band: '#334744', observed: '#ffc368', peak: '#ff9875' },
}
const timeFormat = new Intl.DateTimeFormat(undefined, {
  hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'UTC',
})
const secondsFormat = new Intl.DateTimeFormat(undefined, {
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23', timeZone: 'UTC',
})
const millisecondsFormat = new Intl.DateTimeFormat(undefined, {
  minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3, timeZone: 'UTC',
})
const dateFormat = new Intl.DateTimeFormat(undefined, {
  month: 'short', day: 'numeric', timeZone: 'UTC',
})

function ranges(points, value) {
  const parts = []
  let previous = -Infinity
  points.forEach((row, index) => {
    if (value(row) === null) return
    if (index - previous > 3) parts.push([])
    parts.at(-1).push({ row, index })
    previous = index
  })
  return parts
}

function line(ctx, points, x, y, value) {
  points.forEach(({ row, index }, i) => ctx[i === 0 ? 'moveTo' : 'lineTo'](x(index), y(value(row))))
}

function draw(canvas, trace, candidates, end, cursor, dark) {
  const { width, height } = canvas.getBoundingClientRect()
  if (!width || !height) return
  const ratio = window.devicePixelRatio || 1
  canvas.width = Math.round(width * ratio)
  canvas.height = Math.round(height * ratio)
  const ctx = canvas.getContext('2d')
  ctx.scale(ratio, ratio)
  const colors = COLORS[dark ? 'dark' : 'light']
  const values = trace.flatMap(row => [row.observed, ...(row.band || [])]).filter(Number.isFinite)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const rawStep = (max - min || Math.abs(min) * .2 || 1) / 4
  const magnitude = 10 ** Math.floor(Math.log10(rawStep))
  const step = [1, 2, 2.5, 5, 10].find(unit => unit * magnitude >= rawStep) * magnitude
  const bottom = Math.floor(min / step) * step
  const top = Math.max(bottom + step, Math.ceil(max / step) * step)
  ctx.font = '12px ui-monospace, monospace'
  const left = Math.max(38, ctx.measureText(formatNumber(bottom)).width + 12, ctx.measureText(formatNumber(top)).width + 12)
  const right = width - 10
  const plotTop = 11
  const plotBottom = height - 27
  const x = index => left + (right - left) * index / Math.max(1, trace.length - 1)
  const y = value => plotBottom - (plotBottom - plotTop) * (value - bottom) / (top - bottom)

  ctx.textAlign = 'right'
  ctx.textBaseline = 'middle'
  ctx.fillStyle = colors.label
  ctx.strokeStyle = colors.grid
  for (let tick = 0; tick <= Math.round((top - bottom) / step); tick++) {
    const value = tick === 0 ? bottom : bottom + tick * step
    const offset = y(value)
    ctx.fillText(formatNumber(Math.abs(value) < step * 1e-9 ? 0 : value), left - 9, offset)
    ctx.beginPath()
    ctx.moveTo(left, offset)
    ctx.lineTo(right, offset)
    ctx.stroke()
  }

  const span = Date.parse(trace.at(-1).time) - Date.parse(trace[0].time)
  let tickFormat = timeFormat
  if (span < 1000) tickFormat = millisecondsFormat
  else if (span < 3600000) tickFormat = secondsFormat
  else if (span >= 86400000) tickFormat = dateFormat
  const timeTicks = Math.min(trace.length - 1, width < 360 ? 2 : 4)
  for (let tick = 0; tick <= timeTicks; tick++) {
    const index = Math.round(tick / Math.max(1, timeTicks) * (trace.length - 1))
    ctx.textAlign = tick === 0 ? 'left' : tick === timeTicks ? 'right' : 'center'
    ctx.fillText(tickFormat.format(new Date(trace[index].time)), x(index), height - 9)
  }

  const visible = trace.slice(0, end)
  for (const points of ranges(visible, row => row.band)) {
    ctx.fillStyle = colors.band
    ctx.beginPath()
    line(ctx, points, x, y, row => row.band[2])
    for (const { row, index } of [...points].reverse()) ctx.lineTo(x(index), y(row.band[0]))
    ctx.closePath()
    ctx.fill()
    ctx.strokeStyle = colors.forecast
    ctx.setLineDash([4, 5])
    ctx.beginPath()
    line(ctx, points, x, y, row => row.band[1])
    ctx.stroke()
    ctx.setLineDash([])
  }

  ctx.strokeStyle = colors.observed
  ctx.fillStyle = colors.observed
  ctx.lineWidth = 2
  ctx.lineJoin = 'round'
  for (const points of ranges(visible, row => row.observed)) {
    ctx.beginPath()
    line(ctx, points, x, y, row => row.observed)
    ctx.stroke()
    if (points.length === 1) {
      const [{ row, index }] = points
      ctx.fillRect(x(index) - 1, y(row.observed) - 1, 3, 3)
    }
  }

  const positions = new Map(visible.map((row, index) => [row.time, index]))
  for (const candidate of candidates) {
    const index = positions.get(candidate.peak.time)
    if (index === undefined) continue
    ctx.strokeStyle = colors.peak
    ctx.fillStyle = dark ? '#1d2322' : '#fff'
    ctx.beginPath()
    ctx.arc(x(index), y(candidate.peak.observed), 4.5, 0, 2 * Math.PI)
    ctx.fill()
    ctx.stroke()
  }

  if (cursor !== null) {
    ctx.lineWidth = 1
    ctx.strokeStyle = colors.label
    ctx.setLineDash([3, 4])
    ctx.beginPath()
    ctx.moveTo(x(cursor), plotTop)
    ctx.lineTo(x(cursor), plotBottom)
    ctx.stroke()
    ctx.setLineDash([])
  }
  return { left, right }
}

export default function ForecastChart({ trace, candidates, end = trace.length, following = false, preview = false, label, precise = false }) {
  const canvas = useRef(null)
  const axes = useRef(null)
  const [hover, setHover] = useState(null)
  const cursor = hover !== null && hover < end ? hover : null
  const point = trace[cursor ?? (following ? end - 1 : -1)]

  useEffect(() => {
    const element = canvas.current
    const redraw = () => { axes.current = draw(element, trace, candidates, end, cursor, preview) }
    const observer = new ResizeObserver(redraw)
    observer.observe(element)
    redraw()
    return () => observer.disconnect()
  }, [trace, candidates, end, cursor, preview])

  return (
    <figure className={preview ? 'forecast-chart forecast-chart-preview' : 'forecast-chart'}>
      <figcaption className="chart-legend">
        <span><i className="legend-observed" /> Observed</span>
        <span><i className="legend-band" /> Expected range</span>
        <span><i className="legend-peak" /> Candidate peak</span>
      </figcaption>
      <canvas ref={canvas} role="img" aria-label={`${label}, observed values and expected range over time in UTC.${preview ? '' : ' Use arrow keys to inspect observations.'}`}
        tabIndex={preview ? undefined : 0}
        onPointerMove={preview ? undefined : event => {
          if (!axes.current) return
          const { left, right } = axes.current
          const offset = event.clientX - event.currentTarget.getBoundingClientRect().left
          const index = Math.round((offset - left) / (right - left) * (trace.length - 1))
          setHover(Math.max(0, Math.min(end - 1, index)))
        }}
        onPointerLeave={preview ? undefined : () => setHover(null)}
        onBlur={preview ? undefined : () => setHover(null)}
        onKeyDown={preview ? undefined : event => {
          if (!['ArrowLeft', 'ArrowRight', 'Escape'].includes(event.key)) return
          event.preventDefault()
          if (event.key === 'Escape') setHover(null)
          else setHover(Math.max(0, Math.min(end - 1, (cursor ?? (event.key === 'ArrowLeft' ? end : -1))
            + (event.key === 'ArrowLeft' ? -1 : 1))))
        }}>
        Observed values and expected range over time.
      </canvas>
      {!preview && <p className="chart-readout" role={following ? undefined : 'status'}>
        {point ? `${formatTime(point.time, precise)} · ${point.observed === null ? 'Missing observation' : `Observed ${formatNumber(point.observed)}`}`
          + (point.band ? ` · Expected ${formatNumber(point.band[0])}–${formatNumber(point.band[2])}` : '')
          : `${formatTime(trace[0].time, precise)} to ${formatTime(trace.at(-1).time, precise)}`}
      </p>}
    </figure>
  )
}
