const BASE_URL = ''

async function handleResponse(res) {
  if (!res.ok) {
    let errorMessage = `HTTP error ${res.status}: ${res.statusText}`
    try {
      const errorData = await res.json()
      if (errorData?.detail) {
        errorMessage = typeof errorData.detail === 'string'
          ? errorData.detail
          : JSON.stringify(errorData.detail)
      }
    } catch {
      // Ignore JSON parse errors for non-JSON response bodies
    }
    throw new Error(errorMessage)
  }
  return res.json()
}

export async function fetchHealth() {
  const res = await fetch(`${BASE_URL}/api/health`)
  return handleResponse(res)
}

export async function fetchDocuments() {
  const res = await fetch(`${BASE_URL}/api/documents`)
  return handleResponse(res)
}

export async function fetchDocumentDetail(docId) {
  const res = await fetch(`${BASE_URL}/api/documents/${encodeURIComponent(docId)}`)
  return handleResponse(res)
}

export async function fetchScenarios() {
  const res = await fetch(`${BASE_URL}/api/scenarios`)
  return handleResponse(res)
}

export async function submitQuery({
  query,
  asOfDate,
  as_of_date,
  pipeline = 'P3_FullSystem',
  useLlm = false,
  use_llm,
  model = 'qwen2.5:3b',
}) {
  const res = await fetch(`${BASE_URL}/api/query`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      query,
      as_of_date: asOfDate || as_of_date,
      pipeline,
      use_llm: use_llm !== undefined ? use_llm : useLlm,
      model,
    }),
  })
  return handleResponse(res)
}
