export const DEFAULT_SHIELD_THRESHOLD = 0.1
export const BASELINE_SHIELD_THRESHOLD = 0.5

const dateTimeFormat = new Intl.DateTimeFormat(undefined, {
  day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'UTC',
})
const timestampFormat = new Intl.DateTimeFormat(undefined, {
  year: 'numeric', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit',
  hourCycle: 'h23', timeZone: 'UTC',
})
const fractionalTimestampFormat = new Intl.DateTimeFormat(undefined, {
  year: 'numeric', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit',
  fractionalSecondDigits: 3, hourCycle: 'h23', timeZone: 'UTC',
})

export function formatTime(value, precise = false) {
  const fraction = /\.(\d+)(?:Z|[+-])/.exec(value)?.[1]?.replace(/0+$/, '')
  const date = new Date(value)
  if (fraction?.length > 3) {
    const parts = fractionalTimestampFormat.formatToParts(date)
    return `${parts.map(part => part.type === 'fractionalSecond' ? fraction : part.value).join('')} UTC`
  }
  const format = fraction ? fractionalTimestampFormat : precise ? timestampFormat : dateTimeFormat
  return `${format.format(date)} UTC`
}

export function formatNumber(value) {
  return Number(value).toLocaleString(undefined, { maximumSignificantDigits: 5 })
}

export function formatInterval(seconds) {
  return seconds < 1 ? `${formatNumber(seconds * 1000)} ms` : `${formatNumber(seconds)} s`
}

export function metricLabel(name) {
  if (name === 'response_time_ms') return 'Response time (ms)'
  if (name === 'http_5xx_rate') return 'HTTP 5xx rate'
  return name.replaceAll('_', ' ')
}
