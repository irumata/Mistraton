import { useEffect, useRef, useState } from 'react'
import ForecastChart from './ForecastChart.jsx'
import { BaselineDecision, EpisodeDecision, InvestigationOutcome, MetricSources, ShieldThresholdControl } from './InvestigationDetails.jsx'
import { DEFAULT_SHIELD_THRESHOLD, formatInterval, metricLabel } from './investigationFormat.js'
import { investigationStages } from './investigationDemo.js'
import { EVIDENCE_TYPES, MAX_SUPPORTING_FILES } from './useInvestigation.js'
import './InvestigationPanel.css'

const RUN_STATUS = { queued: 'Queued', running: 'In progress', complete: 'Complete', failed: 'Stopped' }
const STAGE_STATUS = { pending: 'Waiting', running: 'In progress', done: 'Complete', skipped: 'Skipped', failed: 'Stopped' }

function IncidentForm({ busy, uploading, error, setError, submit, submitHuggingFaceTimeline }) {
  const [file, setFile] = useState(null)
  const [attachments, setAttachments] = useState([])
  const [context, setContext] = useState('')
  const [threshold, setThreshold] = useState(String(DEFAULT_SHIELD_THRESHOLD))

  function chooseFile(files) {
    if (busy || !files.length) return
    if (files.length > 1) {
      setError('Choose one incident file and put the rest under supporting data.')
      return
    }
    const next = files[0]
    const extension = next.name.slice(next.name.lastIndexOf('.')).toLowerCase()
    if (next.name.lastIndexOf('.') < 0 || !['.zip', ...EVIDENCE_TYPES.split(',')].includes(extension)) {
      setError('Choose a ZIP, timestamped CSV, log, or text report.')
      return
    }
    setError('')
    setFile(next)
  }

  return (
    <section className="workspace-card" aria-labelledby="upload-title">
      <h3 id="upload-title">Upload incident evidence</h3>
      <form onSubmit={event => {
        event.preventDefault()
        if (file && !busy) submit(file, attachments, context, Number(threshold))
      }}>
        <fieldset disabled={busy}>
          <label className={file ? 'file-drop file-selected' : 'file-drop'}
            onDragOver={event => event.preventDefault()}
            onDrop={event => { event.preventDefault(); chooseFile(event.dataTransfer.files) }}>
            <input className="visually-hidden" type="file" accept={`.zip,${EVIDENCE_TYPES}`}
              aria-describedby="incident-formats" onChange={event => chooseFile(event.target.files)} />
            <span className="file-symbol" aria-hidden="true">+</span>
            <span className="file-label">{file ? 'Change incident file' : 'Choose a file or drop it here'}</span>
            {file && <strong className="chosen-file">{file.name}</strong>}
          </label>
          <p className="field-note" id="incident-formats">ZIP, timestamped CSV, access log, or text report. 20 MB total.</p>
          <details className="supporting-data">
            <summary>Add supporting data or context <span>Optional</span></summary>
            <div>
              <label htmlFor="supporting-files">Supporting files</label>
              <input id="supporting-files" type="file" multiple accept={EVIDENCE_TYPES}
                aria-describedby="supporting-formats" onChange={event => {
                  setError('')
                  setAttachments(Array.from(event.target.files))
                }} />
              <p className="field-note" id="supporting-formats">Up to {MAX_SUPPORTING_FILES} timestamped CSVs, logs, or text reports.</p>
              <label htmlFor="incident-context">Anything the AI should look for?</label>
              <textarea id="incident-context" rows={3} maxLength={1200} value={context}
                placeholder="For example, response times increased after a rollout."
                onChange={event => setContext(event.target.value)} />
            </div>
          </details>
          <div className="upload-threshold">
            <label htmlFor="upload-shield-threshold">Shieldstral candidate threshold</label>
            <input id="upload-shield-threshold" name="shieldThreshold" type="number" min={0} max={1} step="any"
              value={threshold} onChange={event => setThreshold(event.target.value)} required inputMode="decimal"
              aria-describedby="upload-shield-threshold-help" />
            <p className="field-note" id="upload-shield-threshold-help">Mistral Small 4 investigates candidates with a Shieldstral score at or above this cutoff.</p>
          </div>
          <button className="primary-action" disabled={!file || busy} type="submit">
            {uploading ? 'Uploading…' : 'Build monitor'}
          </button>
        </fieldset>
        {error && <p className="form-error" role="alert">{error}</p>}
      </form>
      <div className="public-timeline-entry">
        <button type="button" className="secondary-action" disabled={busy || threshold === '' || Number(threshold) < 0 || Number(threshold) > 1}
          onClick={() => submitHuggingFaceTimeline(Number(threshold))}>Analyze Hugging Face’s July timeline</button>
        <p className="field-note">Five published daily action counts and a monitoring plan.</p>
      </div>
    </section>
  )
}

