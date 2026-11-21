import { useEffect, useRef, useState } from 'react'
import { api, apiPost } from '../api.js'

const STAGES = [
  { id: 'ingest', label: 'ingest', hint: 'raw/ bundles → records.jsonl' },
  { id: 'extract-metrics', label: 'extract-metrics', hint: 'Mistral Large → metrics.yaml (API key needed)' },
  { id: 'label-windows', label: 'label-windows', hint: 'Shieldstral window labels → labels.db' },
  { id: 'build-dataset', label: 'build-dataset', hint: 'windows.jsonl + splits.json' },
  { id: 'train-detection', label: 'train-detection', hint: 'forecasting detector (primary)' },
  { id: 'train-embedding-head', label: 'train-embedding-head', hint: 'embedding classifier head' },
  { id: 'train-zero-shot', label: 'train-zero-shot', hint: 'control baseline' },
  { id: 'evaluate', label: 'evaluate', hint: 'held-out incident test set' },
  { id: 'shadow-run', label: 'shadow-run', hint: 'shadow mode over test streams' },
  { id: 'finetune-timestral', label: 'finetune-timestral', hint: 'QLoRA on GPU → artifacts/timestral-hf' },
]

export default function PipelinePanel() {
  const [job, setJob] = useState(null)
  const [error, setError] = useState('')
  const [evalReport, setEvalReport] = useState(null)
  const timer = useRef(null)

  async function poll(jobId) {
    const j = await api(`/sentinel/pipeline/jobs/${jobId}`)
    setJob(j)
    if (j.status === 'running') {
      timer.current = setTimeout(() => poll(jobId).catch(e => setError(e.message)), 1500)
    } else {
      setEvalReport(null)
      if (j.stage === 'evaluate') {
        api(`/sentinel/eval?strategy=${j.stage === 'evaluate' ? 'forecasting' : 'forecasting'}`).then(setEvalReport).catch(() => {})
      }
    }
  }

  async function run(stage) {
    setError(''); setJob(null)
    try {
      const started = await apiPost(`/sentinel/pipeline/${stage}`, { strategy: 'forecasting' })
      setJob(started)
      timer.current = setTimeout(() => poll(started.job_id).catch(e => setError(e.message)), 1000)
    } catch (e) {
      setError(e.message)
    }
  }

  useEffect(() => () => clearTimeout(timer.current), [])

  async function showEval() {
    setError('')
    try { setEvalReport(await api('/sentinel/eval?strategy=forecasting')) } catch (e) { setError(e.message) }
  }

  return (
    <section className="panel">
      <h2>Pipeline</h2>
      <div className="stages">
        {STAGES.map(s => (
          <button key={s.id} title={s.hint} disabled={job?.status === 'running'} onClick={() => run(s.id)}>
            {s.label}
          </button>
        ))}
        <button className="ghost" onClick={showEval}>show eval</button>
      </div>
      {job && (
        <div>
          <p>Job <code>{job.job_id}</code> · <b>{job.stage}</b> · <span className={`badge ${job.status}`}>{job.status}</span></p>
          {job.log && <pre className="log">{job.log}</pre>}
        </div>
      )}
      {evalReport && (
        <div>
          <h3>Evaluation (held-out test)</h3>
          <pre>{JSON.stringify(evalReport, null, 2)}</pre>
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </section>
  )
}
