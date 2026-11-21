import { useEffect, useState } from 'react'
import { api, apiPost, uploadForm } from '../api.js'
import { investigationStages, sampleInvestigation } from './investigationDemo.js'
import { DEFAULT_SHIELD_THRESHOLD } from './investigationFormat.js'

const PATH = '/sentinel/investigations'
const RUN_STORAGE = 'safestral-investigation'
const MAX_BYTES = 20 * 1024 * 1024
export const EVIDENCE_TYPES = '.csv,.log,.txt,.md,.json,.jsonl'
export const MAX_SUPPORTING_FILES = 8

function savedRun() {
  try {
    const run = JSON.parse(window.sessionStorage.getItem(RUN_STORAGE))
    return typeof run?.id === 'string' && typeof run.key === 'string' ? run : null
  } catch {
    return null
  }
}

function saveRun(run) {
  try {
    if (run) window.sessionStorage.setItem(RUN_STORAGE, JSON.stringify(run))
    else window.sessionStorage.removeItem(RUN_STORAGE)
  } catch { /* The current page works when browser storage is disabled. */ }
}

export default function useInvestigation() {
  const [run, setRun] = useState(savedRun)
  const [view, setView] = useState(null)
  const [sampleStep, setSampleStep] = useState(null)
  const [sampleThreshold, setSampleThreshold] = useState(DEFAULT_SHIELD_THRESHOLD)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [connection, setConnection] = useState('')
  const [pollVersion, setPollVersion] = useState(0)
  const sample = sampleStep !== null
  const sampleRunning = sample && sampleStep < investigationStages.length
  const busy = uploading || sampleRunning || Boolean(run && (!view || ['queued', 'running'].includes(view.status)))
  const voiceRun = run && view?.id === run.id && view.result.source && ['complete', 'failed'].includes(view.status)
    ? { ...run, filename: view.result.source.public_timeline_url ? 'Hugging Face timeline investigation' : view.filename }
    : null

  useEffect(() => {
    if (!sampleRunning) return undefined
    const timer = window.setTimeout(() => setSampleStep(step => step + 1), 540)
    return () => window.clearTimeout(timer)
  }, [sampleRunning, sampleStep])

  useEffect(() => {
    if (!run) return undefined
    const controller = new AbortController()
    let timer
    async function poll() {
      try {
        const data = await api(`${PATH}/${run.id}`, {
          signal: controller.signal,
          headers: { 'X-Safestral-Run-Key': run.key },
        })
        if (controller.signal.aborted) return
        setView(data)
        setConnection('')
        if (['queued', 'running'].includes(data.status)) timer = window.setTimeout(poll, 900)
      } catch (err) {
        if (controller.signal.aborted) return
        if ([401, 403, 404].includes(err.status)) {
          saveRun(null)
          setRun(null)
          setView(null)
          setConnection('')
          setError(err.message)
        } else {
          setConnection('Connection interrupted. Reconnecting to the run…')
          timer = window.setTimeout(poll, 2400)
        }
      }
    }
    poll()
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [run, pollVersion])

  function startSample() {
    if (busy) return
    setError('')
    setSampleThreshold(DEFAULT_SHIELD_THRESHOLD)
    setSampleStep(0)
  }

  async function updateShieldThreshold(threshold) {
    if (sample) {
      setSampleThreshold(threshold)
      return
    }
    const updated = await api(`${PATH}/${run.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', 'X-Safestral-Run-Key': run.key },
      body: JSON.stringify({ shield_threshold: threshold }),
    })
    setView(current => current?.id === updated.id ? updated : current)
    if (['queued', 'running'].includes(updated.status)) setPollVersion(version => version + 1)
  }

  async function submit(file, attachments, context, threshold) {
    if (!file || busy) return
    setError('')
    if (attachments.length > MAX_SUPPORTING_FILES) {
      setError(`Choose up to ${MAX_SUPPORTING_FILES} supporting files.`)
      return
    }
    if ([file, ...attachments].reduce((bytes, item) => bytes + item.size, 0) > MAX_BYTES) {
      setError('Keep the combined upload at or below 20 MB.')
      return
    }
    const form = new FormData()
    form.append('file', file)
    form.append('shield_threshold', threshold)
    if (context.trim()) form.append('prompt', context.trim())
    for (const attachment of attachments) form.append('attachments', attachment)
    return startRun(() => uploadForm(PATH, form))
  }

  function submitHuggingFaceTimeline(threshold) {
    return startRun(() => apiPost(`${PATH}/hugging-face-timeline`, { shield_threshold: threshold }))
  }

  async function startRun(request) {
    if (busy) return
    setError('')
    setUploading(true)
    try {
      const started = await request()
      saveRun(started)
      setSampleStep(null)
      setView(null)
      setConnection('')
      setRun(started)
      return true
    } catch (err) {
      setError(err.message)
    } finally {
      setUploading(false)
    }
  }

  return {
    view: sample ? sampleInvestigation(sampleStep, sampleThreshold) : view,
    voiceRun, sample, busy, uploading, error, setError, connection, startSample, submit, submitHuggingFaceTimeline,
    updateShieldThreshold,
  }
}
