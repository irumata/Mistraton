import { BASELINE_SHIELD_THRESHOLD, DEFAULT_SHIELD_THRESHOLD } from './investigationFormat.js'

const start = Date.UTC(2026, 0, 15, 14)
const observations = [
  114, 118, 110, 123, 129, 121, 126, 120, 130, 117, 123, 119,
  112, 125, 119, 130, 116, 127, 132, 121, 129, 124, 119, 125,
  129, 123, 128, 184, 193, 133, 125, 119, 130, 127, 122, 140,
  229, 344, 412, 301, 181, 149, 125, 133, 124, 121, 119, 125,
]

const sampleMetric = {
  id: 'm1',
  metric: 'response_time_ms',
  source: 'gateway_metrics.csv',
  source_field: 'response_time_ms',
  interval_seconds: 60,
  measurements: observations.length,
  populated_intervals: observations.length,
  intervals: observations.length,
  eligible_for_detection: true,
  first_interval: new Date(start).toISOString(),
}
const sampleMetrics = [sampleMetric, { ...sampleMetric, id: 'm2', metric: 'http_5xx_rate', source_field: 'http_5xx_rate' }]

export const sampleTrace = observations.map((observed, index) => {
  const forecast = Math.round(124 + 5 * Math.sin(index / 3.4))
  return {
    time: new Date(start + index * 60000).toISOString(),
    observed,
    band: index < 24 ? null : [forecast - 23, forecast, forecast + 37],
  }
})

function episode(id, first, last, peakIndex, shieldScore, review) {
  const { time, observed, band } = sampleTrace[peakIndex]
  return {
    id, direction: 'above', shield_score: shieldScore, review_decision: 'triage', ...review,
    start: sampleTrace[first].time,
    end: sampleTrace[last].time,
    observations: last - first + 1,
    peak: { time, observed, lower: band[0], forecast: band[1], upper: band[2] },
  }
}

const candidates = [
  episode('alert-1', 27, 28, 28, 0.08, {
    explanation: 'The two elevated intervals peaked at 193 ms.',
    hypotheses: ['A short burst of requests may have increased queue time.', 'A backend retry could have delayed a few requests.'],
    next_check: 'Compare request volume and backend retry counts around 14:28 UTC.',
  }),
  episode('alert-2', 36, 40, 38, 0.94, {
    explanation: 'Latency stayed above the forecast band for five minutes and peaked at 412 ms.',
    hypotheses: ['An upstream service may have slowed for several minutes.', 'A gateway queue may have built up during the same period.'],
    next_check: 'Compare gateway queue time and upstream request duration between 14:36 and 14:40 UTC.',
  }),
]

export const investigationStages = [
  ['archive', 'Read the incident', 'Evidence'],
  ['investigate', 'Choose what to measure', 'Le Chonk'],
  ['calibrate', 'Check the baseline', 'Shieldstral'],
  ['detect', 'Forecast and detect', 'Timestral'],
  ['verify', 'Verify candidates', 'Shieldstral'],
  ['review', 'Investigate verified episodes', 'Le Chonk'],
]

const details = [
  ['Reading the example incident report and gateway metrics.', 'Found two timestamped series in the gateway CSV.'],
  ['Comparing the measurements with the reported slow requests.', 'Selected gateway response time.'],
  ['Checking whether early measurements are stable enough for calibration.', 'Accepted the first 24 observations as a baseline.'],
  ['Comparing each later observation with its expected range.', 'Scored 24 observations and found two candidate episodes.'],
  ['Reviewing the duration and size of each departure.', ''],
  ['Investigating the episodes that passed Shieldstral.', ''],
]

export function sampleInvestigation(step, threshold = DEFAULT_SHIELD_THRESHOLD) {
  const result = {}
  const passed = candidates.filter(candidate => candidate.shield_score >= threshold).length
  if (step > 0) {
    result.source = { files: ['incident.md', sampleMetric.source], series_count: sampleMetrics.length }
    result.metrics = sampleMetrics
  }
  if (step > 1) {
    result.metric = sampleMetric
    result.policy = {
      shield_threshold: threshold, review_shield_threshold: DEFAULT_SHIELD_THRESHOLD,
      baseline_shield_threshold: BASELINE_SHIELD_THRESHOLD, review_limit: 6, max_missing_intervals: 2,
    }
    result.plan = {
      summary: 'Monitor gateway response time to spot slow requests.',
      reason: 'The report describes slow responses. Gateway response time shows when the slowdown started and recovered. The HTTP 5xx rate tracks failed requests.',
      monitoring: 'Record request duration in milliseconds and aggregate it into one-minute intervals. Compare each interval with the expected range.',
      direction: 'above',
      persistence: 2,
    }
  }
  if (step > 2) result.baseline = {
    measurements: 24, min: 110, median: 122, max: 132,
    last_interval: sampleTrace[23].time, shield_score: 0.91, approved: true,
  }
  if (step > 3) {
    result.trace = sampleTrace
    result.scored = 24
    result.calibration_count = 8
    result.padding = 0
    result.candidates = candidates.map(({ explanation, hypotheses, next_check, review_decision, shield_score, ...candidate }) => {
      const reviewThreshold = shield_score >= DEFAULT_SHIELD_THRESHOLD ? DEFAULT_SHIELD_THRESHOLD : threshold
      const reviewed = step >= investigationStages.length && shield_score >= reviewThreshold
      let status = 'candidate'
      if (step > 4) status = shield_score >= threshold ? reviewed ? 'supported' : 'verified' : 'shield_below_threshold'
      return {
        ...candidate, status,
        shield_score: step > 4 ? shield_score : undefined,
        ...(reviewed && { explanation, hypotheses, next_check, review_decision, review_threshold: reviewThreshold }),
      }
    })
  }

  const stages = Object.fromEntries(investigationStages.map(([key], index) => [
    key,
    {
      status: index < step ? 'done' : index === step ? 'running' : 'pending',
      detail: index <= step ? details[index][Number(index < step)] : '',
    },
  ]))
  if (step > 4) stages.verify.detail = `Shieldstral passed ${passed} of ${candidates.length} candidates at ${threshold}.`
  if (step >= investigationStages.length) {
    const reviewed = result.candidates.filter(candidate => candidate.review_decision).length
    stages.review = reviewed
      ? { status: 'done', detail: `Large 4 investigated ${reviewed} episodes that passed Shieldstral.` }
      : { status: 'skipped', detail: 'No candidates passed Shieldstral for Large 4 review.' }
  }
  return { status: step < investigationStages.length ? 'running' : 'complete', stages, result, filename: 'Gateway slowdown' }
}

