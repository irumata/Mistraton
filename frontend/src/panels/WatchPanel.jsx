import { useEffect, useState } from 'react'
import { api, apiPost } from '../api.js'

// parse "ts,value" lines; if the line is just a number, synthesize hourly timestamps
function parsePoints(text) {
  const base = new Date('2026-01-01T00:00:00Z').getTime()
  return text.split('\n').map(l => l.trim()).filter(Boolean).map((l, i) => {
    const [a, b] = l.split(',')
    if (b !== undefined) return { ts: new Date(a.trim()).toISOString(), value: Number(b.trim()) }
    return { ts: new Date(base + i * 60 * 1000).toISOString(), value: Number(a.trim()) }
  })
}

export default function WatchPanel() {
  const [metrics, setMetrics] = useState([])
  const [metric, setMetric] = useState('')
  const [text, setText] = useState('10,10,11,10,10,11,10,10,25,60,120,300,600,900,400,150')
  const [shadow, setShadow] = useState(true)
  const [events, setEvents] = useState(null)
  const [alerts, setAlerts] = useState([])
  const [error, setError] = useState('')

  async function refresh() {
    try {
      const d = await api('/sentinel/data')
      setMetrics(d.metrics)
      if (!metric && d.metrics.length) setMetric(d.metrics[0])
      const a = await api('/sentinel/alerts')
      setAlerts(a.alerts)
    } catch (e) { setError(e.message) }
  }

  useEffect(() => { refresh() }, []) // eslint-disable-line

  async function send() {
    setError('')
    try {
      const streams = { [metric]: parsePoints(text) }
      const res = await apiPost('/sentinel/watch', { streams, shadow })
      setEvents(res)
      await refresh()
    } catch (e) {
      setError(e.message)
    }
  }

  async function feedback(alertId, decision) {
    try {
      await apiPost('/sentinel/feedback', { alert_id: alertId, decision, reviewer: 'web-ui' })
      await refresh()
    } catch (e) { setError(e.message) }
  }

  return (
    <section className="panel">
      <h2>Watch live streams</h2>
      <div className="row">
        <select value={metric} onChange={e => setMetric(e.target.value)}>
          {metrics.length === 0 && <option value="">no metrics yet — run the pipeline</option>}
          {metrics.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <label><input type="checkbox" checked={shadow} onChange={e => setShadow(e.target.checked)} /> shadow mode</label>
        <button onClick={send} disabled={!metric}>Send stream</button>
        <button className="ghost" onClick={refresh}>Refresh alerts</button>
      </div>
      <textarea rows={3} value={text} onChange={e => setText(e.target.value)}
        placeholder={'one value per line, or "ISO-timestamp,value" per line'} />
      {error && <p className="error">{error}</p>}
      {events && (
        <div>
          <h3>Events</h3>
          {events.events.length === 0 ? <p className="muted">no detection events</p> : (
            <pre>{JSON.stringify(events.events, null, 2)}</pre>
          )}
          <h3>Alerts fired</h3>
          {events.alerts.length === 0 ? <p className="muted">none</p> : (
            <pre>{JSON.stringify(events.alerts, null, 2)}</pre>
          )}
        </div>
      )}
      <h3>Stored alerts</h3>
      {alerts.length === 0 ? <p className="muted">none</p> : (
        <table>
          <thead><tr><th>alert</th><th>metric</th><th>state</th><th>conf</th><th>verified</th><th></th></tr></thead>
          <tbody>
            {alerts.map(a => (
              <tr key={a.alert_id}>
                <td>{a.alert_id}</td><td>{a.event.metric}</td><td>{a.event.window_state}</td>
                <td>{a.event.model_confidence?.toFixed(2)}</td><td>{a.verified === null ? 'shadow' : String(a.verified)}</td>
                <td>
                  <button onClick={() => feedback(a.alert_id, 'confirmed')}>confirm</button>
                  <button className="ghost" onClick={() => feedback(a.alert_id, 'rejected')}>reject</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