function Activity({ view, uploading, busy, sample, connection, activity }) {
  const status = uploading ? 'Uploading…' : RUN_STATUS[view?.status] || (busy ? 'Connecting…' : 'Ready')
  const active = view?.status === 'running' ? investigationStages.find(([key]) => view.stages[key]?.status === 'running') : null
  const completed = view ? Object.values(view.stages).filter(stage => ['done', 'skipped'].includes(stage.status)).length : 0
  const finished = view && ['complete', 'failed'].includes(view.status)
  return (
    <section id="run-activity" tabIndex={-1} ref={activity} className="workspace-card activity-card" aria-labelledby="activity-title">
      <div className="activity-heading">
        <h3 id="activity-title">Run activity</h3>
        <span className={view?.status === 'complete' ? 'run-state run-done' : 'run-state'}>{status}</span>
      </div>
      <p className="activity-description" role="status">
        {uploading ? 'Sending the incident files.' : view
          ? [sample ? 'Synthetic sample' : view.result.source?.public_timeline_url ? 'Public incident timeline' : 'Incident evidence', view.filename].join(' · ')
          : 'Follow the AI as it builds and checks your monitor.'}
        {active && <span className="visually-hidden">. {active[1]} in progress.</span>}
      </p>
      {view && <progress className="run-progress" aria-label="Investigation stages processed"
        max={investigationStages.length} value={completed} />}
      <ol className="stage-list" aria-label="Investigation stages">
        {investigationStages.map(([key, label, model]) => {
          const stage = view?.stages[key]
          const state = stage?.status || 'pending'
          return <li key={key} data-state={state} aria-current={state === 'running' ? 'step' : undefined}>
            <span className="stage-dot" aria-label={STAGE_STATUS[state]}>{state === 'done' ? '✓' : state === 'failed' ? '!' : ''}</span>
            <div className="stage-content">
              <div className="stage-heading"><strong>{label}</strong><span>{model}</span></div>
              {view?.models?.[key] && <code className="model-id">{view.models[key]}</code>}
              {stage?.detail && <p>{state === 'skipped' && 'Skipped. '}{stage.detail}</p>}
            </div>
          </li>
        })}
      </ol>
      {connection && <p className="field-note" role="status">{connection}</p>}
      {view?.status === 'failed' && <p role="alert" className="form-error">{view.error}</p>}
      {finished && view.result.source && <a className="text-action activity-link" href="#monitor-results">
        {view.result.trace?.length ? 'View monitor and decisions' : view.result.plan ? 'View monitoring plan' : 'View incident evidence'}
      </a>}
    </section>
  )
}

function MonitorReplay({ result, models, sample, finished }) {
  const { trace, candidates, metric, plan, policy } = result
  const [frame, setFrame] = useState(null)
  const [paused, setPaused] = useState(false)
  const playing = frame !== null && frame < trace.length && !paused
  const end = frame ?? trace.length

  useEffect(() => {
    if (!playing) return undefined
    const stride = Math.max(1, Math.ceil(trace.length / 100))
    const timer = window.setInterval(() => setFrame(value => Math.min(trace.length, value + stride)), 80)
    return () => window.clearInterval(timer)
  }, [playing, trace.length])

  function toggleReplay() {
    if (playing) setPaused(true)
    else {
      if (frame === null || frame === trace.length) setFrame(1)
      setPaused(false)
    }
  }

  const currentTime = trace[end - 1].time
  const visibleCandidates = candidates.filter(candidate => candidate.end <= currentTime)
  const alerts = visibleCandidates.filter(candidate => candidate.status === 'supported').length
  const scored = end === trace.length ? result.scored : trace.slice(0, end).filter(row => row.band).length
  return (
    <div className="replay">
      <div className="replay-heading">
        <div>
          <h3>Incident replay</h3>
          <p>{sample ? 'The sample shows an illustrative forecast band.' : 'Timestral predicts the next observation from earlier measurements. The expected range starts at its 5th and 95th forecast percentiles.'}</p>
        </div>
        {finished && <button type="button" className="secondary-action" onClick={toggleReplay}>
          {playing ? 'Pause replay' : paused ? 'Resume replay' : 'Replay observations'}
        </button>}
      </div>
      <ForecastChart trace={trace} end={end} following={frame !== null} candidates={visibleCandidates}
        label={metricLabel(metric.metric)} precise={metric.interval_seconds < 60} />
      <dl className="replay-stats">
        <div><dt>Scored observations</dt><dd>{scored}</dd></div>
        <div><dt>Candidate episodes</dt><dd>{visibleCandidates.length}</dd></div>
        <div><dt>Alerts for triage</dt><dd>{alerts}</dd></div>
      </dl>
      {visibleCandidates.length > 0 && <div className="candidate-list">
        <h3>Episode decisions</h3>
        <p className="field-note">Shieldstral decides which episodes go to Mistral Small 4. All detected episodes remain visible in the replay.</p>
        {visibleCandidates.map(candidate => <EpisodeDecision key={candidate.id} candidate={candidate}
          plan={plan} policy={policy} models={models} interval={metric.interval_seconds} finished={finished} />)}
      </div>}
      {end === trace.length && visibleCandidates.length === 0 &&
        <p className="replay-note">No measured departure met the detection rule.</p>}
    </div>
  )
}

