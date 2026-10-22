import React, { useEffect, useState } from 'react'
import { Calendar, AlertTriangle, Sparkles, Loader2 } from 'lucide-react'

import AnalysisPanel from './components/AnalysisPanel'
import CorpusReader from './components/CorpusReader'
import PrintReport from './components/PrintReport'
import Sidebar from './components/Sidebar'
import { Card, Legend, Pill } from './ui/primitives'
import {
  fetchDocumentDetail,
  fetchDocuments,
  fetchHealth,
  fetchScenarios,
  submitQuery,
} from './services/api'

const FALLBACK_SCENARIOS = [
  {
    name: 'Kịch bản 1: Xuất khẩu phần mềm năm 2026 (Thuế suất 0%)',
    query:
      'Doanh nghiệp tại Cần Thơ xuất khẩu phần mềm sang Nhật Bản vào tháng 03/2026 áp dụng thuế suất thuế GTGT bao nhiêu?',
    as_of_date: '2026-03-20',
    badge_color: 'green',
  },
  {
    name: 'Kịch bản 2: Bẫy thời gian áp dụng Luật 48 trước ngày 01/07/2025',
    query:
      'Doanh nghiệp xuất khẩu dịch vụ vào tháng 05/2024 áp dụng điều kiện theo Luật 48/2024 có được không?',
    as_of_date: '2024-05-15',
    badge_color: 'red',
  },
  {
    name: 'Kịch bản 3: Sửa đổi hóa đơn chứng từ theo Nghị định 144/2026',
    query:
      'Quy định về hóa đơn chứng từ đối với dịch vụ xuất khẩu theo Điều 9 Nghị định 181/2025 áp dụng như thế nào vào tháng 08/2026?',
    as_of_date: '2026-08-10',
    badge_color: 'yellow',
  },
]

