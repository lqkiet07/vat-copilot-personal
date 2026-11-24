import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, BookOpen, Copy, ExternalLink, GitBranch } from "lucide-react";
import drum from "@/imports/img_6-hinh-nen-powerpoint-trong-dong.jpg";
import { type Citation, DOCS, type LawDoc } from "./data";
import { apiDocToLawDoc, citationBodyMessage, DOCUMENT_LABELS, findCitationBody, parseCitationId } from "./api";
import { DATA_FREEZE_DATE, STAGING_DATA_FREEZE_DATE } from "./config";
import { AnswerAuditSection, AnswerConclusionBox, AnswerHeader } from "./components/AnswerSections";
import {
  FreezeWarningBanner,
  PortalHeader,
  ReasoningProgressCard,
  UserPromptBubble,
  WelcomeScreen,
} from "./components/ChatLayoutSections";
import { Composer } from "./components/Composer";
import { LibraryDrawer } from "./components/LibraryDrawer";
import { MarkdownContent } from "./components/MarkdownContent";
import { RelatedArticlePreview } from "./components/RelatedArticlePreview";
import { RelatedDrawerFrame } from "./components/RelatedDrawerFrame";
import { type Pipeline, SettingsDialog, type SystemSettings } from "./components/SettingsDialog";
import { Sidebar } from "./components/Sidebar";
import { type Msg, useChatSession } from "./hooks/useChatSession";
import { useDocumentCache } from "./hooks/useDocumentCache";
import {
  type DocCategory,
  fmtDate,
  formatInlineCitationsPlainText,
  getDocCategory,
  parseCitationTarget,
  resolveLibraryDocId,
  store,
} from "./utils/citationMatch";

type RightPanel = "none" | "library" | "related";

const DEFAULT_SETTINGS: SystemSettings = {
  pipeline: "P3",
  defaultDate: DATA_FREEZE_DATE,
  useLlm: true,
};

function getIsDesktopViewport(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return true;
  return window.matchMedia("(min-width: 1024px)").matches;
}

function getIsWideViewport(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return true;
  return window.matchMedia("(min-width: 1280px)").matches;
}

