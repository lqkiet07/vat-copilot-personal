import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  BookOpen,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  FileText,
  FolderKanban,
  Landmark,
  Layers,
  Loader2,
  RotateCcw,
  Scale,
  Search,
  X,
} from "lucide-react";
import { parseCitationId, type ApiDocumentSummary } from "../api";
import type { Citation, LawDoc, ResolvedProvisionMeta } from "../data";
import {
  collectDocCitationMatches,
  doesArticleMatchCitation,
  doesCitationMatchDoc,
  doesClauseMatchCitation,
  doesPointMatchCitation,
  fmtDate,
  getDocCategory,
  highlightOperativeSpans,
  parseCitationTarget,
  stripLeadingClauseNumber,
  type DocCategory,
} from "../utils/citationMatch";

type Props = {
  open: boolean;
  isDesktop: boolean;
  libraryDocs: LawDoc[];
  liveSummaries: ApiDocumentSummary[] | null;
  liveDocDetails: Record<string, LawDoc>;
  loadingDocIds: Set<string>;
  docErrors: Record<string, string>;
  libraryCategory: DocCategory;
  activeLibraryDoc: string;
  librarySearch: string;
  highlightedCitations: Citation[];
  focusedCitation: Citation | null;
  resolvedProvisions?: Record<string, ResolvedProvisionMeta>;
  highlightContext?: string;
  openedFromCitation?: boolean;
  onCategoryChange: (cat: DocCategory) => void;
  onSelectDoc: (docId: string) => void;
  onSearchChange: (q: string) => void;
  onFocusCitation: (c: Citation) => void;
  onJumpToProvision?: (unitId: string) => void;
  onRetryDoc: (docId: string) => void;
  onClose: () => void;
};

const CATEGORY_TABS: Array<{
  key: DocCategory;
  label: string;
  Icon: typeof Layers;
}> = [
    { key: "all", label: "Tất cả", Icon: Layers },
    { key: "luat", label: "Luật", Icon: Scale },
    { key: "nghidinh", label: "Nghị định", Icon: Landmark },
    { key: "thongtu", label: "Thông tư", Icon: FileText },
    { key: "other", label: "Khác", Icon: FolderKanban },
  ];

