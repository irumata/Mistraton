import { useState } from 'react'
import { BASELINE_SHIELD_THRESHOLD, DEFAULT_SHIELD_THRESHOLD, formatInterval, formatNumber, formatTime, metricLabel } from './investigationFormat.js'

const OUTCOMES = {
  supported: 'Warrants manual triage',
  needs_review: 'Needs manual review',
  dismissed_on_review: 'Dismissed after review',
  shield_below_threshold: 'Below Shieldstral threshold',
  unreviewed: 'Shieldstral check skipped',
  verified: 'Passed Shieldstral',
  candidate: 'Awaiting Shieldstral',
}

const REVIEW_DECISIONS = {
  triage: 'Triage recommended', needs_review: 'Uncertain · manual review', dismiss: 'Dismissal recommended',
  unavailable: 'Review unavailable',
}

function ModelId({ model }) {
  return model && <code className="model-id">{model}</code>
}

function ShieldScore({ score, threshold }) {
  if (score == null) return null
  const rounded = Number(score.toPrecision(4))
  return <p className="field-note">
    Yes score <strong>{rounded === threshold && score !== threshold ? score : rounded}</strong>
    {' · '}Pass threshold <strong>≥ {threshold}</strong>
  </p>
}

export function InvestigationOutcome({ view }) {
  const { candidates, trace, plan } = view.result
  if (!plan) return null

  const total = candidates?.length ?? 0
  const triage = candidates?.filter(candidate => candidate.status === 'supported').length ?? 0
  const pending = candidates?.filter(candidate => candidate.status === 'needs_review').length ?? 0
  const unreviewed = candidates?.filter(candidate => candidate.status === 'unreviewed').length ?? 0
  let title = 'Assessment in progress'
  let detail = 'The replay and model reviews are in progress.'
  if (view.status === 'failed') {
    title = 'Assessment stopped'
    detail = 'The run activity shows which check stopped. Any decisions below reflect the checks that completed.'
  } else if (view.status === 'complete') {
    if (!trace?.length) {
      title = 'Monitoring plan only'
      detail = view.stages.detect?.detail
    } else if (!total) {
      title = 'No candidate episodes'
      detail = 'No measured departure met the Timestral detection rule in the replay.'
    } else {
      title = triage > 0
        ? (triage === 1 ? '1 episode warrants manual triage' : `${triage} episodes warrant manual triage`)
        : (pending > 0 ? 'Manual review needed' : 'No episodes qualified for triage')
      detail = [
        `${triage} of ${total} candidates passed both checks.`,
        pending ? `${pending} need manual review.` : '',
        unreviewed ? `${unreviewed} skipped Shieldstral verification.` : '',
      ].filter(Boolean).join(' ')
    }
  }

  return <div className="investigation-outcome" data-alert={triage > 0}>
    <h3>{title}</h3>
    {detail && <p>{detail}</p>}
  </div>
}

