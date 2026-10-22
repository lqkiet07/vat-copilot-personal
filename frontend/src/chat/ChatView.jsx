import React, { useCallback, useEffect, useRef, useState } from 'react'

import { fetchScenarios } from '../services/api'
import { streamChat } from './chatStream'
import { DEFAULT_AS_OF_DATE } from './chatStore'
import { useChatSessions } from './ChatSessionsContext'
import ChatMessage from './ChatMessage'
import ChatComposer from './ChatComposer'
import ChatEmptyState from './ChatEmptyState'

const CHAT_MODEL = 'qwen2.5:7b'

function toApiMessages(messages) {
  return messages
    .filter((message) => (message.role === 'user' || message.role === 'assistant') && message.content)
    .map((message) => ({ role: message.role, content: String(message.content).slice(0, 8000) }))
}

export function ChatView() {
  const { activeSession, activeId, appendMessage, updateSession, setMessages } = useChatSessions()
  const [input, setInput] = useState('')
  const [asOfDate, setAsOfDate] = useState(DEFAULT_AS_OF_DATE)
  const [streaming, setStreaming] = useState(false)
  const [activeSources, setActiveSources] = useState([])
  const [sampleQuestions, setSampleQuestions] = useState([])
  const [pending, setPending] = useState(null)

  const abortRef = useRef(null)
  const scrollRef = useRef(null)
  const sourcesRef = useRef([])
  const resolvedDateRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    fetchScenarios()
      .then((list) => {
        if (cancelled || !Array.isArray(list)) return
        setSampleQuestions(list.map((item) => item.query).filter(Boolean).slice(0, 3))
      })
      .catch(() => {
        /* The empty state falls back to hardcoded sample questions */
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    setAsOfDate(activeSession?.as_of_date || DEFAULT_AS_OF_DATE)
  }, [activeId])

  useEffect(() => {
    const node = scrollRef.current
    if (!node) return
    node.scrollTop = node.scrollHeight
  }, [pending?.message?.content, activeSession?.messages?.length])

  const handleAsOfDateChange = useCallback(
    (value) => {
      setAsOfDate(value)
      if (activeSession) updateSession(activeSession.id, { as_of_date: value })
    },
    [activeSession, updateSession],
  )

  const runRequest = useCallback(
    async (sessionId, history) => {
      const assistantId = `a-${Date.now()}-${Math.random().toString(16).slice(2)}`
      let partial = ''
      const controller = new AbortController()
      abortRef.current = controller
      setStreaming(true)
      setActiveSources([])
      sourcesRef.current = []
      resolvedDateRef.current = null
      setPending({
        sessionId,
        message: { id: assistantId, role: 'assistant', content: '', streaming: true },
      })

      const commit = (message) => {
        setPending(null)
        setStreaming(false)
        abortRef.current = null
        appendMessage(sessionId, { ...message, id: assistantId })
      }

      try {
        await streamChat({
          messages: history,
          asOfDate,
          model: CHAT_MODEL,
          signal: controller.signal,
          onEvent: ({ event, data }) => {
            if (event === 'meta') {
              sourcesRef.current = Array.isArray(data.sources) ? data.sources : []
              resolvedDateRef.current = data.as_of_date || null
              setActiveSources(sourcesRef.current)
            } else if (event === 'token') {
              partial += data.delta || ''
              setPending((current) =>
                current
                  ? { ...current, message: { ...current.message, content: partial } }
                  : current,
              )
            } else if (event === 'done') {
              commit({
                role: 'assistant',
                content: data.message || partial,
                citations: data.citations || [],
                sources: data.sources?.length ? data.sources : sourcesRef.current,
                risk_score: data.risk_score,
                risk_level: data.risk_level,
                warnings: data.warnings || [],
                llm_used: data.llm_used,
                in_scope: data.in_scope,
                as_of_date: data.as_of_date || resolvedDateRef.current,
              })
            } else if (event === 'error') {
              commit({
                role: 'assistant',
                content: data.message || 'Đã xảy ra lỗi khi tra cứu.',
                error: true,
              })
            }
          },
        })
      } catch (error) {
        if (error.name === 'AbortError') {
          commit({ role: 'assistant', content: partial, stopped: true })
        } else {
          commit({
            role: 'assistant',
            content: error.message || 'Không thể kết nối tới máy chủ tra cứu.',
            error: true,
          })
        }
      }
    },
    [appendMessage, asOfDate],
  )

  const send = useCallback(
    async (textOverride) => {
      const text = (textOverride ?? input).trim()
      if (!text || streaming || !activeSession) return

      const history = toApiMessages(activeSession.messages)
      const payload = [...history, { role: 'user', content: text }]

      setInput('')
      appendMessage(activeSession.id, { role: 'user', content: text })
      await runRequest(activeSession.id, payload)
    },
    [activeSession, appendMessage, input, runRequest, streaming],
  )

  const regenerate = useCallback(async () => {
    if (!activeSession || streaming) return
    const messages = activeSession.messages
    let lastUserIndex = -1
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      if (messages[index].role === 'user') {
        lastUserIndex = index
        break
      }
    }
    if (lastUserIndex === -1) return

    const kept = messages.slice(0, lastUserIndex + 1)
    setMessages(activeSession.id, kept)
    await runRequest(activeSession.id, toApiMessages(kept))
  }, [activeSession, runRequest, setMessages, streaming])

  const copyMessage = useCallback(async (content) => {
    try {
      await navigator.clipboard.writeText(content)
    } catch {
      /* The browser may deny clipboard access */
    }
  }, [])

  const messages = activeSession?.messages || []
  const visibleMessages =
    pending && pending.sessionId === activeId ? [...messages, pending.message] : messages
  const lastAssistantId = [...visibleMessages]
    .reverse()
    .find((message) => message.role === 'assistant')?.id

  return (
    <main className="flex-1 flex flex-col min-w-0 overflow-hidden bg-paper">
      <div ref={scrollRef} className="flex-1 overflow-y-auto min-h-0">
        <div className="max-w-[78ch] mx-auto px-6 py-6 space-y-5">
          {visibleMessages.length === 0 ? (
            <ChatEmptyState questions={sampleQuestions} onPick={(question) => send(question)} />
          ) : (
            visibleMessages.map((message) => (
              <React.Fragment key={message.id}>
                <ChatMessage
                  message={message}
                  isLastAssistant={message.id === lastAssistantId}
                  onCopy={copyMessage}
                  onRegenerate={regenerate}
                />
                {message.streaming && activeSources.length > 0 && (
                  <p className="text-[11px] text-muted">
                    Đang đối chiếu {activeSources.length} văn bản pháp luật tại mốc {asOfDate}
                  </p>
                )}
              </React.Fragment>
            ))
          )}
        </div>
      </div>

      <ChatComposer
        input={input}
        onInputChange={setInput}
        asOfDate={asOfDate}
        onAsOfDateChange={handleAsOfDateChange}
        onSend={() => send()}
        onStop={() => abortRef.current?.abort()}
        streaming={streaming}
      />
    </main>
  )
}

export default ChatView
