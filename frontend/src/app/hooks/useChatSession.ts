import type React from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { queryLegalApi } from "../api";
import { getEffectiveDate } from "../config";
import type { Result } from "../data";
import type { Pipeline } from "../components/SettingsDialog";
import { saveConversations, store } from "../utils/citationMatch";

export type Msg = { id: number; q: string; date: string; pipeline: Pipeline; result: Result | null };
export type Conversation = { id: string; title: string; updated: number; messages: Msg[] };

export const REASONING_STEPS = [
  "Chuẩn hóa câu hỏi pháp lý và xác định mốc thời gian thẩm định...",
  "Truy vấn đồ thị tri thức thời gian Neo4j và mở rộng điều khoản liên quan...",
  "Xếp hạng mức độ liên quan và xác minh hiệu lực từng khoản/điểm...",
  "Tổng hợp lập luận IRAC và kiểm định trích dẫn chống ảo giác...",
];

type UseChatSessionArgs = {
  query: string;
  date: string;
  pipeline: Pipeline;
  useLlm: boolean;
  activeFreezeDate: string;
  taRef: React.RefObject<HTMLTextAreaElement | null>;
  onQueryChange: (q: string) => void;
  onResetCitationFocus: () => void;
};

/** Manage conversation history, multi-step query execution, abort handling, and safe persistence. */
export function useChatSession({
  query,
  date,
  pipeline,
  useLlm,
  activeFreezeDate,
  taRef,
  onQueryChange,
  onResetCitationFocus,
}: UseChatSessionArgs) {
  const [convs, setConvs] = useState<Conversation[]>(() => store.get("vat_public_convs", []));
  const [activeId, setActiveId] = useState<string>(() => {
    const list = store.get<Conversation[]>("vat_public_convs", []);
    return list.length > 0 ? list[0].id : "c_initial";
  });
  const [messages, setMessages] = useState<Msg[]>(() => {
    const list = store.get<Conversation[]>("vat_public_convs", []);
    return list.length > 0 ? list[0].messages || [] : [];
  });
  const [loading, setLoading] = useState(false);
  const [step, setStep] = useState(0);

  const activeIdRef = useRef(activeId);
  const focusTimerRef = useRef<number | null>(null);
  const abortControllerRef = useRef<{
    msgId: number;
    originId: string;
    query: string;
    controller: AbortController;
  } | null>(null);

  useEffect(() => {
    saveConversations("vat_public_convs", convs);
  }, [convs]);

  useEffect(() => {
    return () => {
      if (focusTimerRef.current !== null) {
        window.clearTimeout(focusTimerRef.current);
      }
    };
  }, []);

  const newChat = useCallback(() => {
    const id = `c_${Date.now()}`;
    const newConv: Conversation = {
      id,
      title: "Cuộc trò chuyện mới",
      updated: Date.now(),
      messages: [],
    };
    setConvs((prev) => [newConv, ...prev]);
    activeIdRef.current = id;
    setActiveId(id);
    setMessages([]);
    onResetCitationFocus();
    onQueryChange("");
    setLoading(false);
    if (focusTimerRef.current !== null) window.clearTimeout(focusTimerRef.current);
    focusTimerRef.current = window.setTimeout(() => taRef.current?.focus(), 50);
  }, [onQueryChange, onResetCitationFocus, taRef]);

  const selectConv = useCallback(
    (id: string) => {
      activeIdRef.current = id;
      setActiveId(id);
      const found = convs.find((c) => c.id === id);
      if (found) {
        setMessages(found.messages || []);
      }
      onResetCitationFocus();
      setLoading(false);
    },
    [convs, onResetCitationFocus]
  );

  const deleteConv = useCallback(
    (id: string, e: React.MouseEvent) => {
      e.stopPropagation();
      const rest = convs.filter((c) => c.id !== id);
      setConvs(rest);
      if (activeId === id) {
        setLoading(false);
        onResetCitationFocus();
        if (rest.length > 0) {
          activeIdRef.current = rest[0].id;
          setActiveId(rest[0].id);
          setMessages(rest[0].messages || []);
        } else {
          newChat();
        }
      }
    },
    [convs, activeId, newChat, onResetCitationFocus]
  );

  const abortQuery = useCallback(() => {
    const activeReq = abortControllerRef.current;
    if (!activeReq) return;
    activeReq.controller.abort();
    abortControllerRef.current = null;
    setLoading(false);

    if (activeIdRef.current === activeReq.originId) {
      setMessages((prev) => prev.filter((m) => m.id !== activeReq.msgId));
      onQueryChange(activeReq.query);
    }
    setConvs((prev) =>
      prev.map((c) =>
        c.id === activeReq.originId
          ? { ...c, messages: c.messages.filter((m) => m.id !== activeReq.msgId) }
          : c
      )
    );
  }, [onQueryChange]);

  const send = useCallback(
    async (customQ?: string, customDate?: string) => {
      const q = (customQ ?? query).trim();
      const d = customDate ?? date;
      if (!q || loading) return;

      const originId = activeId;
      const id = Date.now();
      const userMsg: Msg = { id, q, date: d, pipeline, result: null };
      const nextMsgs = [...messages, userMsg];
      setMessages(nextMsgs);
      onQueryChange("");
      setLoading(true);
      setStep(0);

      setConvs((prev) => {
        const title = q.slice(0, 36) + (q.length > 36 ? "..." : "");
        if (!prev.some((c) => c.id === originId)) {
          return [{ id: originId, title, updated: Date.now(), messages: nextMsgs }, ...prev];
        }
        return prev.map((c) =>
          c.id === originId
            ? {
                ...c,
                title: c.messages.length === 0 ? title : c.title,
                updated: Date.now(),
                messages: nextMsgs,
              }
            : c
        );
      });

      const historyPayload = messages
        .filter((m) => m.result !== null)
        .flatMap((m) => [
          { role: "user", content: m.q },
          { role: "assistant", content: m.result!.conclusion },
        ]);
      const effectiveDate = getEffectiveDate(d, activeFreezeDate);
      const controller = new AbortController();
      abortControllerRef.current = { msgId: id, originId, query: q, controller };

      const apiPromise = queryLegalApi(
        q,
        effectiveDate,
        pipeline,
        useLlm,
        historyPayload,
        undefined,
        controller.signal
      ).then(
        (res) => ({ res, error: null }),
        (error: unknown) => ({ res: null, error })
      );

      for (let s = 0; s < REASONING_STEPS.length - 1; s++) {
        if (controller.signal.aborted) break;
        setStep(s);
        await new Promise((r) => setTimeout(r, 220));
      }

      try {
        const outcome = await apiPromise;
        if (outcome.error) throw outcome.error;
        const res = outcome.res!;
        setStep(REASONING_STEPS.length - 1);
        await new Promise((r) => setTimeout(r, 120));

        const finalMsgs = nextMsgs.map((m) => (m.id === id ? { ...m, result: res } : m));
        if (activeIdRef.current === originId) {
          setMessages(finalMsgs);
          setLoading(false);
        }
        setConvs((prev) =>
          prev.map((c) =>
            c.id === originId ? { ...c, updated: Date.now(), messages: finalMsgs } : c
          )
        );
      } catch (err: unknown) {
        if (err instanceof Error && err.name === "AbortError") {
          return;
        }
        const errorMsg =
          err instanceof Error
            ? err.message
            : "Không thể kết nối máy chủ thẩm định pháp lý (Backend offline).";
        const errorResult: Result = {
          score: 100,
          issue: `Lỗi kết nối máy chủ thẩm định: "${q}"`,
          rule: "Không thể kết nối với dịch vụ backend FastAPI.",
          citations: [],
          application: [errorMsg],
          conclusion: errorMsg,
          audit: [{ tone: "bad", text: errorMsg }],
        };
        const finalMsgs = nextMsgs.map((m) => (m.id === id ? { ...m, result: errorResult } : m));
        if (activeIdRef.current === originId) {
          setMessages(finalMsgs);
          setLoading(false);
        }
        setConvs((prev) =>
          prev.map((c) =>
            c.id === originId ? { ...c, updated: Date.now(), messages: finalMsgs } : c
          )
        );
      } finally {
        if (abortControllerRef.current?.msgId === id) {
          abortControllerRef.current = null;
        }
      }
    },
    [query, date, loading, activeId, pipeline, messages, onQueryChange, activeFreezeDate, useLlm]
  );

  return {
    convs,
    activeId,
    messages,
    loading,
    step,
    newChat,
    selectConv,
    deleteConv,
    abortQuery,
    send,
  };
}

