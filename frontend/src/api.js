async function request(path, opts = {}) {
  const headers = { ...(opts.headers || {}) }
  const res = await fetch(path, { cache: 'no-store', credentials: 'same-origin', ...opts, headers })
  if (!res.ok) {
    let detail = `${res.status}`
    try {
      const body = await res.json()
      detail = body.detail || detail
    } catch { /* keep status */ }
    throw new Error(`Request failed (${detail})`)
  }
  return res.json()
}

export function api(path, opts = {}) {
  return request(path, opts)
}

export function apiPost(path, body) {
  return request(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body || {}),
  })
}

export function uploadForm(path, formData) {
  // do NOT set content-type; the browser adds the multipart boundary
  return request(path, { method: 'POST', body: formData })
}
