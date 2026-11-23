import { useCallback, useEffect, useRef, useState } from "react";
import { fetchDocumentDetail, fetchDocuments, fetchHealth, type ApiDocumentSummary } from "../api";
import type { LawDoc } from "../data";

export type RuntimeEnv = "canonical" | "staging";

/** Manage runtime environment detection, live document summaries, and deduplicated detail fetches. */
export function useDocumentCache() {
  const [runtimeEnv, setRuntimeEnv] = useState<RuntimeEnv>("canonical");
  const [liveSummaries, setLiveSummaries] = useState<ApiDocumentSummary[] | null>(null);
  const [liveDocDetails, setLiveDocDetails] = useState<Record<string, LawDoc>>({});
  const [loadingDocIds, setLoadingDocIds] = useState<Set<string>>(() => new Set());
  const [docErrors, setDocErrors] = useState<Record<string, string>>({});

  const mountedRef = useRef(true);
  const summariesRef = useRef<Set<string>>(new Set());
  const cacheRef = useRef<Record<string, LawDoc>>({});
  const inFlightRef = useRef<Map<string, Promise<void>>>(new Map());

  useEffect(() => {
    mountedRef.current = true;
    const controller = new AbortController();

    fetchHealth(controller.signal)
      .then((health) => {
        if (mountedRef.current && health.environment === "staging") {
          setRuntimeEnv("staging");
        }
      })
      .catch(() => {
        /* stay in canonical offline fallback */
      });

    fetchDocuments(controller.signal)
      .then((list) => {
        if (mountedRef.current && Array.isArray(list) && list.length > 0) {
          summariesRef.current = new Set(list.map((s) => s.id));
          setLiveSummaries(list);
        }
      })
      .catch(() => {
        /* keep static demo DOCS fallback when backend is offline */
      });

    return () => {
      mountedRef.current = false;
      controller.abort();
    };
  }, []);

  const ensureDocuments = useCallback((docIds: readonly string[], force = false) => {
    const uniqueIds = Array.from(new Set(docIds.filter(Boolean)));
    for (const docId of uniqueIds) {
      if (!summariesRef.current.has(docId)) continue;
      if (!force && cacheRef.current[docId]) continue;
      if (inFlightRef.current.has(docId)) continue;

      if (mountedRef.current) {
        setLoadingDocIds((prev) => {
          if (prev.has(docId)) return prev;
          const next = new Set(prev);
          next.add(docId);
          return next;
        });
        setDocErrors((prev) => {
          if (!(docId in prev)) return prev;
          const next = { ...prev };
          delete next[docId];
          return next;
        });
      }

      const promise = fetchDocumentDetail(docId)
        .then((doc) => {
          cacheRef.current = { ...cacheRef.current, [docId]: doc };
          if (mountedRef.current) {
            setLiveDocDetails((prev) => ({ ...prev, [docId]: doc }));
          }
        })
        .catch((err: unknown) => {
          const message = err instanceof Error ? err.message : "Không thể tải nội dung văn bản.";
          if (mountedRef.current) {
            setDocErrors((prev) => ({ ...prev, [docId]: message }));
          }
        })
        .finally(() => {
          inFlightRef.current.delete(docId);
          if (mountedRef.current) {
            setLoadingDocIds((prev) => {
              if (!prev.has(docId)) return prev;
              const next = new Set(prev);
              next.delete(docId);
              return next;
            });
          }
        });

      inFlightRef.current.set(docId, promise);
    }
  }, []);

  const retryDocument = useCallback(
    (docId: string) => {
      ensureDocuments([docId], true);
    },
    [ensureDocuments]
  );

  return {
    runtimeEnv,
    liveSummaries,
    liveDocDetails,
    loadingDocIds,
    docErrors,
    ensureDocuments,
    retryDocument,
  };
}

