import { useEffect, useState } from 'react'
import './App.css'

async function check(path, signal) {
  const response = await fetch(path, { signal, cache: 'no-store' })
  if (!response.ok) throw new Error(`Request failed (${response.status})`)
  const data = await response.json()
  if (data.status !== 'ok') throw new Error('Service is not ready')
  return data
}

export default function App() {
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
    <main>
      <header><a className="brand" href="/">M<span>↗</span></a><span className="wordmark">Mistralethon</span></header>
      <section className="intro">
        <p className="eyebrow">A SMALL BEGINNING. A BIG POSSIBILITY.</p>
        <h1>Make something<br /><em>worth building.</em></h1>
        <p className="lede">Your next idea starts here. The essentials are connected, so you can focus on what comes next.</p>
      </section>
      <section className="status-card" aria-label="Service status">
        <div className="card-top"><h2>Project status</h2><span className={`badge ${ready ? 'ready' : ''}`}>{state.loading ? 'Checking…' : ready ? 'All systems ready' : 'Needs attention'}</span></div>
        <div className="service"><span>Application</span><span className={state.api ? 'online' : 'muted'}>{state.loading ? 'Checking…' : state.api ? 'Online' : 'Unavailable'}</span></div>
        <div className="service"><span>Database</span><span className={state.db ? 'online' : 'muted'}>{state.loading ? 'Checking…' : state.db ? 'Connected' : 'Unavailable'}</span></div>
        <p className="status-note" role="status">{state.loading ? 'Checking the live services.' : ready ? 'Everything is connected. You’re ready to start.' : 'A service could not be reached. Try checking again in a moment.'}</p>
        <button type="button" disabled={state.loading} onClick={() => { setState({ loading: true, api: false, db: false }); setAttempt(value => value + 1) }}>Check again <span aria-hidden="true">↗</span></button>
      </section>
      <footer><span>Mistralethon</span><span>Built to begin.</span></footer>
    </main>
  )
}
