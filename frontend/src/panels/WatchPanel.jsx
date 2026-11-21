import { useEffect, useState } from 'react'
import { api, apiPost } from '../api.js'
import { SafetralShield, PixelMark } from '../components/pixel.jsx'

// parse "ts,value" lines; if the line is just a number, synthesize 1m-spaced timestamps
function parsePoints(text) {
  const base = new Date('2026-01-01T00:00:00Z').getTime()
  return text.split('\n').map(l => l.trim()).filter(Boolean).map((l, i) => {
    const [a, b] = l.split(',')
    if (b !== undefined) return { ts: new Date(a.trim()).toISOString(), value: Number(b.trim()) }
    return { ts: new Date(base + i * 60 * 1000).toISOString(), value: Number(a.trim()) }
  })
}

const STAGE_LABEL = { timstral: '1 · Timestral', shieldstral: '2 · Safetral', large: '3 · Large' }

function fmtTime(iso) {
  try { return new Date(iso).toISOString().slice(11, 19) + 'Z' } catch { return iso }
}

function SafetralTag({ verified }) {
  if (verified === true) {
    return <span className="safetral-tag verified"><SafetralShield size={16} color="#1a7f37" title="SAFETRAL verified" /> Safetral ✓</span>
  }
  if (verified === false) {
    return <span className="safetral-tag rejected"><SafetralShield size={16} color="#E10500" title="SAFETRAL rejected" /> Safetral ✗</span>
  }
  return <span className="safetral-tag shadow"><SafetralShield size={16} color="#8a8272" title="shadow mode" /> shadow</span>
}

function ChainRow({ chain }) {
  const ev = chain.event
  return (
    <div className="chain-row">
      <div className="chain-head">
        <span className="metric">{ev.metric}</span>
        <span className="win">window {fmtTime(ev.window_start)} → {fmtTime(ev.window_end)}</span>
        <span className="win">state={ev.window_state} conf={ev.model_confidence?.toFixed(2)} lead={ev.lead_time_minutes}min</span>
      </div>
      <div className="chain">
        {chain.stages.map((s, i) => (
          <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            {i > 0 && <span className="chip arrow" aria-hidden="true">→</span>}
            <span className={`chip ${s.passed ? 'ok' : 'fail'}`} title={`${s.model} — ${s.detail} (${s.seconds.toFixed(3)}s)`}>
              <PixelMark ok={s.passed} size={11} /> {STAGE_LABEL[s.stage] || s.stage}
            </span>
          </span>
        ))}
        <span className={`outcome ${chain.fired ? 'fired' : chain.suppressed ? 'suppressed' : 'clear'}`}>
          {chain.fired ? '▶ fired' : chain.suppressed ? '✕ suppressed' : '· clear'}
        </span>
      </div>
    </div>
  )
}

function AlertCard({ alert }) {
  const ev = alert.event
  return (
    <div className="alert-card">
      <div className="alert-top">
        <span className="aid">ALERT {alert.alert_id}</span>
        <span className="alert-meta">{ev.metric} · {ev.window_state} · conf {ev.model_confidence?.toFixed(2)} · {fmtTime(ev.window_start)} · lead {ev.lead_time_minutes}min</span>
        <SafetralTag verified={alert.verified} />
      </div>
      {alert.explanation && (
        <div className="console" role="log" aria-label="Mistral Large explanation (as printed by the watcher)">
          <span className="c-head">{'// printed by sentinel-loop watcher'}</span>{'\n'}{alert.explanation}
        </div>
      )}
    </div>
  )
}