/** Right drawer rendering legal documents with Citation Focus Mode, operative amendments, and key-phrase highlighting. */
export function LibraryDrawer({
  open,
  isDesktop,
  libraryDocs,
  liveSummaries,
  liveDocDetails,
  loadingDocIds,
  docErrors,
  libraryCategory,
  activeLibraryDoc,
  librarySearch,
  highlightedCitations,
  focusedCitation,
  resolvedProvisions,
  highlightContext = "",
  openedFromCitation = false,
  onCategoryChange,
  onSelectDoc,
  onSearchChange,
  onFocusCitation,
  onJumpToProvision,
  onRetryDoc,
  onClose,
}: Props) {
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const browseAllMode = !(openedFromCitation && highlightedCitations.length > 0);
  const [showOriginalMap, setShowOriginalMap] = useState<Record<string, boolean>>({});

  const categoryCounts = useMemo(() => {
    const counts: Record<DocCategory, number> = {
      all: libraryDocs.length,
      luat: 0,
      nghidinh: 0,
      thongtu: 0,
      other: 0,
    };
    for (const d of libraryDocs) {
      counts[getDocCategory(d.id)] += 1;
    }
    return counts;
  }, [libraryDocs]);

  const categorizedDocs = useMemo(() => {
    const term = librarySearch.trim().toLowerCase();
    return libraryDocs.filter((d) => {
      if (libraryCategory !== "all" && getDocCategory(d.id) !== libraryCategory) {
        return false;
      }
      if (!term) return true;
      return (
        d.tab.toLowerCase().includes(term) ||
        d.full.toLowerCase().includes(term) ||
        d.chapters.some((c) =>
          c.articles.some(
            (a) =>
              a.title.toLowerCase().includes(term) ||
              `điều ${a.no}`.includes(term) ||
              a.clauses.some(
                (cl) =>
                  cl.text.toLowerCase().includes(term) ||
                  cl.points?.some((pt) => pt.text.toLowerCase().includes(term))
              )
          )
        )
      );
    });
  }, [libraryDocs, libraryCategory, librarySearch]);

  const activeDocData = useMemo(() => {
    return libraryDocs.find((d) => d.id === activeLibraryDoc) || null;
  }, [libraryDocs, activeLibraryDoc]);

  const citedDocIds = useMemo(() => {
    const ids = new Set<string>();
    for (const d of libraryDocs) {
      if (highlightedCitations.some((c) => doesCitationMatchDoc(c, d))) {
        ids.add(d.id);
      }
    }
    return ids;
  }, [libraryDocs, highlightedCitations]);

  const docCitations = useMemo(() => {
    if (!activeDocData) return [];
    return highlightedCitations.filter((c) => doesCitationMatchDoc(c, activeDocData));
  }, [activeDocData, highlightedCitations]);

  const docMatches = useMemo(() => {
    if (!activeDocData || docCitations.length === 0) return [];
    return collectDocCitationMatches(activeDocData, docCitations);
  }, [activeDocData, docCitations]);

  const focusedMatchIndex = useMemo(() => {
    if (docMatches.length === 0) return -1;
    if (!focusedCitation) return 0;
    const exactIdx = docMatches.findIndex(
      (m) => m.citation.id === focusedCitation.id && m.citation.label === focusedCitation.label
    );
    return exactIdx >= 0 ? exactIdx : 0;
  }, [docMatches, focusedCitation]);

  // Scroll the focused Clause/Point/Article into view once full text has rendered.
  useEffect(() => {
    if (!open || !activeDocData || activeDocData.chapters.length === 0) return;
    if (!focusedCitation && docMatches.length === 0) return;

    const rafId = window.requestAnimationFrame(() => {
      const container = scrollContainerRef.current;
      if (!container) return;
      const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      const target =
        container.querySelector('[data-focused-citation="true"]') ||
        container.querySelector('[data-cited="true"]');
      if (target instanceof HTMLElement) {
        target.scrollIntoView({
          behavior: reducedMotion ? "auto" : "smooth",
          block: "center",
        });
      }
    });

    return () => window.cancelAnimationFrame(rafId);
  }, [open, activeDocData, focusedCitation, docMatches.length]);

  if (!open) return null;

  const isDocLoading = Boolean(
    activeDocData &&
    loadingDocIds.has(activeDocData.id) &&
    activeDocData.chapters.length === 0
  );
  const docError = activeDocData ? docErrors[activeDocData.id] : undefined;
  const focusedTarget = focusedCitation ? parseCitationTarget(focusedCitation) : null;

  return (
    <>
      {!isDesktop && (
        <div
          aria-hidden="true"
          onClick={onClose}
          className="fixed inset-0 z-40 bg-slate-900/40 backdrop-blur-[1px]"
        />
      )}

      <aside
        aria-label="Thư viện văn bản pháp luật GTGT"
        className={
          isDesktop
            ? "relative z-30 flex h-full w-[460px] xl:w-[500px] shrink-0 flex-col border-l border-[#CBD5E1] bg-[#F8FAFC] shadow-2xl"
            : "fixed inset-y-0 right-0 z-50 flex h-full w-full max-w-[480px] flex-col border-l border-[#CBD5E1] bg-[#F8FAFC] shadow-2xl"
        }
      >
        {/* Header */}
        <div className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-[#CBD5E1] bg-white px-4">
          <div className="flex min-w-0 items-center gap-2">
            <BookOpen className="h-4 w-4 shrink-0 text-[#1E3A8A]" />
            <span className="truncate text-[14px] font-bold text-[#1E3A8A]">
              {activeDocData && !browseAllMode ? activeDocData.tab : "Kho Văn Bản Pháp Luật GTGT"}
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[#64748B] transition-colors hover:bg-[#F1F5F9] hover:text-[#0F172A] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
            title="Đóng thư viện"
            aria-label="Đóng thư viện"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {browseAllMode && (
          <>
            {/* Category Tabs */}
            <div
              role="tablist"
              aria-label="Phân loại văn bản pháp luật"
              className="flex shrink-0 gap-1 overflow-x-auto border-b border-[#CBD5E1] bg-white px-3 pt-2 text-[12px]"
            >
              {CATEGORY_TABS.map(({ key, label, Icon }) => {
                const count = categoryCounts[key];
                if (key === "other" && count === 0) return null;
                const active = libraryCategory === key;
                return (
                  <button
                    type="button"
                    role="tab"
                    aria-selected={active}
                    key={key}
                    onClick={() => onCategoryChange(key)}
                    className={`flex shrink-0 items-center gap-1.5 rounded-t-lg px-2.5 py-2 font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40 ${active
                        ? "border-b-2 border-[#1E3A8A] bg-[#EFF6FF] text-[#1E3A8A]"
                        : "text-[#64748B] hover:bg-[#F8FAFC] hover:text-[#0F172A]"
                      }`}
                  >
                    <Icon className="h-3.5 w-3.5 shrink-0" />
                    <span>
                      {label} ({count})
                    </span>
                  </button>
                );
              })}
            </div>

            {/* Search */}
            <div className="shrink-0 border-b border-[#CBD5E1] bg-[#F1F5F9] p-3">
              <div className="relative flex items-center">
                <Search className="pointer-events-none absolute left-3 h-3.5 w-3.5 text-[#94A3B8]" />
                <input
                  type="text"
                  value={librarySearch}
                  onChange={(e) => onSearchChange(e.target.value)}
                  aria-label="Tìm kiếm trong kho văn bản"
                  placeholder="Tìm kiếm điều, khoản, tên hoặc mã văn bản..."
                  className="w-full rounded-xl border border-[#CBD5E1] bg-white py-1.5 pl-8 pr-8 text-[12.5px] focus-visible:border-[#1E3A8A] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/25"
                />
                {librarySearch && (
                  <button
                    type="button"
                    onClick={() => onSearchChange("")}
                    className="absolute right-1.5 flex h-6 w-6 items-center justify-center rounded-full text-[#64748B] hover:bg-[#E2E8F0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
                    title="Xóa từ khóa tìm kiếm"
                    aria-label="Xóa từ khóa tìm kiếm"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
              {liveSummaries && liveSummaries.some((s) => !liveDocDetails[s.id]) && librarySearch.trim() && (
                <p className="mt-1.5 text-[11px] text-[#64748B]">
                  Tìm kiếm quét tên/mã mọi văn bản và toàn văn các văn bản đã tải.
                </p>
              )}
            </div>

            {/* Document Pills Strip */}
            <div className="scroll-quiet flex shrink-0 gap-1.5 overflow-x-auto border-b border-[#CBD5E1] bg-white p-2 text-[11.5px]">
              {categorizedDocs.length === 0 ? (
                <span className="px-2 py-1 text-[11.5px] italic text-[#64748B]">
                  Không có văn bản khớp bộ lọc
                </span>
              ) : (
                categorizedDocs.map((d) => {
                  const isSelected = activeLibraryDoc === d.id;
                  const isCitedDoc = citedDocIds.has(d.id);
                  return (
                    <button
                      type="button"
                      key={d.id}
                      onClick={() => onSelectDoc(d.id)}
                      title={isCitedDoc ? "Văn bản có điều khoản đang được trích dẫn" : d.full}
                      className={`flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40 ${isSelected
                          ? "bg-[#1E3A8A] text-white shadow-xs"
                          : isCitedDoc
                            ? "border border-amber-300 bg-amber-50 text-amber-950 hover:bg-amber-100"
                            : "bg-[#F1F5F9] text-[#475569] hover:bg-[#E2E8F0]"
                        }`}
                    >
                      {isCitedDoc && (
                        <span
                          className={`h-1.5 w-1.5 rounded-full ${isSelected ? "bg-amber-300" : "bg-amber-500"}`}
                          aria-hidden="true"
                        />
                      )}
                      <span>{d.tab}</span>
                    </button>
                  );
                })
              )}
            </div>
          </>
        )}

        {/* Sticky Citation Navigator Bar when viewing a cited document */}
        {docMatches.length > 0 && (
          <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-amber-200 bg-[#FFFBEB] px-3.5 py-2 text-[12px] text-amber-950">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5 font-bold text-amber-900">
                <span className="inline-block h-2 w-2 shrink-0 rounded-full bg-amber-500" aria-hidden="true" />
                <span className="truncate">
                  Đang tô sáng {docMatches.length} căn cứ trích dẫn ({focusedMatchIndex + 1}/{docMatches.length})
                </span>
              </div>
              <div className="truncate text-[11px] text-amber-800">
                {docMatches[focusedMatchIndex]?.label}
              </div>
            </div>

            <div className="flex items-center gap-1">
              {docMatches.length > 1 && (
                <>
                  <button
                    type="button"
                    onClick={() => {
                      const prevIdx = (focusedMatchIndex - 1 + docMatches.length) % docMatches.length;
                      onFocusCitation(docMatches[prevIdx].citation);
                    }}
                    className="flex h-8 w-8 items-center justify-center rounded-lg border border-amber-300 bg-white text-amber-900 hover:bg-amber-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/40"
                    title="Trích dẫn trước"
                    aria-label="Trích dẫn trước"
                  >
                    <ChevronLeft className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      const nextIdx = (focusedMatchIndex + 1) % docMatches.length;
                      onFocusCitation(docMatches[nextIdx].citation);
                    }}
                    className="flex h-8 w-8 items-center justify-center rounded-lg border border-amber-300 bg-white text-amber-900 hover:bg-amber-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/40"
                    title="Trích dẫn tiếp theo"
                    aria-label="Trích dẫn tiếp theo"
                  >
                    <ChevronRight className="h-4 w-4" />
                  </button>
                </>
              )}
            </div>
          </div>
        )}

        {/* Document Body */}
        <div ref={scrollContainerRef} className="scroll-quiet flex-1 space-y-4 overflow-y-auto p-4">
          {categorizedDocs.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-[#CBD5E1] bg-white p-6 text-center">
              <Search className="mx-auto mb-2 h-8 w-8 text-[#94A3B8]" />
              <p className="text-[13.5px] font-semibold text-[#0F172A]">
                Không tìm thấy văn bản hoặc điều khoản phù hợp
              </p>
              <p className="mt-1 text-[12px] text-[#64748B]">
                Thử tìm theo số hiệu văn bản (ví dụ: 48/2024, 181/2025) hoặc đặt lại bộ lọc danh mục.
              </p>
              <button
                type="button"
                onClick={() => {
                  onCategoryChange("all");
                  onSearchChange("");
                }}
                className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-[#1E3A8A] px-4 py-1.5 text-[12px] font-semibold text-white hover:bg-[#1E40AF] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                <span>Đặt lại bộ lọc</span>
              </button>
            </div>
          ) : !activeDocData ? (
            <div className="rounded-2xl border border-[#CBD5E1] bg-white p-6 text-center text-[#475569]">
              <p className="text-[13px] font-semibold text-[#1E3A8A]">
                Hiện chưa có nội dung toàn văn cho văn bản này trong chế độ ngoại tuyến.
              </p>
              <p className="mt-2 text-[12px] text-[#64748B]">
                Vui lòng kiểm tra kết nối máy chủ để tải đầy đủ điều khoản của văn bản.
              </p>
            </div>
          ) : (
            <>
              <div className="rounded-2xl border border-[#BFDBFE] bg-[#EFF6FF] p-3 text-[12px]">
                <div className="font-bold text-[#1E3A8A]">{activeDocData.full}</div>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <span className="font-mono-num font-semibold text-[#475569]">
                    Hiệu lực: {activeDocData.effective || "Chưa cập nhật"}
                  </span>
                  {activeDocData.badge.text && (
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${activeDocData.badge.tone === "green"
                          ? "bg-green-100 text-green-800"
                          : "bg-amber-100 text-amber-900"
                        }`}
                    >
                      {activeDocData.badge.text}
                    </span>
                  )}
                </div>
              </div>

              {docError && activeDocData.chapters.length === 0 ? (
                <div className="rounded-2xl border border-red-200 bg-red-50 p-5 text-center text-[12.5px] text-red-900">
                  <AlertTriangle className="mx-auto mb-2 h-6 w-6 text-red-600" />
                  <p className="font-semibold">Không thể tải toàn văn văn bản ({docError})</p>
                  <button
                    type="button"
                    onClick={() => onRetryDoc(activeDocData.id)}
                    className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-red-700 px-3.5 py-1.5 text-[12px] font-semibold text-white hover:bg-red-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/40"
                  >
                    <RotateCcw className="h-3.5 w-3.5" />
                    <span>Thử tải lại</span>
                  </button>
                </div>
              ) : isDocLoading ||
                (activeDocData.chapters.length === 0 &&
                  liveSummaries?.some((s) => s.id === activeDocData.id) &&
                  !liveDocDetails[activeDocData.id]) ? (
                <div className="flex items-center justify-center gap-2 rounded-2xl border border-[#CBD5E1] bg-white p-6 text-[12.5px] text-[#475569]">
                  <Loader2 className="h-4 w-4 animate-spin text-[#1E3A8A]" />
                  <span>Đang tải toàn văn điều khoản từ máy chủ...</span>
                </div>
              ) : null}

              {activeDocData.chapters.map((ch, chIdx) => (
                <div key={`${activeDocData.id}-ch-${chIdx}`} className="space-y-3">
                  <div className="border-b border-[#CBD5E1]/60 pb-1 text-[11.5px] font-bold uppercase tracking-wide text-[#64748B]">
                    {ch.title}
                  </div>
                  {ch.articles.map((art, artIdx) => {
                    const articleCitations = docCitations.filter((c) => doesArticleMatchCitation(art, c));
                    const hasArticleDescendantHit = articleCitations.length > 0;
                    const isArticleLevelHit = articleCitations.some(
                      (c) => parseCitationTarget(c).level === "article"
                    );
                    const isFocusedArticle = Boolean(
                      focusedCitation &&
                      focusedTarget?.level === "article" &&
                      doesArticleMatchCitation(art, focusedCitation)
                    );

                    return (
                      <div
                        key={art.id || `${activeDocData.id}-art-${artIdx}`}
                        id={`lib-art-${art.id}`}
                        data-cited={isArticleLevelHit ? "true" : undefined}
                        data-focused-citation={isFocusedArticle ? "true" : undefined}
                        className={`rounded-2xl border p-4 transition-colors ${isFocusedArticle
                            ? "cite-glow border-amber-500 bg-[#FFFBEB]"
                            : hasArticleDescendantHit
                              ? "border-amber-300 bg-white shadow-xs"
                              : "border-[#CBD5E1] bg-white"
                          }`}
                      >
                        <div className="flex flex-wrap items-start justify-between gap-2">
                          <h3 className="font-serif-legal text-[15px] font-semibold leading-snug text-[#1E3A8A]">
                            Điều {art.no}: {art.title}
                          </h3>
                          {hasArticleDescendantHit && (
                            <span
                              className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold ${isArticleLevelHit
                                  ? "bg-amber-200 text-amber-950"
                                  : "bg-amber-100 text-amber-900"
                                }`}
                            >
                              {isArticleLevelHit ? "Điều được trích dẫn" : "Có căn cứ trích dẫn"}
                            </span>
                          )}
                        </div>

                        <div className="font-serif-legal mt-2.5 space-y-2 text-[14px] leading-relaxed text-[#334155]">
                          {art.clauses.map((c, cIdx) => {
                            const clauseHits = articleCitations.filter((cit) =>
                              doesClauseMatchCitation(c, art, cit)
                            );
                            const isClauseLevelHit = clauseHits.some(
                              (cit) => parseCitationTarget(cit).level === "clause"
                            );
                            const isFocusedClause = Boolean(
                              focusedCitation &&
                              focusedTarget?.level === "clause" &&
                              doesClauseMatchCitation(c, art, focusedCitation)
                            );
                            const clauseKey = c.id || `${art.id}-c-${cIdx}-${c.n}`;
                            const clauseDisplay = c.label || String(c.n);
                            const resolvedClauseMeta =
                              (c.id ? resolvedProvisions?.[c.id] : undefined) ||
                              clauseHits.map((h) => resolvedProvisions?.[h.id]).find(Boolean);
                            const isClauseAmended = Boolean(
                              resolvedClauseMeta &&
                              resolvedClauseMeta.status === "AMENDED" &&
                              resolvedClauseMeta.text
                            );
                            const showOriginalClause = Boolean(showOriginalMap[clauseKey]);
                            const rawClauseBody =
                              isClauseAmended && !showOriginalClause
                                ? stripLeadingClauseNumber(resolvedClauseMeta!.text, clauseDisplay)
                                : c.text;
                            const clauseSegments =
                              isClauseLevelHit && highlightContext
                                ? highlightOperativeSpans(rawClauseBody, highlightContext)
                                : [{ text: rawClauseBody, highlight: false }];

                            return (
                              <div
                                key={clauseKey}
                                data-cited={isClauseLevelHit ? "true" : undefined}
                                data-focused-citation={isFocusedClause ? "true" : undefined}
                                className={
                                  isClauseLevelHit
                                    ? `rounded-xl border-l-4 border-amber-500 bg-amber-50/95 px-3 py-2 text-[#0F172A] ring-1 ring-amber-200 ${isFocusedClause ? "cite-glow" : ""
                                    }`
                                    : clauseHits.length > 0
                                      ? "rounded-xl border-l-2 border-amber-300 pl-2.5"
                                      : undefined
                                }
                              >
                                {(isClauseLevelHit || isClauseAmended) && (
                                  <div className="font-sans-ui mb-1.5 flex flex-wrap items-center gap-1.5">
                                    {isClauseLevelHit && (
                                      <span className="inline-flex items-center gap-1 rounded-full bg-amber-200/90 px-2 py-0.5 text-[10px] font-bold text-amber-950">
                                        Khoản được trích dẫn
                                      </span>
                                    )}
                                    {isClauseAmended && (
                                      <span className="inline-flex items-center rounded-full border border-indigo-200 bg-indigo-50 px-2 py-0.5 text-[10px] font-bold text-indigo-900">
                                        {showOriginalClause
                                          ? "Đang xem nguyên văn trước sửa đổi"
                                          : "Nội dung đang có hiệu lực (đã sửa đổi)"}
                                      </span>
                                    )}
                                  </div>
                                )}
                                <div>
                                  <span className="font-semibold text-[#0F172A]">{clauseDisplay}. </span>
                                  <span>
                                    {clauseSegments.map((seg, segIdx) =>
                                      seg.highlight ? (
                                        <mark
                                          key={segIdx}
                                          className="rounded bg-amber-200/90 px-0.5 font-semibold text-[#0F172A]"
                                        >
                                          {seg.text}
                                        </mark>
                                      ) : (
                                        <span key={segIdx}>{seg.text}</span>
                                      )
                                    )}
                                  </span>
                                </div>

                                {isClauseAmended && resolvedClauseMeta && (
                                  <div className="font-sans-ui mt-2 space-y-1 rounded-lg border border-indigo-200/80 bg-indigo-50/60 px-2.5 py-2 text-[11.5px] text-indigo-950">
                                    {resolvedClauseMeta.amended_by.map((link, linkIdx) => {
                                      const parsedLink = parseCitationId(link.source_unit);
                                      const effDate = link.effective_on ? fmtDate(link.effective_on) : "";
                                      return (
                                        <button
                                          type="button"
                                          key={`${link.source_unit}-${linkIdx}`}
                                          onClick={() => onJumpToProvision?.(link.source_unit)}
                                          className="flex w-full items-center justify-between gap-1.5 text-left font-semibold text-indigo-800 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40"
                                        >
                                          <span>
                                            Đã sửa đổi bởi {parsedLink.label}
                                            {effDate ? ` (từ ${effDate})` : ""}
                                          </span>
                                          <ExternalLink className="h-3.5 w-3.5 shrink-0" />
                                        </button>
                                      );
                                    })}
                                    <button
                                      type="button"
                                      onClick={() =>
                                        setShowOriginalMap((prev) => ({
                                          ...prev,
                                          [clauseKey]: !prev[clauseKey],
                                        }))
                                      }
                                      className="text-[11px] font-medium text-[#475569] underline-offset-2 hover:text-[#1E3A8A] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
                                    >
                                      {showOriginalClause
                                        ? "Xem nội dung đang có hiệu lực"
                                        : "Xem nguyên văn trước sửa đổi"}
                                    </button>
                                  </div>
                                )}

                                {c.points && c.points.length > 0 && (
                                  <div className="mt-1.5 space-y-1.5 pl-4 text-[13.5px] text-[#475569]">
                                    {c.points.map((pt, pIdx) => {
                                      const pointHits = articleCitations.filter((cit) =>
                                        doesPointMatchCitation(pt, c, art, cit)
                                      );
                                      const isPointHit = pointHits.length > 0;
                                      const isFocusedPoint = Boolean(
                                        focusedCitation &&
                                        focusedTarget?.level === "point" &&
                                        doesPointMatchCitation(pt, c, art, focusedCitation)
                                      );
                                      const pointKey = pt.id || `${clauseKey}-p-${pIdx}-${pt.l}`;
                                      const resolvedPointMeta =
                                        (pt.id ? resolvedProvisions?.[pt.id] : undefined) ||
                                        pointHits.map((h) => resolvedProvisions?.[h.id]).find(Boolean);
                                      const isPointAmended = Boolean(
                                        resolvedPointMeta &&
                                        resolvedPointMeta.status === "AMENDED" &&
                                        resolvedPointMeta.text
                                      );
                                      const showOriginalPoint = Boolean(showOriginalMap[pointKey]);
                                      const rawPointBody =
                                        isPointAmended && !showOriginalPoint
                                          ? stripLeadingClauseNumber(resolvedPointMeta!.text, pt.l)
                                          : pt.text;
                                      const pointSegments =
                                        isPointHit && highlightContext
                                          ? highlightOperativeSpans(rawPointBody, highlightContext)
                                          : [{ text: rawPointBody, highlight: false }];

                                      return (
                                        <div
                                          key={pointKey}
                                          data-cited={isPointHit ? "true" : undefined}
                                          data-focused-citation={isFocusedPoint ? "true" : undefined}
                                          className={
                                            isPointHit
                                              ? `rounded-lg border-l-4 border-amber-500 bg-amber-100/85 px-2.5 py-1.5 text-[#0F172A] ring-1 ring-amber-300 ${isFocusedPoint ? "cite-glow" : ""
                                              }`
                                              : undefined
                                          }
                                        >
                                          {isPointHit && (
                                            <span className="font-sans-ui mr-1.5 inline-flex items-center rounded-full bg-amber-300/90 px-2 py-0.5 text-[10px] font-bold text-amber-950">
                                              Điểm được trích dẫn
                                            </span>
                                          )}
                                          {isPointAmended && (
                                            <span className="font-sans-ui mr-1.5 inline-flex items-center rounded-full border border-indigo-200 bg-indigo-50 px-2 py-0.5 text-[10px] font-bold text-indigo-900">
                                              {showOriginalPoint ? "Nguyên văn cũ" : "Đã sửa đổi"}
                                            </span>
                                          )}
                                          <span className="font-semibold text-[#1E3A8A]">{pt.l}) </span>
                                          <span>
                                            {pointSegments.map((seg, segIdx) =>
                                              seg.highlight ? (
                                                <mark
                                                  key={segIdx}
                                                  className="rounded bg-amber-200/90 px-0.5 font-semibold text-[#0F172A]"
                                                >
                                                  {seg.text}
                                                </mark>
                                              ) : (
                                                <span key={segIdx}>{seg.text}</span>
                                              )
                                            )}
                                          </span>
                                          {isPointAmended && resolvedPointMeta && (
                                            <div className="font-sans-ui mt-1.5 space-y-1 rounded-lg border border-indigo-200/80 bg-indigo-50/60 px-2 py-1.5 text-[11px] text-indigo-950">
                                              {resolvedPointMeta.amended_by.map((link, linkIdx) => {
                                                const parsedLink = parseCitationId(link.source_unit);
                                                const effDate = link.effective_on ? fmtDate(link.effective_on) : "";
                                                return (
                                                  <button
                                                    type="button"
                                                    key={`${link.source_unit}-${linkIdx}`}
                                                    onClick={() => onJumpToProvision?.(link.source_unit)}
                                                    className="flex w-full items-center justify-between gap-1 text-left font-semibold text-indigo-800 hover:underline"
                                                  >
                                                    <span>
                                                      Đã sửa đổi bởi {parsedLink.label}
                                                      {effDate ? ` (từ ${effDate})` : ""}
                                                    </span>
                                                    <ExternalLink className="h-3 w-3 shrink-0" />
                                                  </button>
                                                );
                                              })}
                                              <button
                                                type="button"
                                                onClick={() =>
                                                  setShowOriginalMap((prev) => ({
                                                    ...prev,
                                                    [pointKey]: !prev[pointKey],
                                                  }))
                                                }
                                                className="text-[10.5px] font-medium text-[#475569] underline-offset-2 hover:text-[#1E3A8A] hover:underline"
                                              >
                                                {showOriginalPoint
                                                  ? "Xem nội dung đang có hiệu lực"
                                                  : "Xem nguyên văn trước sửa đổi"}
                                              </button>
                                            </div>
                                          )}
                                        </div>
                                      );
                                    })}
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    );
                  })}
                </div>
              ))}
            </>
          )}
        </div>
      </aside>
    </>
  );
}

