import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import './VoicePanel.css'

const PATH = '/sentinel/voice'
const MAX_RECORDING_MS = 25_000
const MAX_QUESTION = 1000
const RECORDING_TYPES = [
  ['audio/webm;codecs=opus', 'webm'],
  ['audio/ogg;codecs=opus', 'ogg'],
  ['audio/mp4', 'mp4'],
]
const STATUS = {
  briefing: 'Preparing the incident briefing…',
  microphone: 'Waiting for microphone access…',
  recording: 'Recording. Select Stop and send when you finish.',
  answering: 'Preparing your answer…',
}

function stopTracks(stream) {
  stream?.getTracks().forEach(track => track.stop())
}

function VoiceConversation({ run }) {
  const [phase, setPhase] = useState('idle')
  const [briefing, setBriefing] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [turns, setTurns] = useState([])
  const [question, setQuestion] = useState('')
  const [error, setError] = useState('')
  const [audioNote, setAudioNote] = useState('')
  const [hasAudio, setHasAudio] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const player = useRef(null)
  const transcript = useRef(null)
  const active = useRef({})
  const headers = run ? { 'X-Safestral-Run-Key': run.key } : {}
  const ready = phase === 'ready'
  const recording = phase === 'recording'

  useEffect(() => {
    const resources = active.current
    const element = player.current
    return () => {
      resources.request?.abort()
      resources.microphone = null
      window.clearTimeout(resources.timer)
      if (resources.recorder) {
        resources.recorder.onerror = null
        resources.recorder.onstop = null
        if (resources.recorder.state === 'recording') resources.recorder.stop()
      }
      stopTracks(resources.stream)
      element.pause()
      if (resources.audioUrl) URL.revokeObjectURL(resources.audioUrl)
    }
  }, [])

  useEffect(() => {
    const log = transcript.current
    if (log) log.scrollTop = log.scrollHeight
  }, [turns])

  function playAudio(data) {
    const resources = active.current
    const element = player.current
    element.pause()
    element.removeAttribute('src')
    if (resources.audioUrl) URL.revokeObjectURL(resources.audioUrl)
    resources.audioUrl = null
    setHasAudio(false)
    setSpeaking(false)
    setAudioNote(data.audio_warning || '')
    if (!data.audio) return
    const bytes = Uint8Array.from(atob(data.audio), character => character.charCodeAt(0))
    const url = URL.createObjectURL(new Blob([bytes], { type: data.audio_type || 'audio/mpeg' }))
    resources.audioUrl = url
    element.src = url
    setHasAudio(true)
    element.play().catch(() => {
      if (player.current === element && resources.audioUrl === url && element.paused) {
        setAudioNote('Select Play to hear the audio.')
      }
    })
  }

  async function startBriefing() {
    const resources = active.current
    if (resources.request) return
    const controller = new AbortController()
    resources.request = controller
    setPhase('briefing')
    setError('')
    try {
      const data = await api(`${PATH}/briefing`, {
        method: 'POST', signal: controller.signal,
        headers: { ...headers, 'content-type': 'application/json' },
        body: JSON.stringify(run ? { run_id: run.id } : {}),
      })
      if (controller.signal.aborted) return
      setBriefing(data.briefing)
      setSourceUrl(data.source_url || '')
      setPhase('ready')
      playAudio(data)
    } catch (err) {
      if (!controller.signal.aborted) {
        setError(err.message)
        setPhase('idle')
      }
    } finally {
      resources.request = null
    }
  }

  async function sendQuestion({ clip, extension, text }) {
    const resources = active.current
    if (resources.request) return
    const controller = new AbortController()
    resources.request = controller
    player.current.pause()
    setError('')
    setAudioNote('')
    setPhase('answering')
    const form = new FormData()
    if (run) form.append('run_id', run.id)
    form.append('history', JSON.stringify(turns.slice(-4)))
    if (clip) form.append('audio', clip, `question.${extension}`)
    else form.append('question', text)
    try {
      const data = await api(`${PATH}/questions`, {
        method: 'POST', signal: controller.signal, headers, body: form,
      })
      if (controller.signal.aborted) return
      setTurns(current => [...current, { question: data.question, answer: data.answer }])
      if (!clip) setQuestion('')
      playAudio(data)
    } catch (err) {
      if (!controller.signal.aborted) setError(err.message)
    } finally {
      resources.request = null
      if (!controller.signal.aborted) setPhase('ready')
    }
  }

  async function startRecording() {
    const resources = active.current
    if (resources.microphone || resources.recorder || resources.request) return
    const format = RECORDING_TYPES.find(([mime]) => window.MediaRecorder?.isTypeSupported(mime))
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !format) {
      setError('Microphone recording needs a supported browser on HTTPS or localhost. You can type your question below.')
      return
    }
    const [mimeType, extension] = format
    const operation = {}
    resources.microphone = operation
    setPhase('microphone')
    setError('')
    setAudioNote('')
    player.current.pause()
    let stream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true } })
      if (resources.microphone !== operation) {
        stopTracks(stream)
        return
      }
      resources.stream = stream
      const recorder = new MediaRecorder(stream, { mimeType, audioBitsPerSecond: 48_000 })
      const chunks = []
      let failed = false
      resources.recorder = recorder
      recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data) }
      recorder.onerror = () => {
        failed = true
        setError('The recording stopped. Try the microphone again or type your question.')
      }
      recorder.onstop = () => {
        window.clearTimeout(resources.timer)
        stopTracks(stream)
        resources.stream = null
        resources.recorder = null
        if (failed || !chunks.length) {
          if (!failed) setError('The recording was empty. Try again.')
          setPhase('ready')
          return
        }
        const clip = new Blob(chunks, { type: recorder.mimeType || mimeType })
        sendQuestion({ clip, extension })
      }
      recorder.start()
      resources.timer = window.setTimeout(() => {
        if (recorder.state === 'recording') recorder.stop()
      }, MAX_RECORDING_MS)
      setPhase('recording')
    } catch (err) {
      stopTracks(stream)
      resources.stream = null
      resources.recorder = null
      if (resources.microphone !== operation) return
      setError(err.name === 'NotAllowedError'
        ? 'Microphone permission was denied. Allow it in your browser or type your question below.'
        : 'The microphone could not start. Check your input device or type your question below.')
      setPhase('ready')
    } finally {
      if (resources.microphone === operation) resources.microphone = null
    }
  }

  function stopRecording() {
    const recorder = active.current.recorder
    if (recorder?.state === 'recording') recorder.stop()
  }

  return (
    <>
      {phase === 'idle' && <button className="primary-action" type="button" onClick={startBriefing}>Start voice briefing</button>}
      <p className="voice-status" role="status">{STATUS[phase] || (speaking ? 'Playing audio…' : '')}</p>
      {briefing && (
        <>
          <div ref={transcript} className="voice-transcript" role="log" tabIndex={0}
            aria-label="Incident conversation" aria-relevant="additions">
            <div className="voice-message">
              <strong>Safestral</strong>
              <p>{briefing}</p>
              {sourceUrl && <a href={sourceUrl} className="text-action">Read the incident timeline</a>}
            </div>
            {turns.map((turn, index) => <div className="voice-turn" key={index}>
              <div className="voice-message voice-operator"><strong>You</strong><p>{turn.question}</p></div>
              <div className="voice-message"><strong>Safestral</strong><p>{turn.answer}</p></div>
            </div>)}
          </div>
          <div className="voice-controls">
            <button className={recording ? 'voice-recording-button' : 'primary-action'} type="button"
              disabled={!ready && !recording} onClick={recording ? stopRecording : startRecording}>
              {recording ? 'Stop and send' : 'Ask with microphone'}
            </button>
            <span className="field-note">Up to 25 seconds per question.</span>
          </div>
          <form className="voice-question" onSubmit={event => {
            event.preventDefault()
            if (ready && question.trim()) sendQuestion({ text: question.trim() })
          }}>
            <label className="visually-hidden" htmlFor="voice-question">Type a question about the incident</label>
            <input id="voice-question" value={question} onChange={event => setQuestion(event.target.value)}
              maxLength={MAX_QUESTION} placeholder="Or type a question" disabled={!ready} />
            <button className="secondary-action" disabled={!ready || !question.trim()} type="submit">Ask</button>
          </form>
        </>
      )}
      <audio ref={player} controls hidden={!hasAudio || !ready} aria-label="Play the latest Safestral response"
        onPlay={() => { setSpeaking(true); setAudioNote('') }} onPause={() => setSpeaking(false)}
        onEnded={() => setSpeaking(false)} onError={() => setAudioNote('The audio could not play. The transcript is available above.')} />
      {audioNote && <p className="field-note" role="status">{audioNote}</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
    </>
  )
}

export default function VoicePanel({ run, source, setSource }) {
  const activeRun = source === 'run' ? run : null
  return (
    <section id="incident-voice" className="voice-panel" aria-labelledby="voice-title">
      <div>
        <h2 id="voice-title">Incident voice briefing</h2>
        <p className="workspace-intro">Hear what happened, then ask about the evidence. Voxtral listens and speaks. Mistral Large 4 answers.</p>
      </div>
      <div className="workspace-card voice-card">
        <div className="voice-source">
          <label htmlFor="voice-source">Incident</label>
          <select id="voice-source" value={activeRun ? 'run' : 'public'} onChange={event => setSource(event.target.value)}>
            <option value="public">Hugging Face’s July 2026 incident</option>
            {run && <option value="run">{run.filename}</option>}
          </select>
        </div>
        <VoiceConversation key={activeRun?.id || 'public'} run={activeRun} />
        <p className="field-note voice-privacy">Recordings and questions are sent to Mistral. The microphone turns off after each question.</p>
      </div>
    </section>
  )
}