export default function WatchPanel() {
  const [metrics, setMetrics] = useState([])
  const [metric, setMetric] = useState('')
  const [text, setText] = useState('10,10,11,10,10,11,10,10,25,60,120,300,600,900,400,150')
  const [shadow, setShadow] = useState(true)
  const [res, setRes] = useState(null)
  const [alerts, setAlerts] = useState([])
  const [error, setError] = useState('')
  const [incidents, setIncidents] = useState([])
  const [demoIncident, setDemoIncident] = useState('')
  const [demoScope, setDemoScope] = useState('incident')
  const [demoMax, setDemoMax] = useState(80)
  const [demoRes, setDemoRes] = useState(null)
  const [demoBusy, setDemoBusy] = useState(false)

  async function refresh() {
    try {
      const d = await api('/sentinel/data')
      setMetrics(d.metrics)
      if (!metric && d.metrics.length) setMetric(d.metrics[0])
      setIncidents(d.records.map(r => r.incident_id))
      const a = await api('/sentinel/alerts')
      setAlerts(a.alerts)
    } catch (e) { setError(e.message) }
  }

  async function runDemo() {
    setDemoBusy(true); setError(''); setDemoRes(null)
    try {
      const r = await apiPost('/sentinel/demo', {
        incident_id: demoIncident || null,
        shadow,
        max_windows: demoMax,
        window_scope: demoScope,
      })
      setDemoRes(r)
      await refresh()
    } catch (e) { setError(e.message) } finally { setDemoBusy(false) }
  }

  useEffect(() => { refresh() }, []) // eslint-disable-line

  async function send() {
    setError('')
    try {
      const streams = { [metric]: parsePoints(text) }
      const r = await apiPost('/sentinel/watch', { streams, shadow })
      setRes(r)
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
      <h2>Watch live streams — detection chain</h2>
      <p className="muted">
        Every window runs the fixed chain: <b>Timestral detects → Safetral (Shieldstral) verifies → Large explains (printed)</b>.
        Safetral-rejected candidates are suppressed as false positives.
      </p>

      <h3>Demo — replay stored incident data through the chain</h3>
      <p className="muted">
        Runs the windows of a stored incident bundle (from <code>raw/</code>) through the same fixed chain:
        see Timestral raise the alarm, Safetral suppress the false positives, and Large 4 print the explanation.
      </p>
      <div className="row">
        <select value={demoIncident} onChange={e => setDemoIncident(e.target.value)}>
          <option value="">all incidents</option>
          {incidents.map(i => <option key={i} value={i}>{i}</option>)}
        </select>
        <select value={demoScope} onChange={e => setDemoScope(e.target.value)} title="window scope">
          <option value="incident">around incident</option>
          <option value="full">full series (hard negatives)</option>
        </select>
        <label className="muted">max windows/metric
          <input type="number" min="5" max="2000" value={demoMax} style={{ width: 70 }}
            onChange={e => setDemoMax(Number(e.target.value))} />
        </label>
        <label><input type="checkbox" checked={shadow} onChange={e => setShadow(e.target.checked)} /> shadow mode</label>
        <button onClick={runDemo} disabled={demoBusy}>{demoBusy ? 'Running…' : '▶ Run demo'}</button>
      </div>
      {demoRes && (
        <>
          <div className="summary-strip">
            <span className="stat events"><b>{demoRes.totals.windows_evaluated}</b>windows</span>
            <span className="stat events"><b>{demoRes.totals.detection_events}</b>timestral events</span>
            <span className="stat alerts"><b>{demoRes.totals.alerts_fired}</b>alerts fired</span>
            <span className="stat suppressed"><b>{demoRes.totals.suppressed_by_shieldstral}</b>suppressed (safetral)</span>
            <span className="stat"><b>{demoRes.shadow ? 'SHADOW' : 'LIVE'}</b> · {demoRes.window_scope}</span>
          </div>
          {demoRes.incidents.map(inc => (
            <div key={inc.incident_id}>
              <h3 className="demo-inc">{inc.incident_id} — {inc.metrics.join(', ') || 'no metrics'}</h3>
              {(!inc.chains || inc.chains.length === 0) && <p className="muted">no candidates in evaluated windows</p>}
              {inc.chains.slice(-8).map((c, i) => <ChainRow key={i} chain={c} />)}
              {inc.alerts.slice(-3).map(a => <AlertCard key={a.alert_id} alert={a} />)}
            </div>
          ))}
        </>
      )}

      <h3>Live stream</h3>
      <div className="row">
        <select value={metric} onChange={e => setMetric(e.target.value)}>
          {metrics.length === 0 && <option value="">no metrics yet — run the pipeline</option>}
          {metrics.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <label><input type="checkbox" checked={shadow} onChange={e => setShadow(e.target.checked)} /> shadow mode</label>
        <button onClick={send} disabled={!metric}>▶ Run chain</button>
        <button className="ghost" onClick={refresh}>↻ Refresh alerts</button>
      </div>
      <textarea rows={3} value={text} onChange={e => setText(e.target.value)}
        placeholder={'one value per line, or "ISO-timestamp,value" per line'} />
      {error && <p className="error">{error}</p>}

      {res && (
        <>
          <div className="summary-strip">
            <span className="stat events"><b>{res.events.length}</b>events</span>
            <span className="stat alerts"><b>{res.alerts.length}</b>alerts fired</span>
            <span className="stat suppressed"><b>{res.suppressed}</b>suppressed (safetral)</span>
            <span className="stat"><b>{res.shadow ? 'SHADOW' : 'LIVE'}</b>mode</span>
          </div>

          <h3>Chain trace (per window)</h3>
          {(!res.chains || res.chains.length === 0) && <p className="muted">no windows evaluated</p>}
          {res.chains.slice(-10).map((c, i) => <ChainRow key={i} chain={c} />)}

          <h3>Fired alerts — Large explanation (printed)</h3>
          {res.alerts.length === 0 ? <p className="muted">none — Safetral suppressed everything or nothing fired</p> : (
            res.alerts.slice(-6).map(a => <AlertCard key={a.alert_id} alert={a} />)
          )}
        </>
      )}

      <h3>Stored alerts</h3>
      {alerts.length === 0 ? <p className="muted">none</p> : (
        <table>
          <thead><tr><th>alert</th><th>metric</th><th>state</th><th>conf</th><th>safetral</th><th>explanation</th><th></th></tr></thead>
          <tbody>
            {alerts.map(a => (
              <tr key={a.alert_id}>
                <td>{a.alert_id}</td>
                <td>{a.event.metric}</td>
                <td>{a.event.window_state}</td>
                <td>{a.event.model_confidence?.toFixed(2)}</td>
                <td><SafetralTag verified={a.verified} /></td>
                <td>
                  {a.explanation
                    ? <details className="details-expl"><summary>view</summary><pre>{a.explanation}</pre></details>
                    : <span className="muted">—</span>}
                </td>
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
