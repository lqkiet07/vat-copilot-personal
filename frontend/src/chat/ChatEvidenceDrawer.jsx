import React, { useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'

import CorpusReader from '../components/CorpusReader'
import { fetchDocumentDetail, fetchDocuments } from '../services/api'
import { formatArticleCode, formatValidity } from './ChatSourceList'

export function ChatEvidenceDrawer({ source, onClose }) {
  const [documents, setDocuments] = useState([])
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(false)
  const documentsRequested = useRef(false)
  const docId = source?.doc_id || ''

  useEffect(() => {
    if (!source || documentsRequested.current) return undefined
    documentsRequested.current = true
    let cancelled = false
    fetchDocuments()
      .then((list) => {
        if (!cancelled && Array.isArray(list)) setDocuments(list)
      })
      .catch(() => {
        if (!cancelled) setDocuments([])
      })
    return () => {
      cancelled = true
    }
  }, [source])

  useEffect(() => {
    if (!docId) {
      setDetail(null)
      setLoading(false)
      return undefined
    }
    let cancelled = false
    setLoading(true)
    setDetail(null)
    fetchDocumentDetail(docId)
      .then((data) => {
        if (!cancelled) setDetail(data)
      })
      .catch(() => {
        if (!cancelled) setDetail(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [docId])

  useEffect(() => {
    if (!source) return undefined
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') onClose?.()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [source, onClose])

  if (!source) return null

  const articleCode = formatArticleCode(source.code) || source.id

  return (
    <>
      <div
        className="fixed inset-0 bg-black/20 z-50"
        onClick={onClose}
        role="presentation"
        aria-hidden="true"
      />

      <aside
        className="fixed right-0 top-0 h-screen w-full max-w-[460px] bg-surface border-l border-hairline shadow-xl flex flex-col z-50"
        aria-label="Nguyên văn điều khoản"
      >
        <header className="flex items-start gap-3 border-b border-hairline px-4 py-3 shrink-0">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-mono text-[11px] text-accent">{articleCode}</span>
              <span className="text-[11px] text-muted">{formatValidity(source)}</span>
            </div>
            <h2 className="mt-1 font-serif text-[15px] leading-snug text-ink">{source.title}</h2>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="inline-flex items-center gap-1 rounded-lg border border-hairline px-2 py-1 text-[11px] text-body hover:text-ink hover:border-neutral-400 transition-colors cursor-pointer shrink-0"
          >
            <X className="w-3.5 h-3.5" strokeWidth={1.5} />
            <span>Đóng</span>
          </button>
        </header>

        <div className="flex-1 min-h-0 flex flex-col">
          {loading && (
            <p className="shrink-0 border-b border-hairline bg-paper px-4 py-1.5 text-[11px] text-muted">
              Đang tải nguyên văn điều khoản…
            </p>
          )}
          <div className="flex-1 min-h-0 overflow-hidden">
            <CorpusReader
              documents={documents}
              selectedDocId={docId}
              onSelectDoc={() => {}}
              currentDocDetail={detail}
              activeArticleIds={[source.id]}
              loading={loading}
              className="h-full"
            />
          </div>
        </div>
      </aside>
    </>
  )
}

export default ChatEvidenceDrawer
