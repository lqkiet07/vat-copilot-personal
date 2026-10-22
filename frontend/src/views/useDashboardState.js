import { useEffect, useState } from 'react'

import {
  fetchDocumentDetail,
  fetchDocuments,
  fetchHealth,
  fetchScenarios,
  submitQuery,
} from '../services/api'

export const FALLBACK_SCENARIOS = [
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

export function useDashboardState() {
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
  const [ollamaOnline] = useState(false)

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

  return {
    health,
    scenarios,
    selectedScenario: selectedScenarioIdx,
    onSelectScenario: handleSelectScenario,
    pipeline,
    onSelectPipeline: setPipeline,
    useLlm,
    onToggleLlm: setUseLlm,
    ollamaOnline,
    documents,
    selectedDocId,
    onSelectDoc: setSelectedDocId,
    currentDocDetail,
    docLoading,
    query,
    onQueryChange: setQuery,
    asOfDate,
    onAsOfDateChange: setAsOfDate,
    queryLoading,
    queryResult,
    activeArticleIds,
    errorMessage,
    onRunQuery: handleRunQuery,
    onCitationClick: handleCitationClick,
    onNewQuery: handleNewQuery,
  }
}
