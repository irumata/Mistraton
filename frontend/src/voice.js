export async function voiceStatus(signal) {
  const response = await fetch('/api/voice/status', { signal, cache: 'no-store' })
  if (!response.ok) throw new Error(`Request failed (${response.status})`)
  return response.json()
}

export async function speak(text, voiceId) {
  const response = await fetch('/api/voice/say', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(voiceId ? { text, voice_id: voiceId } : { text }),
  })
  if (!response.ok) throw new Error(`Voice request failed (${response.status})`)
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const audio = new Audio(url)
  audio.onended = () => URL.revokeObjectURL(url)
  await audio.play()
  return audio
}
