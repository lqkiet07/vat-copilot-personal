const BASE_URL = ''

function parseFrame(frame) {
  const trimmed = frame.trim()
  if (!trimmed) return null

  let event = 'message'
  const dataLines = []
  for (const line of trimmed.split('\n')) {
    if (line.startsWith('event:')) {
      event = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trim())
    }
  }
  if (dataLines.length === 0) return null

  const raw = dataLines.join('\n')
  try {
    return { event, data: JSON.parse(raw) }
  } catch {
    return { event, data: { raw } }
  }
}

export async function streamChat({
  messages,
  asOfDate,
  model = 'qwen2.5:7b',
  signal,
  onEvent,
}) {
  const res = await fetch(`${BASE_URL}/api/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      messages,
      as_of_date: asOfDate,
      model,
    }),
    signal,
  })

  if (!res.ok) {
    let detail = `HTTP error ${res.status}: ${res.statusText}`
    try {
      const data = await res.json()
      if (data?.detail) {
        detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
      }
    } catch {
      // Non JSON error body
    }
    throw new Error(detail)
  }

  if (!res.body) {
    throw new Error('Máy chủ không trả về dữ liệu trực tiếp.')
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let separator = buffer.indexOf('\n\n')
    while (separator !== -1) {
      const frame = buffer.slice(0, separator)
      buffer = buffer.slice(separator + 2)
      const parsed = parseFrame(frame)
      if (parsed) onEvent(parsed)
      separator = buffer.indexOf('\n\n')
    }
  }

  const tail = parseFrame(buffer)
  if (tail) onEvent(tail)
}