export function App() {
  const [hasPersistedSettings] = useState(() => {
    try {
      return localStorage.getItem("vat_public_settings") !== null;
    } catch {
      return false;
    }
  });
  const [settings, setSettings] = useState<SystemSettings>(() =>
    store.get("vat_public_settings", DEFAULT_SETTINGS)
  );

  const [date, setDate] = useState<string>(settings.defaultDate);
  const [pipeline, setPipeline] = useState<Pipeline>(settings.pipeline);
  const [query, setQuery] = useState("");

  const [isDesktop, setIsDesktop] = useState<boolean>(getIsDesktopViewport);
  const [sidebarOpen, setSidebarOpen] = useState<boolean>(getIsDesktopViewport);
  const [rightPanel, setRightPanel] = useState<RightPanel>("none");
  const [libraryCategory, setLibraryCategory] = useState<DocCategory>("all");
  const [activeLibraryDoc, setActiveLibraryDoc] = useState("l48");
  const [librarySearch, setLibrarySearch] = useState("");
  const [focusedCitation, setFocusedCitation] = useState<Citation | null>(null);
  const [citationContext, setCitationContext] = useState<{
    msgId: number;
    citations: Citation[];
  } | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [pipelineMenuOpen, setPipelineMenuOpen] = useState(false);

  const taRef = useRef<HTMLTextAreaElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const userEditedDateRef = useRef(false);

  const {
    runtimeEnv,
    liveSummaries,
    liveDocDetails,
    loadingDocIds,
    docErrors,
    ensureDocuments,
    retryDocument,
  } = useDocumentCache();

  const isStaging = runtimeEnv === "staging";
  const activeFreezeDate = isStaging ? STAGING_DATA_FREEZE_DATE : DATA_FREEZE_DATE;

  const resetCitationFocus = useCallback(() => {
    setCitationContext(null);
    setFocusedCitation(null);
  }, []);

  const {
    convs,
    activeId,
    messages,
    loading,
    step,
    stageMessages,
    newChat,
    selectConv,
    deleteConv,
    abortQuery,
    send,
  } = useChatSession({
    query,
    date,
    pipeline,
    useLlm: settings.useLlm,
    activeFreezeDate,
    taRef,
    onQueryChange: setQuery,
    onResetCitationFocus: resetCitationFocus,
  });

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(min-width: 1024px)");
    const onChange = (e: MediaQueryListEvent) => {
      setIsDesktop(e.matches);
      if (!e.matches) setSidebarOpen(false);
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  // Lock pipeline P3 in staging without mutating saved settings; default fresh sessions to staging freeze date.
  useEffect(() => {
    if (runtimeEnv !== "staging") return;
    setPipeline("P3");
    if (!hasPersistedSettings && !userEditedDateRef.current && date === DATA_FREEZE_DATE) {
      setDate(STAGING_DATA_FREEZE_DATE);
    }
  }, [runtimeEnv, hasPersistedSettings, date]);

  useEffect(() => {
    if (!liveSummaries || liveSummaries.length === 0) return;
    setActiveLibraryDoc((prev) =>
      liveSummaries.some((s) => s.id === prev)
        ? prev
        : liveSummaries.find((s) => DOCUMENT_LABELS[s.id]?.libraryDocId === prev)?.id ||
          liveSummaries[0].id
    );
  }, [liveSummaries]);

  useEffect(() => {
    if (rightPanel !== "library" || !activeLibraryDoc) return;
    ensureDocuments([activeLibraryDoc]);
  }, [rightPanel, activeLibraryDoc, liveSummaries, ensureDocuments]);

  useEffect(() => {
    store.set("vat_public_settings", settings);
  }, [settings]);

  useEffect(() => {
    const reduced =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    endRef.current?.scrollIntoView({ behavior: reduced ? "auto" : "smooth" });
  }, [messages]);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== "Escape" || settingsOpen) return;
      if (rightPanel !== "none") setRightPanel("none");
      else if (!isDesktop && sidebarOpen) setSidebarOpen(false);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [rightPanel, isDesktop, sidebarOpen, settingsOpen]);

  const latestResult = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].result) return messages[i].result;
    }
    return null;
  }, [messages]);

  const latestCitations: Citation[] = useMemo(() => latestResult?.citations || [], [latestResult]);

  const activeCitations: Citation[] = useMemo(() => {
    if (citationContext && messages.some((m) => m.id === citationContext.msgId)) {
      return citationContext.citations;
    }
    return latestCitations;
  }, [citationContext, messages, latestCitations]);

  useEffect(() => {
    if (rightPanel !== "related" || activeCitations.length === 0) return;
    const docIds = activeCitations
      .map((c) => parseCitationTarget(c).docId)
      .filter((id): id is string => Boolean(id));
    ensureDocuments(docIds);
  }, [rightPanel, activeCitations, liveSummaries, ensureDocuments]);

  const libraryDocs = useMemo<LawDoc[]>(() => {
    if (!liveSummaries || liveSummaries.length === 0) return [...DOCS];
    return liveSummaries.map((s) => liveDocDetails[s.id] || apiDocToLawDoc({ ...s, chapters: [], articles: [] }));
  }, [liveSummaries, liveDocDetails]);

  const citationSearchDocs = useMemo<LawDoc[]>(
    () => [...Object.values(liveDocDetails), ...DOCS],
    [liveDocDetails]
  );

  const openLibraryPanel = useCallback(
    (docId?: string, targetCitation?: Citation | null) => {
      if (docId) setActiveLibraryDoc(docId);
      if (targetCitation !== undefined) setFocusedCitation(targetCitation);
      if (docId) ensureDocuments([docId]);
      if (isDesktop && !getIsWideViewport()) setSidebarOpen(false);
      setRightPanel("library");
    },
    [ensureDocuments, isDesktop]
  );

  const openRelatedPanel = useCallback(
    (msgId?: number, citations?: Citation[], focus?: Citation | null) => {
      if (msgId !== undefined && citations) setCitationContext({ msgId, citations });
      if (focus !== undefined) setFocusedCitation(focus);
      if (isDesktop && !getIsWideViewport()) setSidebarOpen(false);
      setRightPanel("related");
    },
    [isDesktop]
  );

  const handleCitationBadgeClick = useCallback(
    (msgId: number, msgCitations: Citation[], clicked: Citation) => {
      setCitationContext({ msgId, citations: msgCitations });
      setFocusedCitation(clicked);
      const targetDocId = resolveLibraryDocId(clicked, libraryDocs);
      if (targetDocId && libraryDocs.some((d) => d.id === targetDocId)) {
        setLibraryCategory("all");
        setLibrarySearch("");
        openLibraryPanel(targetDocId, clicked);
      } else {
        openRelatedPanel(msgId, msgCitations, clicked);
      }
    },
    [libraryDocs, openLibraryPanel, openRelatedPanel]
  );

  const handleCategoryChange = useCallback(
    (cat: DocCategory) => {
      setLibraryCategory(cat);
      if (cat === "all") return;
      const currentInCat = libraryDocs.some(
        (d) => d.id === activeLibraryDoc && getDocCategory(d.id) === cat
      );
      if (!currentInCat) {
        const firstMatch = libraryDocs.find((d) => getDocCategory(d.id) === cat);
        if (firstMatch) setActiveLibraryDoc(firstMatch.id);
      }
    },
    [libraryDocs, activeLibraryDoc]
  );

  return (
    <div className="relative flex h-screen w-full overflow-hidden bg-[#FFFFFF] font-sans text-[#1F1F1F] antialiased">
      <Sidebar
        isDesktop={isDesktop}
        sidebarOpen={sidebarOpen}
        convs={convs}
        activeId={activeId}
        libraryCount={libraryDocs.length}
        libraryOpen={rightPanel === "library"}
        onToggleSidebar={() => setSidebarOpen((v) => !v)}
        onCloseMobile={() => setSidebarOpen(false)}
        onNewChat={newChat}
        onSelectConv={selectConv}
        onDeleteConv={deleteConv}
        onOpenLibrary={() => openLibraryPanel()}
        onOpenSettings={() => setSettingsOpen(true)}
      />

      <main className="relative z-10 flex min-w-0 flex-1 flex-col overflow-hidden bg-[#FFFFFF]">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute left-1/2 top-1/2 z-0 h-[560px] w-[560px] -translate-x-1/2 -translate-y-1/2 select-none rounded-full bg-contain bg-center bg-no-repeat opacity-[0.065]"
          style={{
            backgroundImage: `url(${drum})`,
            maskImage: "radial-gradient(circle, rgba(0,0,0,1) 48%, rgba(0,0,0,0) 76%)",
            WebkitMaskImage: "radial-gradient(circle, rgba(0,0,0,1) 48%, rgba(0,0,0,0) 76%)",
          }}
        />

        <PortalHeader
          showOpenSidebarBtn={!sidebarOpen || !isDesktop}
          isStaging={isStaging}
          docCount={libraryDocs.length}
          freezeDate={activeFreezeDate}
          relatedOpen={rightPanel === "related"}
          citationsCount={activeCitations.length}
          onOpenSidebar={() => setSidebarOpen(true)}
          onToggleRelated={() =>
            rightPanel === "related" ? setRightPanel("none") : openRelatedPanel()
          }
        />

        {date > activeFreezeDate && <FreezeWarningBanner freezeDate={activeFreezeDate} />}

        <div aria-live="polite" className="scroll-quiet relative z-10 min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex min-h-full w-full max-w-[820px] flex-col justify-center px-3 py-5 sm:px-4 sm:py-6">
            {messages.length === 0 ? (
              <WelcomeScreen
                taRef={taRef}
                query={query}
                date={date}
                pipeline={pipeline}
                isStaging={isStaging}
                loading={loading}
                pipelineMenuOpen={pipelineMenuOpen}
                onPipelineMenuChange={setPipelineMenuOpen}
                onQueryChange={setQuery}
                onDateChange={(nextDate) => {
                  userEditedDateRef.current = true;
                  setDate(nextDate);
                }}
                onPipelineChange={setPipeline}
                onSend={() => send()}
                onAbort={abortQuery}
                onPickScenario={(q, d) => {
                  userEditedDateRef.current = true;
                  setQuery(q);
                  setDate(d);
                  send(q, d);
                }}
              />
            ) : (
              <div className="space-y-6 pb-20">
                {messages.map((m) => (
                  <div key={m.id} className="space-y-4">
                    <UserPromptBubble q={m.q} date={m.date} pipeline={m.pipeline} />

                    {m.result ? (
                      <Answer
                        msg={m}
                        onClickCitation={(cit) =>
                          handleCitationBadgeClick(m.id, m.result!.citations, cit)
                        }
                        onOpenRelated={() => openRelatedPanel(m.id, m.result!.citations)}
                        onRetry={() => send(m.q, m.date)}
                        onEditQuery={() => {
                          setQuery(m.q);
                          taRef.current?.focus();
                        }}
                        onSelectMissingFact={(fact) => {
                          setQuery((prev) => {
                            const prefix = prev.trim() ? `${prev.trim()} ` : "";
                            return `${prefix}Bổ sung thông tin (${fact}): `;
                          });
                          taRef.current?.focus();
                        }}
                      />
                    ) : (
                      <ReasoningProgressCard
                        step={step}
                        stageMessages={stageMessages}
                        onAbort={abortQuery}
                      />
                    )}
                  </div>
                ))}
                <div ref={endRef} />
              </div>
            )}
          </div>
        </div>

        {messages.length > 0 && (
          <Composer
            variant="active"
            taRef={taRef}
            query={query}
            date={date}
            pipeline={pipeline}
            isStaging={isStaging}
            loading={loading}
            pipelineMenuOpen={pipelineMenuOpen}
            onPipelineMenuChange={setPipelineMenuOpen}
            onQueryChange={setQuery}
            onDateChange={(nextDate) => {
              userEditedDateRef.current = true;
              setDate(nextDate);
            }}
            onPipelineChange={setPipeline}
            onSend={() => send()}
            onAbort={abortQuery}
          />
        )}
      </main>

      <LibraryDrawer
        open={rightPanel === "library"}
        isDesktop={isDesktop}
        libraryDocs={libraryDocs}
        liveSummaries={liveSummaries}
        liveDocDetails={liveDocDetails}
        loadingDocIds={loadingDocIds}
        docErrors={docErrors}
        libraryCategory={libraryCategory}
        activeLibraryDoc={activeLibraryDoc}
        librarySearch={librarySearch}
        highlightedCitations={activeCitations}
        focusedCitation={focusedCitation}
        onCategoryChange={handleCategoryChange}
        onSelectDoc={setActiveLibraryDoc}
        onSearchChange={setLibrarySearch}
        onFocusCitation={setFocusedCitation}
        onRetryDoc={retryDocument}
        onOpenRelated={() => setRightPanel("related")}
        onClose={() => setRightPanel("none")}
      />

      {/* Related Citations Drawer (Mutually Exclusive with Library Drawer) */}
      {rightPanel === "related" && (
        <RelatedDrawerFrame
          isDesktop={isDesktop}
          hasCitations={activeCitations.length > 0}
          onClose={() => setRightPanel("none")}
        >
          <div className="rounded-2xl border border-[#BFDBFE] bg-[#EFF6FF] p-3 text-[12px] text-[#1E3A8A]">
            <strong>Trích dẫn từ câu trả lời:</strong> Nhãn trích dẫn không xác nhận nội dung hoặc hiệu
            lực. Nội dung có sẵn bên dưới là dữ liệu văn bản từ kho lưu trữ hoặc bản trích demo để đối
            chiếu.
          </div>

          {activeCitations.map((c, i) => {
            const match = findCitationBody(c, citationSearchDocs);
            const targetArticle = match ? match.article : null;
            const resolvedLibDocId = match
              ? resolveLibraryDocId(c, libraryDocs) || match.document.id
              : null;
            const isLiveMatch = Boolean(match && liveDocDetails[match.document.id]);
            const actionLabel = isLiveMatch ? "Xem trong thư viện" : "Xem bản trích demo";

            return (
              <div
                key={c.id || `${c.art || c.label}-${i}`}
                className="rounded-2xl border border-[#CBD5E1] bg-white p-4 shadow-xs"
              >
                <div className="flex items-start justify-between gap-2 border-b border-[#F1F5F9] pb-2">
                  <span className="text-[13px] font-bold text-[#1E3A8A]">{c.label}</span>
                  {match ? (
                    <button
                      type="button"
                      onClick={() => {
                        setLibraryCategory("all");
                        setLibrarySearch("");
                        openLibraryPanel(resolvedLibDocId || undefined, c);
                      }}
                      className="flex shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-semibold text-[#2563EB] hover:bg-[#EFF6FF] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
                    >
                      <span>{actionLabel}</span>
                      <ExternalLink className="h-3 w-3" />
                    </button>
                  ) : (
                    <span className="shrink-0 text-[11px] italic text-[#94A3B8]">
                      Chưa có bản trích
                    </span>
                  )}
                </div>

                {targetArticle ? (
                  <RelatedArticlePreview
                    article={targetArticle}
                    citation={c}
                    openActionLabel={actionLabel}
                  />
                ) : (
                  <p className="mt-2 text-[12px] text-[#64748B]">{citationBodyMessage(c)}</p>
                )}
              </div>
            );
          })}
        </RelatedDrawerFrame>
      )}

      <SettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        isStaging={isStaging}
        settings={settings}
        onUpdateSettings={(next, syncedDate, syncedPipeline) => {
          setSettings(next);
          if (syncedDate !== undefined) {
            userEditedDateRef.current = true;
            setDate(syncedDate);
          }
          if (syncedPipeline !== undefined) setPipeline(syncedPipeline);
        }}
      />
    </div>
  );
}