function PublishedActivity({ rows, source }) {
  return <section className="published-activity" aria-labelledby="published-activity-title">
    <h3 id="published-activity-title">Published activity by day</h3>
    <p className="field-note">These are recovered actions during the incident. The first and last days are partial.</p>
    <table>
      <thead><tr><th scope="col">Date (UTC)</th><th scope="col">Recovered actions</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.date}><th scope="row">{row.date}</th><td>{row.actions.toLocaleString()}</td></tr>)}</tbody>
    </table>
    <p className="field-note">A normal-activity baseline and higher-resolution observations are needed to test early warning.</p>
    <a className="text-action" href={source}>Hugging Face’s technical timeline</a>
  </section>
}

function Results({ view, sample, updateShieldThreshold, openVoice }) {
  const { source, plan, metric, metrics, policy, trace, candidates, published_activity: publishedActivity } = view.result
  if (!source) return null

  const replayReady = Boolean(trace?.length)
  const title = replayReady ? 'Your incident monitor' : !plan ? 'Incident evidence' : metric ? 'Your monitoring plan' : 'Recommended instrumentation'
  const finished = ['complete', 'failed'].includes(view.status)
  const adjustable = view.status === 'complete' && policy && candidates?.some(candidate => candidate.shield_score != null)
  const persistence = plan?.persistence === 1 ? 'one measured interval' : `${plan?.persistence} measured intervals`
  const departure = plan?.direction === 'both' ? 'outside' : plan?.direction
  return (
    <section id="monitor-results" className="monitor-results" aria-labelledby="results-title">
      <p className="section-label">{sample ? 'Synthetic sample' : 'Investigation results'}</p>
      <h2 id="results-title">{title}</h2>
      <div className="workspace-card result-card">
        {sample && <p className="sample-disclosure">The measurements, forecasts, and model verdicts in this replay are illustrative.</p>}
        <InvestigationOutcome view={view} />
        {openVoice && <a className="text-action" href="#incident-voice" onClick={openVoice}>Open this incident’s voice briefing</a>}
        {publishedActivity && <PublishedActivity rows={publishedActivity} source={source.public_timeline_url} />}
        {adjustable && <ShieldThresholdControl key={view.id || 'sample'} candidates={candidates} policy={policy}
          update={updateShieldThreshold} />}
        {plan && <div className="metric-overview">
          <div>
            <h3>{metric ? metricLabel(metric.metric) : 'What to measure'}</h3>
            <p className="metric-source">{metric ? 'Selected' : 'Recommended'} by Le Chonk (Mistral Large 4)</p>
            <p className="metric-summary">{plan.summary}</p>
            <p>{plan.reason}</p>
          </div>
          <div className="monitor-rule">
            <h4>Monitoring rule</h4>
            <p>{plan.monitoring}</p>
            {metric && replayReady && <p>Flag values {departure} the expected range for at least <strong>{persistence}</strong>.
              The replay uses {formatInterval(metric.interval_seconds)} intervals.</p>}
            {replayReady && policy && <p className="field-note">An episode can bridge up to {policy.max_missing_intervals} missing intervals.</p>}
          </div>
        </div>}
        {(metrics || metric) && <MetricSources metrics={metrics ?? [metric]} selected={metric} />}
        <BaselineDecision result={view.result} model={view.models?.calibrate} />
        {replayReady && <MonitorReplay key={view.id || 'sample'} result={view.result} models={view.models}
          sample={sample} finished={finished} />}
        {finished && <details className="evidence-details">
          <summary>Evidence used · {source.files.length} {source.files.length === 1 ? 'file' : 'files'}</summary>
          <ul>{source.files.map(file => <li key={file}>{file}</li>)}</ul>
          {source.sha256 && <p className="field-note">Upload fingerprint <code>{source.sha256}</code></p>}
        </details>}
      </div>
    </section>
  )
}

export default function InvestigationPanel({ investigation, openVoice }) {
  const activity = useRef(null)
  const { view, sample, busy, uploading, error, setError, connection, submit, submitHuggingFaceTimeline, updateShieldThreshold } = investigation

  async function focusAfterStart(start) {
    const started = await start
    if (started && window.matchMedia('(max-width: 760px)').matches) {
      activity.current?.focus({ preventScroll: true })
      activity.current?.scrollIntoView({ block: 'start' })
    }
  }

  return (
    <section id="investigate" className="workspace" aria-labelledby="investigation-title">
      <h2 id="investigation-title">Build a monitor</h2>
      <p className="workspace-intro">Start with what you have. Timestamped measurements let Safestral test the monitor against the incident.</p>
      <div className="workspace-grid">
        <IncidentForm busy={busy} uploading={uploading} error={error} setError={setError}
          submit={(...args) => focusAfterStart(submit(...args))}
          submitHuggingFaceTimeline={threshold => focusAfterStart(submitHuggingFaceTimeline(threshold))} />
        <Activity view={view} uploading={uploading} busy={busy} sample={sample} connection={connection} activity={activity} />
      </div>
      {view?.result.source && <Results view={view} sample={sample} updateShieldThreshold={updateShieldThreshold}
        openVoice={!sample && investigation.voiceRun ? openVoice : null} />}
    </section>
  )
}
