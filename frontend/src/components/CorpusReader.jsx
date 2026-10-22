import React, { useEffect } from 'react'
import { Calendar, FileText, Loader2 } from 'lucide-react'
import { Card, Legend } from '../ui/primitives'

const DEFAULT_DOCUMENTS = [
  {
    id: 'LUAT_48_2024',
    code: '48/2024/QH15',
    title: 'Luật Thuế giá trị gia tăng số 48/2024/QH15',
    valid_from: '2025-07-01',
    valid_to: '9999-12-31',
    status: 'ACTIVE',
  },
  {
    id: 'ND_181_2025',
    code: '181/2025/NĐ-CP',
    title: 'Nghị định 181/2025/NĐ-CP quy định chi tiết thi hành Luật Thuế giá trị gia tăng',
    valid_from: '2025-07-01',
    valid_to: '9999-12-31',
    status: 'ACTIVE',
  },
  {
    id: 'TT_69_2025',
    code: '69/2025/TT-BTC',
    title: 'Thông tư 69/2025/TT-BTC hướng dẫn thực hiện Nghị định 181/2025/NĐ-CP',
    valid_from: '2025-07-01',
    valid_to: '9999-12-31',
    status: 'ACTIVE',
  },
  {
    id: 'ND_359_2025',
    code: '359/2025/NĐ-CP',
    title: 'Nghị định 359/2025/NĐ-CP sửa đổi, bổ sung một số điều của Nghị định 181/2025/NĐ-CP',
    valid_from: '2026-01-01',
    valid_to: '9999-12-31',
    status: 'ACTIVE',
  },
  {
    id: 'ND_144_2026',
    code: '144/2026/NĐ-CP',
    title: 'Nghị định 144/2026/NĐ-CP sửa đổi, bổ sung một số điều của Nghị định 181/2025/NĐ-CP',
    valid_from: '2026-03-15',
    valid_to: '9999-12-31',
    status: 'ACTIVE',
  },
]

const CANONICAL_DOC_TITLES = {
  LUAT_48_2024: 'Luật Thuế giá trị gia tăng số 48/2024/QH15',
  ND_181_2025: 'Nghị định 181/2025/NĐ-CP quy định chi tiết thi hành Luật Thuế giá trị gia tăng',
  TT_69_2025: 'Thông tư 69/2025/TT-BTC hướng dẫn thực hiện Nghị định 181/2025/NĐ-CP',
  ND_359_2025: 'Nghị định 359/2025/NĐ-CP sửa đổi, bổ sung một số điều của Nghị định 181/2025/NĐ-CP',
  ND_144_2026: 'Nghị định 144/2026/NĐ-CP sửa đổi, bổ sung một số điều của Nghị định 181/2025/NĐ-CP',
}

const DOC_DISPLAY_NAMES = {
  LUAT_48_2024: 'Luật 48/2024',
  ND_181_2025: 'Nghị định 181',
  TT_69_2025: 'Thông tư 69',
  ND_359_2025: 'Nghị định 359',
  ND_144_2026: 'Nghị định 144',
}

function getDocStatus(doc) {
  if (doc?.status) return doc.status
  if (doc?.valid_to && doc.valid_to !== '9999-12-31') return 'AMENDED'
  return 'ACTIVE'
}

function formatArticleCode(code) {
  if (!code) return ''
  return code.replace(/^Dieu\s*/i, 'Điều ')
}

function formatClauseCode(code) {
  if (!code) return ''
  return code.replace(/^Khoan\s*/i, 'Khoản ')
}

function formatPointCode(code) {
  if (!code) return ''
  return code.replace(/^Diem\s*/i, 'Điểm ')
}