export default function App() {
  const [health, setHealth] = useState({
    status: 'healthy',
    neo4j_connected: true,
    version: '1.0.0',
  })
  const [documents, setDocuments] = useState([])
  const [selectedDocId, setSelectedDocId] = useState('LUAT_48_2024')
  const [currentDocDetail, setCurrentDocDetail] = useState(null)
  const [docLoading, setDocLoading] = useState(false)

  const [scenarios, setScenarios] = useState(FALLBACK_SCENARIOS)
  const [selectedScenarioIdx, setSelectedScenarioIdx] = useState(0)

  const [query, setQuery] = useState(FALLBACK_SCENARIOS[0].query)
  const [asOfDate, setAsOfDate] = useState(FALLBACK_SCENARIOS[0].as_of_date)
  const [pipeline, setPipeline] = useState('P3_FullSystem')
  const [useLlm, setUseLlm] = useState(false)
  const [ollamaOnline, setOllamaOnline] = useState(false)

  const [queryLoading, setQueryLoading] = useState(false)
  const [queryResult, setQueryResult] = useState(null)
  const [activeArticleIds, setActiveArticleIds] = useState([])
  const [errorMessage, setErrorMessage] = useState(null)

  // 1. Initial Load: Health, Scenarios, Documents
  useEffect(() => {
    async function initData() {
      try {
        const h = await fetchHealth()
        setHealth(h)
      } catch {
        // Fallback healthy state
      }

      try {
        const scList = await fetchScenarios()
        if (scList && scList.length > 0) {
          setScenarios(scList)
        }
      } catch {
        // Fallback scenarios
      }

      try {
        const docs = await fetchDocuments()
        if (docs && docs.length > 0) {
          setDocuments(docs)
          setSelectedDocId(docs[0].id)
        }
      } catch {
        // Default documents handled inside CorpusReader
      }
    }
    initData()
  }, [])

  // 2. Fetch Document Detail when selectedDocId changes
  useEffect(() => {
    async function loadDetail() {
      if (!selectedDocId) return
      setDocLoading(true)
      try {
        const detail = await fetchDocumentDetail(selectedDocId)
        setCurrentDocDetail(detail)
      } catch {
        setCurrentDocDetail(null)
      } finally {
        setDocLoading(false)
      }
    }
    loadDetail()
  }, [selectedDocId])

  // 3. Scenario Selection Handler
  const handleSelectScenario = (idx) => {
    setSelectedScenarioIdx(idx)
    const sc = scenarios[idx]
    if (sc) {
      setQuery(sc.query)
      setAsOfDate(sc.as_of_date)
    }
  }

  // 4. Submit Query Handler
  const handleRunQuery = async (e) => {
    if (e) e.preventDefault()
    if (!query.trim() || !asOfDate) return

    setQueryLoading(true)
    setErrorMessage(null)

    try {
      const res = await submitQuery({
        query: query.trim(),
        asOfDate,
        pipeline,
        useLlm,
        model: 'qwen2.5:3b',
      })
      setQueryResult(res)
      setActiveArticleIds(res.active_article_ids || res.citations || [])

      // Auto-switch document if first cited article belongs to another doc
      const firstCite = (res.active_article_ids || res.citations || [])[0]
      if (firstCite && documents.length > 0) {
        for (const doc of documents) {
          if (firstCite.startsWith(doc.id)) {
            setSelectedDocId(doc.id)
            break
          }
        }
      }
    } catch (err) {
      setErrorMessage(err.message || 'Lỗi kết nối tới máy chủ API.')
    } finally {
      setQueryLoading(false)
    }
  }

  // 5. Click on Citation Handler (Jumps directly to document & article)
  const handleCitationClick = (citationId) => {
    if (!citationId) return
    const cleanId = citationId.replace(/[\[\]]/g, '')

    if (documents.length > 0) {
      for (const doc of documents) {
        if (cleanId.startsWith(doc.id)) {
          if (selectedDocId !== doc.id) {
            setSelectedDocId(doc.id)
          }
          break
        }
      }
    }

    setActiveArticleIds([cleanId])
    setTimeout(() => {
      const el = document.getElementById(`art-${cleanId}`)
      if (el && typeof el.scrollIntoView === 'function') {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }
    }, 50)
  }

  const handleNewQuery = () => {
    setQuery('')
    setQueryResult(null)
    setActiveArticleIds([])
    setSelectedScenarioIdx(null)
  }

  return (
    <div className="flex h-screen bg-[#FBFBFB] font-sans text-zinc-900 antialiased overflow-hidden selection:bg-zinc-200">
      {/* 1. Harvey Minimal Dark Sidebar */}
      <Sidebar
        scenarios={scenarios}
        selectedScenario={selectedScenarioIdx}
        onSelectScenario={handleSelectScenario}
        pipeline={pipeline}
        onSelectPipeline={setPipeline}
        useLlm={useLlm}
        onToggleLlm={setUseLlm}
        ollamaOnline={ollamaOnline}
        health={health}
        onNewQuery={handleNewQuery}
      />

      {/* 2. Main Work Surface (Harvey Command Center) */}
      <main className="flex-1 flex flex-col min-w-0 overflow-hidden bg-paper">
        {/* Top Minimal Harvey Header */}
        <header className="px-8 pt-7 pb-4 flex flex-col md:flex-row md:items-center justify-between gap-4 flex-shrink-0 print:hidden bg-paper">
          <div>
            <h1 className="font-serif text-[30px] leading-tight text-ink">
              Trung tâm Thẩm định & Tra cứu Thuế GTGT
            </h1>
            <p className="mt-1 text-[11px] text-muted">
              5 văn bản luật · dữ liệu đóng băng 30/09/2026
            </p>
          </div>

          {/* Right Action Tools (Harvey Style Pill Button & Actions) */}
          <div className="flex items-center gap-3">
            {queryResult && (
              <PrintReport
                query={query}
                asOfDate={asOfDate}
                pipeline={pipeline}
                result={queryResult}
              />
            )}

            <button
              type="button"
              onClick={handleRunQuery}
              disabled={queryLoading || !query.trim()}
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-full text-xs font-medium text-white bg-black hover:bg-zinc-800 disabled:bg-zinc-300 transition-all cursor-pointer"
            >
              {queryLoading ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  <span>Đang suy luận...</span>
                </>
              ) : (
                <>
                  <span className="w-4 h-4 rounded-full bg-white/20 text-[10px] flex items-center justify-center font-serif font-bold">
                    H
                  </span>
                  <span>Tra cứu & Thẩm định</span>
                </>
              )}
            </button>
          </div>
        </header>

        {/* State Strip: current selection + backend health */}
        <div className="flex items-center justify-between gap-4 px-8 py-2.5 border-b border-hairline flex-shrink-0">
          <p className="text-[11px] text-muted truncate">
            {selectedScenarioIdx !== null
              ? scenarios[selectedScenarioIdx]?.name
              : 'Chưa chọn kịch bản'}
            {' · '}
            {asOfDate}
            {' · '}
            {pipeline}
          </p>
          <Legend tone={health.neo4j_connected ? 'ok' : 'danger'}>Neo4j 5.26</Legend>
        </div>

        {/* Split-View Workspace: 45% Corpus Reader (Left) | 55% Analysis (Right) */}
        <div className="flex-1 px-8 py-3 flex flex-col lg:flex-row gap-6 overflow-hidden min-h-0">
          {/* CỘT TRÁI (45%): TRÌNH ĐỌC VĂN BẢN PHÁP QUY GỐC */}
          <div
            id="pane-corpus"
            className="w-full lg:w-[45%] h-full flex flex-col min-w-0"
          >
            <CorpusReader
              documents={documents}
              selectedDocId={selectedDocId}
              onSelectDoc={setSelectedDocId}
              currentDocDetail={currentDocDetail}
              activeArticleIds={activeArticleIds}
              loading={docLoading}
              className="w-full h-full"
            />
          </div>

          {/* CỘT PHẢI (55%): KHUNG NHẬP LIỆU STUDIO & THẨM ĐỊNH RỦI RO */}
          <section
            id="pane-analysis"
            aria-label="Legal Reasoning and Risk Inspector"
            className="w-full lg:w-[55%] h-full flex flex-col overflow-y-auto min-w-0 space-y-4 pr-1"
          >
            {/* Harvey Studio Prompt Box — bare input on canvas, not a card */}
            <form onSubmit={handleRunQuery} className="flex-shrink-0 space-y-2.5">
              <div className="flex items-center justify-between">
                <label
                  htmlFor="query-input"
                  className="text-[11px] font-medium text-muted"
                >
                  Yêu cầu tư vấn / Tình huống thuế
                </label>
                <Pill>Strict Grounding</Pill>
              </div>
              <textarea
                id="query-input"
                rows={3}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Nhập câu hỏi nghiệp vụ hoặc chọn kịch bản mẫu từ thanh bên..."
                className="w-full px-3.5 py-3 rounded-md border border-hairline bg-surface focus:bg-surface focus:border-ink focus:ring-0 text-sm text-ink placeholder:text-muted transition-colors resize-none leading-relaxed"
              />
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <Calendar className="w-3.5 h-3.5 text-muted" strokeWidth={1.5} />
                  <span className="text-[11px] text-muted font-medium">
                    Thời điểm diễn ra sự việc:
                  </span>
                  <input
                    type="date"
                    value={asOfDate}
                    onChange={(e) => setAsOfDate(e.target.value)}
                    className="px-2 py-1 rounded-md border border-hairline bg-surface text-[11px] font-mono font-medium text-ink focus:border-ink focus:outline-none"
                  />
                </div>

                <span className="text-[11px] text-muted font-mono">
                  {pipeline}
                </span>
              </div>
            </form>

            {/* Error Notice */}
            {errorMessage && (
              <div className="border-l-2 border-l-rose-500 pl-3 text-[12px] text-body flex items-start gap-2">
                <AlertTriangle className="w-3.5 h-3.5 text-rose-600 flex-shrink-0 mt-0.5" strokeWidth={1.5} />
                <div>
                  <span className="font-semibold text-ink">Không thể hoàn tất tra cứu: </span>
                  <span>{errorMessage}</span>
                </div>
              </div>
            )}

            {/* Results Area */}
            {queryResult ? (
              <div className="space-y-5 animate-fadeIn pb-6">
                <AnalysisPanel result={queryResult} onCitationClick={handleCitationClick} />
              </div>
            ) : (
              /* Minimalist Empty State (Harvey Style) */
              <Card className="flex flex-col items-center justify-center py-12 text-center">
                <div className="w-10 h-10 rounded-full bg-neutral-100 flex items-center justify-center mb-3">
                  <Sparkles className="w-5 h-5 text-neutral-400" strokeWidth={1.5} />
                </div>
                <h2 className="font-serif text-base font-normal text-ink">
                  Sẵn sàng phân tích quy định pháp lý
                </h2>
                <p className="mt-1.5 text-xs max-w-sm text-muted leading-relaxed">
                  Chọn một kịch bản từ thanh bên hoặc nhập câu hỏi cụ thể kèm mốc thời gian để hệ thống lọc đồ thị và thẩm định rủi ro.
                </p>
              </Card>
            )}
          </section>
        </div>
      </main>
    </div>
  )
}
