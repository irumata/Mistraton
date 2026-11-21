import { useState } from 'react'
import { api, apiPost, uploadForm } from '../api.js'

export default function UploadPanel() {
  const [incidentId, setIncidentId] = useState('incident-001')
  const [files, setFiles] = useState([])
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [overview, setOverview] = useState(null)
  const [archives, setArchives] = useState([])
  const [archiveBusy, setArchiveBusy] = useState(false)
  const [archiveResult, setArchiveResult] = useState(null)
  const [archiveFile, setArchiveFile] = useState(null)

  async function refresh() {
    try {
      setOverview(await api('/sentinel/data'))
      setArchives((await api('/sentinel/archive')).archives)
    } catch (e) { setError(e.message) }
  }

  async function prepareArchive() {
    setArchiveBusy(true); setError(''); setArchiveResult(null)
    try {
      setArchiveResult(await apiPost('/sentinel/archive/prepare', {}))
      await refresh()
    } catch (e) { setError(e.message) } finally { setArchiveBusy(false) }
  }

  async function uploadArchive() {
    if (!archiveFile) return
    setArchiveBusy(true); setError(''); setArchiveResult(null)
    try {
      const fd = new FormData()
      fd.append('file', archiveFile)
      fd.append('apply', 'true')
      setArchiveResult(await uploadForm('/sentinel/archive/upload', fd))
      setArchiveFile(null)
      await refresh()
    } catch (e) { setError(e.message) } finally { setArchiveBusy(false) }
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

      <h3>Input data archive</h3>
      <p className="muted">
        Package the source <code>work/</code> folder into a downloadable zip (sha256 manifest), then apply it
        here (or on the Colab host) to verify, extract into <code>raw/</code> and ingest — after which
        <code> extract-metrics</code> (Mistral Large 4) can analyze it and write <code>metrics.yaml</code>.
      </p>
      <div className="row">
        <button disabled={archiveBusy} onClick={prepareArchive}>{archiveBusy ? 'Zipping…' : 'Prepare archive from work/'}</button>
        <input type="file" accept=".zip" onChange={e => setArchiveFile(e.target.files?.[0] || null)} />
        <button disabled={archiveBusy || !archiveFile} onClick={uploadArchive}>Upload &amp; apply archive</button>
        <button className="ghost" onClick={refresh}>Refresh</button>
      </div>
      {archives.length > 0 && (
        <ul className="archive-list">
          {archives.map(a => (
            <li key={a.name}>
              <a href={`/sentinel/archive/download?name=${encodeURIComponent(a.name)}`}>{a.name}</a>{' '}
              <span className="muted">
                {a.valid ? `${a.file_count} files · ${(a.bytes / 1024).toFixed(0)} KiB · ${a.created_at}` : 'invalid archive'}
              </span>
            </li>
          ))}
        </ul>
      )}
      {archiveResult && <pre>{JSON.stringify(archiveResult, null, 2)}</pre>}
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