export function CorpusReader({
  documents = DEFAULT_DOCUMENTS,
  selectedDocId,
  onSelectDoc,
  currentDocDetail,
  activeArticleIds = [],
  loading = false,
  className = '',
}) {
  const docsList = documents && documents.length > 0 ? documents : DEFAULT_DOCUMENTS
  const currentId = selectedDocId || currentDocDetail?.id || docsList[0]?.id

  const activeDocMeta =
    docsList.find((d) => d.id === currentId) || currentDocDetail || docsList[0] || {}

  const fullTitle =
    CANONICAL_DOC_TITLES[currentId] ||
    currentDocDetail?.title ||
    activeDocMeta?.title ||
    'Văn bản quy phạm pháp luật'

  const docCode = currentDocDetail?.code || activeDocMeta?.code || currentId || ''
  const validFrom = currentDocDetail?.valid_from || activeDocMeta?.valid_from || '—'
  const validTo = currentDocDetail?.valid_to || activeDocMeta?.valid_to || '9999-12-31'
  const docStatus = getDocStatus(currentDocDetail || activeDocMeta)

  const effectiveDateText = `Hiệu lực: ${validFrom} → ${
    validTo === '9999-12-31' ? 'Hiện hành' : validTo
  }`

  useEffect(() => {
    if (activeArticleIds && activeArticleIds.length > 0) {
      for (const aid of activeArticleIds) {
        const el = document.getElementById(`art-${aid}`)
        if (el) {
          if (typeof el.scrollIntoView === 'function') {
            el.scrollIntoView({ behavior: 'smooth', block: 'center' })
          }
          break
        }
      }
    }
  }, [activeArticleIds, currentDocDetail])

  const chapters = currentDocDetail?.chapters || []
  const hasChapters =
    chapters.length > 0 && chapters.some((ch) => ch.articles && ch.articles.length > 0)
  const directArticles = currentDocDetail?.articles || []

  const renderArticle = (art) => {
    if (!art) return null
    const isActive = Boolean(activeArticleIds && activeArticleIds.includes(art.id))
    const formattedCode = formatArticleCode(art.code)
    const headerTitle =
      formattedCode && art.title
        ? art.title.startsWith('Điều')
          ? art.title
          : `${formattedCode}. ${art.title}`
        : formattedCode || art.title || ''

    return (
      <div
        key={art.id}
        id={`art-${art.id}`}
        className={`py-3.5 pl-3 border-b border-hairline border-l-2 last:border-b-0 transition-colors ${
          isActive ? 'border-l-accent bg-[#FEFCE8]' : 'border-l-transparent'
        }`}
      >
        <h4
          className={`text-sm leading-snug font-sans ${
            isActive ? 'font-semibold text-ink' : 'font-medium text-zinc-900'
          }`}
        >
          {headerTitle}
        </h4>

        {art.lead && (
          <p className="mt-1.5 text-zinc-600 leading-relaxed text-[13px] legal-content">
            {art.lead}
          </p>
        )}

        {art.clauses && art.clauses.length > 0 && (
          <div className="space-y-2 mt-2">
            {art.clauses.map((clause, cIdx) => {
              const clauseLabel = formatClauseCode(clause.code)
              return (
                <div
                  key={clause.id || `cls-${cIdx}`}
                  id={clause.id ? `cls-${clause.id}` : undefined}
                  className="text-zinc-700 leading-relaxed text-[13px] legal-content"
                >
                  <p>
                    {clauseLabel && (
                      <strong className="font-semibold text-zinc-900 mr-1.5">
                        {clauseLabel}:
                      </strong>
                    )}
                    <span>{clause.content}</span>
                  </p>

                  {clause.points && clause.points.length > 0 && (
                    <div className="space-y-1 mt-1 pl-4 border-l border-zinc-100">
                      {clause.points.map((point, pIdx) => {
                        const pointLabel = formatPointCode(point.code)
                        return (
                          <div
                            key={point.id || `pt-${pIdx}`}
                            id={point.id ? `pt-${point.id}` : undefined}
                            className="text-zinc-600 text-[13px] leading-relaxed"
                          >
                            <p>
                              {pointLabel && (
                                <strong className="font-medium text-zinc-800 mr-1.5">
                                  {pointLabel}:
                                </strong>
                              )}
                              <span>{point.content}</span>
                            </p>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}

        {!art.lead && (!art.clauses || art.clauses.length === 0) && art.content && (
          <p className="mt-1.5 text-zinc-700 leading-relaxed text-[13px] legal-content">
            {art.content}
          </p>
        )}
      </div>
    )
  }

  return (
    <Card
      aria-label="Legal Corpus Reader"
      className={`w-full h-full !p-0 overflow-hidden flex flex-col select-text min-w-0 ${className}`}
    >
      {/* 1. Harvey Underline Tabs (Never clipped off) */}
      <div className="border-b border-hairline bg-surface px-6 pt-3 flex-shrink-0">
        <div className="flex items-center gap-6 overflow-x-auto scrollbar-none">
          {docsList.map((doc) => {
            const isTabActive = doc.id === currentId
            const displayName = DOC_DISPLAY_NAMES[doc.id] || doc.code || doc.id

            return (
              <button
                key={doc.id}
                type="button"
                onClick={() => onSelectDoc?.(doc.id)}
                className={`pb-3 text-xs whitespace-nowrap transition-colors border-b-2 cursor-pointer ${
                  isTabActive
                    ? 'border-zinc-950 text-zinc-950 font-semibold'
                    : 'border-transparent text-zinc-400 hover:text-zinc-800 hover:border-zinc-300 font-medium'
                }`}
              >
                <span>{displayName}</span>
              </button>
            )
          })}
        </div>
      </div>

      {/* 2. Document Editorial Header */}
      <div className="border-b border-hairline bg-surface px-6 py-4 flex-shrink-0">
        <div className="flex items-center justify-between gap-3 mb-1.5">
          <span className="text-[11px] font-mono text-muted">{docCode}</span>
          <Legend tone={docStatus === 'ACTIVE' ? 'ok' : 'warn'}>
            {docStatus === 'ACTIVE' ? 'Hiện hành' : 'Đã sửa đổi'}
          </Legend>
        </div>
        <h2 className="font-serif text-lg text-ink font-normal leading-snug">
          {fullTitle}
        </h2>
        <div className="mt-1 flex items-center gap-1.5 text-[11px] text-muted">
          <Calendar className="w-3.5 h-3.5" />
          <span>{effectiveDateText}</span>
        </div>
      </div>

      {/* 3. Content Scroll Area */}
      <div className="flex-1 overflow-y-auto px-6 py-4 scroll-smooth scrollbar-thin">
        <div className="max-w-[65ch] mx-auto">
          {loading ? (
            <div className="p-8 space-y-4 animate-pulse">
              <div className="flex items-center gap-2 text-zinc-400 text-sm mb-4">
                <Loader2 className="w-4 h-4 animate-spin text-zinc-500" />
                <span>Đang tải nội dung văn bản quy phạm...</span>
              </div>
              <div className="h-4 bg-zinc-200 rounded w-1/3"></div>
              <div className="h-24 bg-zinc-100 rounded-md"></div>
              <div className="h-4 bg-zinc-200 rounded w-1/4"></div>
              <div className="h-32 bg-zinc-100 rounded-md"></div>
            </div>
          ) : hasChapters ? (
            <div className="space-y-6">
              {chapters.map((ch, chIdx) => (
                <section key={ch.id || `ch-${chIdx}`} className="space-y-1">
                  <div className="text-[11px] font-medium text-muted border-b border-hairline pb-1.5 pt-3 first:pt-0 mb-2">
                    {ch.title || ch.code}
                  </div>
                  <div className="space-y-0.5">
                    {ch.articles?.map((art) => renderArticle(art))}
                  </div>
                </section>
              ))}
            </div>
          ) : directArticles.length > 0 ? (
            <div className="space-y-0.5">
              {directArticles.map((art) => renderArticle(art))}
            </div>
          ) : (
            <div className="p-12 text-center text-zinc-400 text-sm">
              <FileText className="w-8 h-8 text-zinc-300 mx-auto mb-2" />
              <span>Chưa có dữ liệu chi tiết cho văn bản này.</span>
            </div>
          )}
        </div>
      </div>
    </Card>
  )
}

export default CorpusReader