export function ShieldThresholdControl({ candidates, policy, update }) {
  const applied = policy.shield_threshold
  const [value, setValue] = useState(String(applied))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const scored = candidates.filter(candidate => candidate.shield_score != null)
  const passed = scored.filter(candidate => candidate.shield_score >= applied).length

  async function apply(event) {
    event.preventDefault()
    if (saving || Number(value) === applied) return
    setSaving(true)
    setError('')
    try {
      await update(Number(value))
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return <form className="shield-threshold-control" onSubmit={apply} aria-labelledby="run-shield-threshold-label">
    <label id="run-shield-threshold-label" htmlFor="run-shield-threshold">Shieldstral candidate threshold</label>
    <div className="threshold-fields">
      <input id="run-shield-threshold" type="number" min={0} max={1} step="any" required inputMode="decimal"
        value={value} disabled={saving} aria-describedby="run-shield-threshold-help"
        onChange={event => setValue(event.target.value)} />
      <button className="secondary-action" type="submit" disabled={saving || !value || Number(value) === applied}>
        {saving ? 'Applying…' : 'Apply threshold'}
      </button>
    </div>
    <p className="field-note" id="run-shield-threshold-help">
      Applying reuses saved scores and reviews. Newly passing episodes go to Mistral Small 4.
    </p>
    <p className="threshold-status" role="status">{passed} of {scored.length} scored candidates pass Shieldstral at {applied}.</p>
    {error && <p className="form-error" role="alert">{error}</p>}
  </form>
}

export function MetricSources({ metrics, selected }) {
  return <section className="metric-sources" aria-label="Measured metrics">
    <h3>Metrics found ({metrics.length})</h3>
    {metrics.length === 0 ? <p className="field-note">The uploaded files yielded no timestamped numeric series.</p> :
      <ul className="metric-list">
        {metrics.map(metric => {
          const used = metric.id === selected?.id
          const log = metric.source.toLowerCase().endsWith('.log')
          const sourceLabel = log ? 'Log field' : 'CSV column'
          const aggregation = log && metric.metric === 'http_5xx_rate'
            ? 'Share of requests with HTTP status 500–599' : 'Mean of readings'
          return <li key={metric.id} data-selected={used}>
            <div className="metric-list-heading">
              <strong>{metricLabel(metric.metric)}</strong>
              {used && <span className="selected-metric">Selected for the plan</span>}
            </div>
            <dl>
              <div><dt>File</dt><dd><code>{metric.source}</code></dd></div>
              {metric.source_field && <div><dt>{sourceLabel}</dt><dd><code>{metric.source_field}</code></dd></div>}
              {metric.populated_intervals != null && <div><dt>Coverage</dt><dd>
                {metric.measurements.toLocaleString()} readings · {metric.populated_intervals}/{metric.intervals} populated intervals
              </dd></div>}
              <div><dt>Aggregation</dt><dd>{aggregation} per {formatInterval(metric.interval_seconds)} interval</dd></div>
              {metric.eligible_for_detection === false && <div><dt>Replay</dt><dd>Too few populated intervals</dd></div>}
            </dl>
          </li>
        })}
      </ul>}
  </section>
}

export function BaselineDecision({ result, model }) {
  const { baseline, metric, calibration_count: count, padding, policy } = result
  if (!baseline) return null

  return <section className="baseline-decision" aria-label="Shieldstral baseline check">
    <div className="decision-title"><h3>Shieldstral baseline check</h3><ModelId model={model} /></div>
    <p className="decision-verdict">{baseline.approved ? 'Accepted for calibration' : 'Declined for calibration'}</p>
    <p className="decision-description">Shieldstral assessed the stability of the early measurements and checked for obvious instrumentation problems.</p>
    <dl className="decision-metrics">
      {metric.first_interval && baseline.last_interval && <div><dt>Baseline window</dt><dd>
        {formatTime(metric.first_interval, true)} to {formatTime(baseline.last_interval, true)}
      </dd></div>}
      <div><dt>Populated baseline intervals</dt><dd>{baseline.measurements}</dd></div>
      {baseline.min != null && <div><dt>Min / median / max</dt><dd>
        {[baseline.min, baseline.median, baseline.max].map(formatNumber).join(' / ')}
      </dd></div>}
    </dl>
    <ShieldScore score={baseline.shield_score} threshold={policy?.baseline_shield_threshold ?? BASELINE_SHIELD_THRESHOLD} />
    <p className="decision-description">{baseline.approved
      ? count != null && padding != null
        ? `Safestral calibrated the forecast band using ${count} early comparisons. Padding per bound was ${formatNumber(padding)} in the metric’s units.`
        : 'Timestral uses the approved baseline to calibrate its forecast band.'
      : 'The replay uses the native Timestral forecast band.'}</p>
  </section>
}

export function EpisodeDecision({ candidate, plan, policy, models, interval, finished }) {
  const { id, peak, start, end, observations, direction, status, explanation, hypotheses, next_check: nextCheck } = candidate
  const threshold = policy?.shield_threshold ?? DEFAULT_SHIELD_THRESHOLD
  const reviewThreshold = candidate.review_threshold ?? policy?.review_shield_threshold ?? threshold
  const score = candidate.shield_score
  const departure = direction === 'below' ? 'below' : 'above'
  const excess = departure === 'below' ? peak.lower - peak.observed : peak.observed - peak.upper
  const precise = interval < 60
  const period = [start, end].filter((time, index) => index === 0 || time !== start)
    .map(time => formatTime(time, precise)).join(' to ')
  const shieldVerdict = score == null ? null : score >= threshold ? 'Yes · Passed' : 'No · Below threshold'
  const reviewVerdict = REVIEW_DECISIONS[candidate.review_decision]
  const originalShieldVerdict = score == null ? null : score >= reviewThreshold ? 'Yes' : 'No'
  const pending = status === 'unreviewed' ? 'Skipped' : finished ? 'No verdict recorded' : 'Awaiting verdict'

  return <article className="candidate" data-outcome={status} aria-label={`Episode ${id.replace('alert-', '')}`}>
    <header className="candidate-heading">
      <h4>Episode {id.replace('alert-', '')} · {OUTCOMES[status]}</h4>
      <span>{period}</span>
    </header>
    <ol className="decision-list">
      <li>
        <h5>Timestral forecast</h5>
        <ModelId model={models?.detect} />
        <p className="decision-verdict">Candidate detected</p>
        <p>{observations} {observations === 1 ? 'interval' : 'intervals'} {departure} the band · {plan.persistence} required</p>
        <dl className="decision-metrics">
          <div><dt>Time of peak departure</dt><dd>{formatTime(peak.time, precise)}</dd></div>
          <div><dt>Observed</dt><dd>{formatNumber(peak.observed)}</dd></div>
          <div><dt>Forecast median</dt><dd>{formatNumber(peak.forecast)}</dd></div>
          <div><dt>Expected range</dt><dd>{formatNumber(peak.lower)}–{formatNumber(peak.upper)}</dd></div>
          <div><dt>Distance {departure} bound</dt><dd>{formatNumber(excess)}</dd></div>
        </dl>
      </li>
      <li>
        <h5>Shieldstral verification</h5>
        <ModelId model={models?.verify} />
        <p className="decision-verdict">{shieldVerdict ?? pending}</p>
        <ShieldScore score={score} threshold={threshold} />
        {shieldVerdict && <p>Checked the peak, duration, and baseline for a meaningful deviation.</p>}
      </li>
      <li>
        <h5>Triage review</h5>
        <ModelId model={(reviewVerdict || status === 'verified') && models?.review} />
        <p className="decision-verdict">{reviewVerdict ?? (status === 'shield_below_threshold' ? 'Skipped by Shieldstral' : pending)}</p>
        {explanation && <p>{explanation}</p>}
        {hypotheses?.length > 0 && <>
          <p><strong>Possible explanations</strong></p>
          <ul className="review-hypotheses">
            {hypotheses.map((hypothesis, index) => <li key={index}>{hypothesis}</li>)}
          </ul>
        </>}
        {nextCheck && <p><strong>Next check</strong> {nextCheck}</p>}
        {reviewVerdict && candidate.review_decision !== 'unavailable' && reviewThreshold !== threshold &&
          <p className="field-note">The reviewer saw Shieldstral’s {originalShieldVerdict} verdict at {reviewThreshold}.</p>}
      </li>
    </ol>
  </article>
}
