import { useState } from 'react'
import { api, uploadForm } from '../api.js'

export default function UploadPanel() {
  const [incidentId, setIncidentId] = useState('incident-001')
  const [files, setFiles] = useState([])
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [overview, setOverview] = useState(null)

  async function refresh() {
    try { setOverview(await api('/sentinel/data')) } catch (e) { setError(e.message) }
  }

  async function onUpload() {
    setBusy(true); setError(''); setResult(null)
    try {
      const fd = new FormData()
      fd.append('incident_id', incidentId)
      fd.append('ingest', 'true')
      for (const f of files) fd.append('files', f)
      const res = await uploadForm('/sentinel/upload', fd)
      setResult(res)
      await refresh()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel">
      <h2>Upload incident data</h2>
      <p className="muted">
        Drop an incident bundle: <code>record.json</code>, logs (<code>*.log/*.txt/*.md</code>),
        chat (<code>*.chat.json</code>), metric exports (<code>metrics/*.csv</code> with
        header <code>timestamp,value</code>). Files land in <code>&lt;data_dir&gt;/raw/&lt;incident_id&gt;/</code> and are
        ingested into <code>records.jsonl</code>.
      </p>
      <div className="row">
        <input value={incidentId} onChange={e => setIncidentId(e.target.value)} placeholder="incident id" />
        <input type="file" multiple onChange={e => setFiles(Array.from(e.target.files || []))} />
        <button disabled={busy || files.length === 0} onClick={onUpload}>{busy ? 'Uploading…' : 'Upload & ingest'}</button>
        <button className="ghost" onClick={refresh}>Refresh</button>
      </div>
      {files.length > 0 && <p className="muted">{files.length} file(s): {files.map(f => f.name).join(', ')}</p>}
      {error && <p className="error">{error}</p>}
      {result && <pre>{JSON.stringify(result, null, 2)}</pre>}
      {overview && (
        <div>
          <h3>Data dir</h3>
          <p className="muted">{overview.data_dir} · artifacts: {overview.artifacts_dir}</p>
          <p><b>Records:</b> {overview.records.length === 0 ? 'none yet' : ''}</p>
          <table>
            <thead><tr><th>incident</th><th>start</th><th>end</th><th>metrics</th></tr></thead>
            <tbody>
              {overview.records.map(r => (
                <tr key={r.incident_id}><td>{r.incident_id}</td><td>{r.start}</td><td>{r.end}</td><td>{r.metrics}</td></tr>
              ))}
            </tbody>
          </table>
          <p><b>Metrics:</b> {overview.metrics.join(', ') || '—'}</p>
          <p><b>Raw bundles:</b> {overview.raw_bundles.join(', ') || '—'}</p>
          <p><b>Artifacts:</b> {overview.artifacts.join(', ') || '—'}</p>
        </div>
      )}
    </section>
  )
}
