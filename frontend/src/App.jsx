import { useEffect, useState } from 'react'
import './App.css'
import UploadPanel from './panels/UploadPanel.jsx'
import PipelinePanel from './panels/PipelinePanel.jsx'
import WatchPanel from './panels/WatchPanel.jsx'
import TimestralPanel from './panels/TimestralPanel.jsx'
import { MistralPixelLogo } from './components/pixel.jsx'

async function check(path, signal) {
  const response = await fetch(path, { signal, cache: 'no-store' })
  if (!response.ok) throw new Error(`Request failed (${response.status})`)
  const data = await response.json()
  if (data.status !== 'ok') throw new Error('Service is not ready')
  return data
}

const TABS = ['Status', 'Upload', 'Pipeline', 'Watch', 'Timestral']

function StatusCard() {
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState({ loading: true, api: false, db: false })

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 8000)
    Promise.allSettled([
      check('/api/health', controller.signal),
      check('/api/db', controller.signal),
    ]).then(([api, db]) => {
      clearTimeout(timeout)
      if (active) setState({ loading: false, api: api.status === 'fulfilled', db: db.status === 'fulfilled' })
    })
    return () => { active = false; clearTimeout(timeout); controller.abort() }
  }, [attempt])

  const ready = state.api && state.db
  return (
    <section className="status-card" aria-label="Service status">
      <div className="card-top"><h2>Project status</h2><span className={`badge ${ready ? 'ready' : ''}`}>{state.loading ? 'Checking…' : ready ? 'All systems ready' : 'Needs attention'}</span></div>
      <div className="service"><span>Application</span><span className={state.api ? 'online' : 'muted'}>{state.loading ? 'Checking…' : state.api ? 'Online' : 'Unavailable'}</span></div>
      <div className="service"><span>Database</span><span className={state.db ? 'online' : 'muted'}>{state.loading ? 'Checking…' : state.db ? 'Connected' : 'Unavailable'}</span></div>
      <p className="status-note" role="status">{state.loading ? 'Checking the live services.' : ready ? 'Everything is connected. You’re ready to start.' : 'A service could not be reached. Try checking again in a moment.'}</p>
      <button type="button" disabled={state.loading} onClick={() => { setState({ loading: true, api: false, db: false }); setAttempt(value => value + 1) }}>Check again <span aria-hidden="true">↗</span></button>
    </section>
  )
}

export default function App() {
  const [tab, setTab] = useState('Status')
  return (
    <main>
      <header>
        <a className="brand" href="/" aria-label="Mistralethon home"><MistralPixelLogo size={44} title="Mistral" /></a>
        <span className="wordmark">Mistralethon</span>
        <span className="safetral-badge" title="Shieldstral guards every alert: labeling, PII filtering, verification">
          <img className="safetral-mark" src="/safetral-mark.png" alt="Safetral logo" width="22" height="24" />
          <span>Safetral<small>shieldstral guard</small></span>
        </span>
      </header>
      <nav className="tabs">
        {TABS.map(t => (
          <button key={t} className={tab === t ? 'tab active' : 'tab'} onClick={() => setTab(t)}>{t}</button>
        ))}
      </nav>
      {tab === 'Status' && (
        <>
          <section className="intro">
            <img className="safetral-intro-logo" src="/safetral-logo.png" alt="Safetral — Shieldstral guard" width="760" height="460" />
            <p className="eyebrow">A small beginning. A big possibility.</p>
            <h1>Sentinel-Loop<br /><em>detect. guard. explain.</em></h1>
            <p className="lede">
              Upload incident data, extract metrics with Mistral Large, label windows with Safetral (Shieldstral),
              tune Timestral — then watch live streams through the chain:
              <b> Timestral detects → Safetral verifies → Large explains</b>.
            </p>
          </section>
          <StatusCard />
        </>
      )}
      {tab === 'Upload' && <UploadPanel />}
      {tab === 'Pipeline' && <PipelinePanel />}
      {tab === 'Watch' && <WatchPanel />}
      {tab === 'Timestral' && <TimestralPanel />}
      <footer><span>Mistralethon</span><span>Built to begin.</span></footer>
    </main>
  )
}
