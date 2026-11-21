import { useState } from 'react'
import { apiPost } from '../api.js'

export default function TimestralPanel() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [windowText, setWindowText] = useState('10,10,11,10,10,11,10,10,25,60,120,300,600,900')

  async function send(content) {
    const next = [...messages, { role: 'user', content }]
    setMessages(next); setInput(''); setBusy(true); setError('')
    try {
      const res = await apiPost('/sentinel/timestral/chat', { messages: next, max_new_tokens: 256 })
      setMessages([...next, { role: 'assistant', content: res.reply }])
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  function analyzeWindow() {
    const values = windowText.split(',').map(s => Number(s.trim())).filter(v => !Number.isNaN(v))
    send(JSON.stringify({ metric: 'ad-hoc', window_start: new Date().toISOString(), values }))
  }

  return (
    <section className="panel">
      <h2>Timestral (fine-tuned, self-hosted on this Colab GPU)</h2>
      <p className="muted">
        Chats with the QLoRA checkpoint in <code>artifacts/timestral-hf</code>. If it 503s, run the
        <code> finetune-timestral </code> pipeline stage first (GPU runtime).
      </p>
      <div className="row">
        <textarea rows={2} style={{ flex: 1 }} value={windowText} onChange={e => setWindowText(e.target.value)}
          placeholder="comma-separated window values" />
        <button onClick={analyzeWindow} disabled={busy}>Analyze window</button>
      </div>
      <div className="chat">
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}><b>{m.role}</b><pre>{m.content}</pre></div>
        ))}
      </div>
      <div className="row">
        <input style={{ flex: 1 }} value={input} onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') send(input) }} placeholder="ask Timestral…" />
        <button disabled={busy || !input} onClick={() => send(input)}>{busy ? '…' : 'Send'}</button>
      </div>
      {error && <p className="error">{error}</p>}
    </section>
  )
}