type AnswerProps = {
  msg: Msg;
  onClickCitation: (citation: Citation) => void;
  onOpenRelated: () => void;
  onRetry: () => void;
  onEditQuery: () => void;
  onSelectMissingFact: (fact: string) => void;
};

function Answer({ msg, onClickCitation, onOpenRelated, onRetry, onEditQuery, onSelectMissingFact }: AnswerProps) {
  const r = msg.result!;
  const [copied, setCopied] = useState(false);
  const [showTrace, setShowTrace] = useState(false);
  const copyTimerRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (copyTimerRef.current !== null) window.clearTimeout(copyTimerRef.current);
    };
  }, []);

  const canonicalIds = r.citations.flatMap((c) => {
    if (!c.id) return [];
    const parsed = parseCitationId(c.id);
    return parsed.label !== parsed.id ? [parsed.id] : [];
  });

  const isErrorCard = r.score === 100 && r.citations.length === 0;

  function copyText() {
    const cleanConclusion = formatInlineCitationsPlainText(r.conclusion);
    const cleanRule = formatInlineCitationsPlainText(r.rule);
    navigator.clipboard.writeText(`${cleanConclusion}\n\nCăn cứ: ${cleanRule}`);
    setCopied(true);
    if (copyTimerRef.current !== null) window.clearTimeout(copyTimerRef.current);
    copyTimerRef.current = window.setTimeout(() => setCopied(false), 2000);
  }

  return (
    <article className="group rounded-3xl border border-[#CBD5E1] bg-white p-4 shadow-xs sm:p-5">
      <AnswerHeader
        pipeline={msg.pipeline}
        date={msg.date}
        answerStatus={r.answer_status}
        isGreeting={r.is_greeting}
        amendedCount={r.amended_citation_ids?.length || 0}
      />

      <AnswerConclusionBox
        result={r}
        isErrorCard={isErrorCard}
        onCitationClick={onClickCitation}
        onSelectMissingFact={onSelectMissingFact}
        onRetry={onRetry}
        onEditQuery={onEditQuery}
      />

      {r.trap && (
        <div className="mt-3 flex items-start gap-2.5 rounded-2xl border border-[#FED7AA] bg-[#FFF7ED] p-3.5 text-[12.5px] text-[#9A3412]">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[#EA580C]" />
          <div className="leading-relaxed">
            <strong>Lưu ý cần rà soát:</strong> {r.trap}
          </div>
        </div>
      )}

      {r.application && r.application.length > 0 && (
        <div className="mt-3.5 space-y-1.5">
          <div className="text-[11.5px] font-bold uppercase tracking-wider text-[#64748B]">
            Các bước đối chiếu mốc thời gian:
          </div>
          <ul className="space-y-1.5 text-[13px] text-[#334155]">
            {r.application.map((app, i) => (
              <li key={i} className="flex items-start gap-2">
                <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-[#1E3A8A]" />
                <MarkdownContent
                  text={app.replace(/^[•\-\*]\s*/, "")}
                  onCitationClick={onClickCitation}
                  className="min-w-0 flex-1"
                />
              </li>
            ))}
          </ul>
        </div>
      )}

      <AnswerAuditSection audit={r.audit} />

      {!r.is_out_of_domain && !r.is_greeting && r.citations && r.citations.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-1.5 border-t border-[#F1F5F9] pt-3">
          <span className="text-[11px] font-bold text-[#64748B]">Văn bản trích dẫn:</span>
          {r.citations.map((c, idx) => (
            <button
              type="button"
              key={c.id || `${c.art || c.label}-${idx}`}
              onClick={() => onClickCitation(c)}
              className="flex items-center gap-1 rounded-full border border-[#BFDBFE] bg-[#EFF6FF] px-2.5 py-1 text-[11px] font-semibold text-[#1E3A8A] transition-colors hover:bg-[#DBEAFE] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
              title="Bấm để mở văn bản và tô sáng đúng điều khoản được trích dẫn"
            >
              <BookOpen className="h-3 w-3 shrink-0" />
              <span>{c.label}</span>
              <ExternalLink className="h-2.5 w-2.5 shrink-0" />
            </button>
          ))}
          <button
            type="button"
            onClick={onOpenRelated}
            className="ml-auto text-[11px] font-semibold text-[#2563EB] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
          >
            Xem bản tóm tắt trích dẫn
          </button>
        </div>
      )}

      {!r.is_out_of_domain && !r.is_greeting && r.citations && r.citations.length > 0 && (
        <div className="mt-3 border-t border-[#F1F5F9] pt-2.5">
          <button
            type="button"
            aria-expanded={showTrace}
            onClick={() => setShowTrace(!showTrace)}
            className="flex items-center gap-1.5 text-[12px] font-medium text-[#1E3A8A] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
          >
            <GitBranch className="h-3.5 w-3.5" />
            <span>
              {showTrace
                ? "Ẩn mô phỏng trích dẫn"
                : "Xem mô phỏng trích dẫn (không phải kết quả xác minh)"}
            </span>
          </button>

          {showTrace && (
            <div className="font-mono-num mt-2 space-y-1.5 rounded-2xl border border-[#CBD5E1] bg-[#0F172A] p-3.5 text-[11.5px] text-[#E2E8F0] shadow-inner">
              <div className="font-bold text-[#94A3B8]">
                Mô phỏng Cypher — không thực thi, không phải kết quả xác minh
              </div>
              <div className="text-[#38BDF8]">{"MATCH (citation)"}</div>
              <div className="text-[#FBBF24]">{`WHERE citation.id IN ${JSON.stringify(canonicalIds)}`}</div>
              <div className="text-[#FBBF24]">{"RETURN citation.id"}</div>
              <div className="text-[#38BDF8]">{`Mốc yêu cầu: ${fmtDate(msg.date)}; không mô phỏng kết quả hiệu lực.`}</div>
              <div className="text-[#38BDF8]">
                {`${r.citations.length - canonicalIds.length} trích dẫn chưa có ID chuẩn được nhận diện; không suy ID từ nhãn hoặc neo thư viện.`}
              </div>
              <div className="mt-2 border-t border-slate-700/60 pt-2 text-[#4ADE80]">
                {`[Mô phỏng]: ${canonicalIds.length} ID chuẩn từ phản hồi; chưa xác minh nội dung hoặc hiệu lực.`}
              </div>
              <div className="text-[#94A3B8]">
                {`→ Căn cứ viện dẫn: ${r.citations.map((c) => c.label).join(", ")}`}
              </div>
            </div>
          )}
        </div>
      )}

      <div className="mt-3 flex items-center justify-between border-t border-[#F1F5F9] pt-2.5 text-[#64748B]">
        <button
          type="button"
          onClick={copyText}
          className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12px] font-medium text-[#475569] transition-colors hover:bg-[#F1F5F9] hover:text-[#1E3A8A] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1E3A8A]/40"
          title="Sao chép kết luận và căn cứ pháp lý"
          aria-label="Sao chép kết luận"
        >
          <Copy className="h-3.5 w-3.5" />
          <span>{copied ? "Đã sao chép vào bộ nhớ tạm!" : "Sao chép kết luận"}</span>
        </button>

        {r.is_out_of_domain && (
          <span className="text-[11px] italic text-[#D97706]">Ngoài phạm vi điều chỉnh</span>
        )}
      </div>
    </article>
  );
}

export default App;
