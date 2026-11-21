import { useEffect, useState } from 'react'
import { speak, voiceStatus } from '../voice'

export default function VoicePanel() {
  const [voice, setVoice] = useState(null)
  const [text, setText] = useState('Hello, I am Bea Cribella.')
  const [voiceId, setVoiceId] = useState('')
  const [speaking, setSpeaking] = useState(false)
  const [note, setNote] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    voiceStatus(controller.signal)
      .then((data) => {
        setVoice(data.voice)
        if (data.voice.voice_id) setVoiceId(data.voice.voice_id)
      })
      .catch(() => setVoice({ enabled: false, model: '', voice_id: null }))
    return () => controller.abort()
  }, [])

  async function onSpeak() {
    setSpeaking(true)
    setNote('')
    try {
      await speak(text, voiceId)
    } catch (error) {
      setNote(error.message)
    } finally {
      setSpeaking(false)
    }
  }

  return (
    <section className="panel voice-panel" aria-label="Voice output">
      <div className="card-top"><h2>Voice — Bea Cribella</h2><span className={`badge ${voice?.enabled ? 'ready' : ''}`}>{voice ? (voice.enabled ? 'Voice ready' : 'Voice off') : 'Checking…'}</span></div>
      <textarea value={text} onChange={(event) => setText(event.target.value)} aria-label="Text to speak" />
      <input value={voiceId} onChange={(event) => setVoiceId(event.target.value)} placeholder="Voice id (optional; defaults to the server-configured voice)" aria-label="Voice id" />
      <p className="status-note" role="status">{note || (voice?.enabled ? `Model ${voice.model}${voice.voice_id ? ` · default voice ${voice.voice_id}` : ''}` : 'Set MISTRAL_API_KEY on the server to enable voice.')}</p>
      <button type="button" disabled={speaking || !text.trim()} onClick={onSpeak}>{speaking ? 'Speaking…' : 'Speak as Bea Cribella'} <span aria-hidden="true">↗</span></button>
    </section>
  )
}
