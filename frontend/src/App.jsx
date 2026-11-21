import { useState } from 'react'
import './App.css'
import InvestigationPanel from './panels/InvestigationPanel.jsx'
import ForecastChart from './panels/ForecastChart.jsx'
import VoicePanel from './panels/VoicePanel.jsx'
import useInvestigation from './panels/useInvestigation.js'
import { sampleInvestigation, sampleTrace, investigationStages } from './panels/investigationDemo.js'

const previewCandidates = sampleInvestigation(investigationStages.length).result.candidates

const workflow = [
  ['Le Chonk (Mistral Large 4)', 'Find the signal', 'Compares the metrics found in the incident files and explains which one to monitor.'],
  ['Shieldstral + Timestral', 'Detect deviations', 'Shieldstral checks the baseline. Timestral forecasts the expected range and sustained departures become candidates.'],
  ['Shieldstral + Mistral Small 4', 'Decide what warrants triage', 'Shieldstral checks every candidate. Mistral Small 4 investigates those that pass and suggests what to check next.'],
]

export default function App() {
  const investigation = useInvestigation()
  const [voiceSource, setVoiceSource] = useState('public')

  function startSample() {
    investigation.startSample()
    const activity = document.getElementById('run-activity')
    activity?.focus({ preventScroll: true })
    activity?.scrollIntoView({ block: 'start' })
  }

  return (
    <>
      <header className="site-header">
        <div className="site-width header-content">
          <a className="brand" href="#" aria-label="Safestral home">
            <span className="brand-mark"><img src="/safetral-mark-64.png" alt="" width="28" height="30" /></span>
            Safestral
          </a>
          <nav aria-label="Site"><a href="#how-it-works">How it works</a></nav>
        </div>
      </header>
      <main className="site-width">
        <section className="home-hero" aria-labelledby="home-title">
          <div className="home-copy">
            <p className="section-label">AI incident monitoring</p>
            <h1 id="home-title">Turn an incident into a monitor.</h1>
            <p className="hero-description">Upload incident evidence to see which metric Safestral selects, what the forecast flags, and how the model reviews determine which episodes warrant triage.</p>
            <div className="hero-actions">
              <a className="primary-action" href="#investigate">Upload an incident</a>
              <button className="text-action" type="button" onClick={startSample} disabled={investigation.busy}>Try a sample replay</button>
              <a className="text-action" href="#incident-voice">Try the voice demo</a>
            </div>
          </div>
          <div className="hero-preview" aria-label="Sample monitor preview">
            <div className="preview-header">
              <div>
                <p className="preview-kicker">Sample replay</p>
                <p className="preview-title">Gateway response time <small>ms</small></p>
              </div>
              <span className="preview-status"><i /> One alert</span>
            </div>
            <ForecastChart trace={sampleTrace} candidates={previewCandidates} label="Sample gateway response time in milliseconds" preview />
            <div className="preview-note">
              <span>14:38 UTC</span>
              <p>Response time peaked at <strong>412 ms</strong> and stayed above the expected range for five minutes.</p>
            </div>
          </div>
        </section>

        <section id="how-it-works" className="how-section" aria-labelledby="how-title">
          <h2 id="how-title" className="section-label">How it works</h2>
          <div className="workflow">
            {workflow.map(([model, title, description]) => (
              <article key={title}>
                <p className="workflow-model">{model}</p>
                <h3>{title}</h3>
                <p>{description}</p>
              </article>
            ))}
          </div>
        </section>

        <InvestigationPanel investigation={investigation} openVoice={() => setVoiceSource('run')} />
        <VoicePanel run={investigation.voiceRun} source={voiceSource} setSource={setVoiceSource} />

        <footer className="site-footer">
          <div>
            <h2>Planned integrations</h2>
            <p>Connect a database to monitor incoming metrics and route alerts to an on-call rotation.</p>
          </div>
          <span>Le Chonk in London</span>
        </footer>
      </main>
    </>
  )
}
