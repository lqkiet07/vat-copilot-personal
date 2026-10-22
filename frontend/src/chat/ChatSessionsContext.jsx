import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

import {
  createSession,
  deleteSession,
  loadSessions,
  saveSessions,
} from './chatStore'

const ChatSessionsContext = createContext(null)

function createMessageId() {
  return globalThis.crypto?.randomUUID?.() ?? `m-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function loadInitialSessions() {
  const loaded = loadSessions()
  return loaded.length > 0 ? loaded : [createSession()]
}

export function ChatSessionsProvider({ children }) {
  const [sessions, setSessions] = useState(loadInitialSessions)
  const [activeId, setActiveId] = useState(() => sessions[0]?.id ?? null)

  useEffect(() => {
    if (sessions.length === 0) return
    saveSessions(sessions)
  }, [sessions])

  const newSession = useCallback(() => {
    const session = createSession()
    setSessions((prev) => [session, ...prev].slice(0, 20))
    setActiveId(session.id)
    return session.id
  }, [])

  const removeSession = useCallback(
    (id) => {
      const remaining = deleteSession(id, sessions)
      if (remaining.length === 0) {
        const fresh = createSession()
        setSessions([fresh])
        setActiveId(fresh.id)
        return
      }
      setSessions(remaining)
      if (activeId === id) setActiveId(remaining[0].id)
    },
    [sessions, activeId],
  )

  const appendMessage = useCallback((sessionId, message) => {
    const id = message.id ?? createMessageId()
    setSessions((prev) =>
      prev.map((session) => {
        if (session.id !== sessionId) return session
        const isFirstUserTurn = session.messages.length === 0 && message.role === 'user'
        return {
          ...session,
          title: isFirstUserTurn ? String(message.content).slice(0, 48) : session.title,
          messages: [...session.messages, { ...message, id }],
          updated_at: Date.now(),
        }
      }),
    )
    return id
  }, [])

  const updateSession = useCallback((sessionId, patch) => {
    setSessions((prev) =>
      prev.map((session) =>
        session.id === sessionId ? { ...session, ...patch, updated_at: Date.now() } : session,
      ),
    )
  }, [])

  const setMessages = useCallback((sessionId, messages) => {
    updateSession(sessionId, { messages })
  }, [updateSession])

  const activeSession = useMemo(
    () => sessions.find((session) => session.id === activeId) ?? sessions[0] ?? null,
    [sessions, activeId],
  )

  const value = useMemo(
    () => ({
      sessions,
      activeId: activeSession?.id ?? null,
      activeSession,
      selectSession: setActiveId,
      newSession,
      removeSession,
      appendMessage,
      updateSession,
      setMessages,
    }),
    [sessions, activeSession, newSession, removeSession, appendMessage, updateSession, setMessages],
  )

  return <ChatSessionsContext.Provider value={value}>{children}</ChatSessionsContext.Provider>
}

export function useChatSessions() {
  const context = useContext(ChatSessionsContext)
  if (!context) {
    throw new Error('useChatSessions must be used inside ChatSessionsProvider')
  }
  return context
}
