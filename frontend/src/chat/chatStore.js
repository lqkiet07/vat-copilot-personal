const STORAGE_KEY = 'vat_copilot_sessions_v1'
const MAX_SESSIONS = 20

export const DEFAULT_AS_OF_DATE = '2026-09-30'

export function loadSessions() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.slice(0, MAX_SESSIONS)
  } catch {
    return []
  }
}

export function saveSessions(sessions) {
  try {
    const list = Array.isArray(sessions) ? sessions.slice(0, MAX_SESSIONS) : []
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(list))
  } catch {
    // Storage unavailable: keep the in-memory state only.
  }
}

export function createSession() {
  const id = globalThis.crypto?.randomUUID?.() ?? `s-${Date.now()}`
  return {
    id,
    title: 'Cuộc tra cứu mới',
    messages: [],
    as_of_date: DEFAULT_AS_OF_DATE,
    updated_at: Date.now(),
  }
}

export function deleteSession(id, sessions) {
  if (!Array.isArray(sessions)) return []
  return sessions.filter((session) => session.id !== id)
}
