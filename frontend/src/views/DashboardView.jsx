import React from 'react'
import { Calendar, AlertTriangle, Sparkles, Loader2 } from 'lucide-react'

import AnalysisPanel from '../components/AnalysisPanel'
import CorpusReader from '../components/CorpusReader'
import PrintReport from '../components/PrintReport'
import { Card, Legend, Pill } from '../ui/primitives'

export function DashboardView({ state }) {
  const {
    health,
    scenarios,
    selectedScenario,
    query,
    onQueryChange,
    asOfDate,
    onAsOfDateChange,
    pipeline,
    queryLoading,
    queryResult,
    activeArticleIds,
    errorMessage,
    documents,
    selectedDocId,
    onSelectDoc,
    currentDocDetail,
    docLoading,
    onRunQuery,
    onCitationClick,
  } = state

  return (
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
            onClick={onRunQuery}
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
          {selectedScenario !== null
            ? scenarios[selectedScenario]?.name
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
            onSelectDoc={onSelectDoc}
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
          <form onSubmit={onRunQuery} className="flex-shrink-0 space-y-2.5">
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
              onChange={(e) => onQueryChange(e.target.value)}
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
                  onChange={(e) => onAsOfDateChange(e.target.value)}
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
              <AnalysisPanel result={queryResult} onCitationClick={onCitationClick} />
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
  )
}

export default DashboardView
