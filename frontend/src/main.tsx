import { StrictMode, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './style.css'

type Job = { id: string; status: string; result: string | null }

function App() {
  const [ready, setReady] = useState(false)
  const [key, setKey] = useState('')
  const [text, setText] = useState('Hello from the hackathon')
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    fetch('/api/ready').then(r => { if (alive.current) setReady(r.ok) }).catch(() => {})
    return () => { alive.current = false }
  }, [])

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true); setError(''); setJob(null)
    try {
      const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${key}` }
      const response = await fetch('/api/jobs', { method: 'POST', headers, body: JSON.stringify({ text }) })
      if (!response.ok) throw new Error(response.status === 401 ? 'Check your API key and try again.' : `Submission failed (${response.status}).`)
      const { job_id } = await response.json() as { job_id: string }
      setJob({ id: job_id, status: 'queued', result: null })
      for (let attempt = 0; attempt < 60; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 1000))
        if (!alive.current) return
        const progress = await fetch(`/api/jobs/${job_id}`, { headers })
        if (!progress.ok) throw new Error(`Could not check job (${progress.status}).`)
        const next = await progress.json() as Job
        setJob(next)
        if (next.status === 'finished') return
        if (next.status === 'failed' || next.status === 'enqueue_failed') throw new Error('The job could not finish. Check the worker logs.')
      }
      throw new Error('Still waiting after one minute. The job may continue running; use its ID to check again.')
    } catch (cause) {
      if (alive.current) setError(cause instanceof Error ? cause.message : 'Something went wrong.')
    } finally {
      if (alive.current) setBusy(false)
    }
  }

  return <main>
    <nav><span className="brand">◈ Launchpad</span><span className={`status ${ready ? 'online' : ''}`}>{ready ? 'Services ready' : 'Connecting to services'}</span></nav>
    <section className="intro"><p className="eyebrow">FROM IDEA TO SOMETHING REAL</p><h1>Your next idea<br /><em>starts here.</em></h1><p className="lead">A small starting point for a big weekend. Send a message, run a background job, and see the result.</p></section>
    <section className="workspace">
      <form onSubmit={submit}><div className="section-title"><span className="number">01</span><h2>Try your first job</h2></div>
        <label htmlFor="api-key">API key</label><input id="api-key" type="password" autoComplete="off" value={key} onChange={e => setKey(e.target.value)} required minLength={32} placeholder="Enter your private API key" />
        <p className="hint">Kept in this page’s memory. Refreshing clears it. Share this key only with trusted teammates.</p>
        <label htmlFor="message">Your message</label><textarea id="message" value={text} onChange={e => setText(e.target.value)} maxLength={4000} required rows={4} />
        <button disabled={busy || !key}>{busy ? 'Working…' : 'Run background job'} <span aria-hidden="true">↗</span></button>
      </form>
      <aside aria-live="polite"><div className="section-title"><span className="number">02</span><h2>The result</h2></div>
        {job ? <><span className="pill">{job.status}</span><p className="result">{job.result ?? 'Your message is on its way…'}</p><p className="hint job-id">Job {job.id}</p></> : <div className="empty"><span aria-hidden="true">✳</span><p>A little input.<br />A working result.</p></div>}
        {error && <p role="alert" className="error">{error}</p>}
      </aside>
    </section>
    <footer><span>Built for the first step. Ready for the next.</span><span>React · FastAPI · RQ</span></footer>
  </main>
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
