import { parseCitationId } from "../api";
import { DOCUMENT_LABELS, type Article, type Citation, type Clause, type LawDoc, type Point } from "../data";

export type DocCategory = "all" | "luat" | "nghidinh" | "thongtu" | "other";

export type ParsedProvisionTarget = {
  docId: string | null;
  legacyDocId: string | null;
  articleNo: string | null;
  articleId: string | null;
  legacyArticleId: string | null;
  clauseKey: string | null;
  clauseId: string | null;
  pointKey: string | null;
  pointId: string | null;
  level: "point" | "clause" | "article" | "doc" | "unknown";
};

export type DocCitationMatch = {
  key: string;
  citation: Citation;
  articleId: string;
  clauseId?: string;
  pointId?: string;
  label: string;
};

export type PreviewClausesResult = {
  clauses: Clause[];
  matchedIndices: Set<number>;
  isExactClauseMatch: boolean;
  omittedCount: number;
};

export const CONVERSATION_STORAGE_LIMIT = 40;

/** Format ISO YYYY-MM-DD date string to DD/MM/YYYY for Vietnamese display. */
export function fmtDate(iso: string): string {
  if (!iso || !iso.includes("-")) return iso;
  const parts = iso.split("-");
  if (parts.length !== 3) return iso;
  return `${parts[2]}/${parts[1]}/${parts[0]}`;
}

/** Normalize staging _d_1 point suffix vs Vietnamese "đ" into a canonical token. */
export function normalizePointKey(raw: string | undefined | null): string | null {
  if (!raw) return null;
  const lowered = raw.toLowerCase();
  return lowered === "d_1" ? "đ" : lowered;
}

/** Parse canonical or legacy citation identifiers down to Article, Clause, and Point targets. */
export function parseCitationTarget(citation: Citation): ParsedProvisionTarget {
  const rawId = citation.id || "";
  const canonicalMatch = rawId.match(
    /^([A-Z0-9_]+?)(?:_D(\d+[a-z]?)(?:_K(\d+[a-z]?)(?:_([a-zđ]|d_1))?)?)?$/i
  );

  if (canonicalMatch) {
    const docKey = canonicalMatch[1].toUpperCase();
    const meta = Object.hasOwn(DOCUMENT_LABELS, docKey) ? DOCUMENT_LABELS[docKey] : null;
    if (meta || citation.doc) {
      const articleNo = canonicalMatch[2]?.toLowerCase() ?? null;
      const clauseKey = canonicalMatch[3]?.toLowerCase() ?? null;
      const rawPoint = canonicalMatch[4]?.toLowerCase() ?? null;
      const pointKey = normalizePointKey(rawPoint);

      const docId = meta ? docKey : null;
      const legacyDocId = meta?.libraryDocId ?? citation.doc ?? null;
      const articleId = docId && articleNo ? `${docId}_D${articleNo}` : null;
      const legacyArticleId = legacyDocId && articleNo ? `${legacyDocId}-d${articleNo}` : citation.art ?? null;
      const clauseId = articleId && clauseKey ? `${articleId}_K${clauseKey}` : null;
      const pointId = clauseId && rawPoint ? `${clauseId}_${rawPoint}` : null;

      const level = pointKey ? "point" : clauseKey ? "clause" : articleNo ? "article" : "doc";
      return {
        docId,
        legacyDocId,
        articleNo,
        articleId,
        legacyArticleId,
        clauseKey,
        clauseId,
        pointKey,
        pointId,
        level,
      };
    }
  }

  // Fallback for legacy demo citations (e.g., art = "l48-d9") without canonical underscore IDs.
  if (citation.art) {
    const legMatch = citation.art.match(/^([a-z0-9]+)-d(\d+[a-z]?)$/i);
    return {
      docId: null,
      legacyDocId: citation.doc || legMatch?.[1]?.toLowerCase() || null,
      articleNo: legMatch?.[2]?.toLowerCase() ?? null,
      articleId: null,
      legacyArticleId: citation.art,
      clauseKey: null,
      clauseId: null,
      pointKey: null,
      pointId: null,
      level: "article",
    };
  }

  return {
    docId: null,
    legacyDocId: citation.doc ?? null,
    articleNo: null,
    articleId: null,
    legacyArticleId: null,
    clauseKey: null,
    clauseId: null,
    pointKey: null,
    pointId: null,
    level: citation.doc ? "doc" : "unknown",
  };
}

/** Check whether a citation resolves to the given library document (live ID or legacy demo ID). */
export function doesCitationMatchDoc(citation: Citation, doc: LawDoc): boolean {
  const target = parseCitationTarget(citation);
  if (target.docId && doc.id === target.docId) return true;
  if (target.legacyDocId && doc.id === target.legacyDocId) return true;
  const docMeta = Object.hasOwn(DOCUMENT_LABELS, doc.id) ? DOCUMENT_LABELS[doc.id] : null;
  if (target.legacyDocId && docMeta?.libraryDocId === target.legacyDocId) return true;
  return false;
}

/** Check whether a citation targets the given Article inside its parent document. */
export function doesArticleMatchCitation(article: Article, citation: Citation): boolean {
  const target = parseCitationTarget(citation);
  if (target.articleId && article.id.toLowerCase() === target.articleId.toLowerCase()) return true;
  if (target.legacyArticleId && article.id.toLowerCase() === target.legacyArticleId.toLowerCase()) return true;
  if (target.articleNo && String(article.no).toLowerCase() === target.articleNo.toLowerCase()) {
    const idPrefix = article.id.split(/_D|-d/i)[0]?.toUpperCase();
    if (!target.docId || idPrefix === target.docId || idPrefix === target.legacyDocId?.toUpperCase()) {
      return true;
    }
  }
  return false;
}

/** Check whether a citation targets (or descends into) the given Clause inside an Article. */
export function doesClauseMatchCitation(clause: Clause, article: Article, citation: Citation): boolean {
  if (!doesArticleMatchCitation(article, citation)) return false;
  const target = parseCitationTarget(citation);
  if (!target.clauseKey) return false;
  if (target.clauseId && clause.id && clause.id.toLowerCase() === target.clauseId.toLowerCase()) {
    return true;
  }
  const clauseLabel = (clause.label || String(clause.n)).toLowerCase();
  return clauseLabel === target.clauseKey;
}

/** Check whether a citation pinpoints the given Point inside a Clause. */
export function doesPointMatchCitation(
  point: Point,
  clause: Clause,
  article: Article,
  citation: Citation
): boolean {
  if (!doesClauseMatchCitation(clause, article, citation)) return false;
  const target = parseCitationTarget(citation);
  if (!target.pointKey) return false;
  if (point.id && target.pointId && point.id.toLowerCase() === target.pointId.toLowerCase()) {
    return true;
  }
  const pointSuffix = point.id?.match(/_([a-zđ]|d_1)$/i)?.[1];
  const normalizedPoint = normalizePointKey(pointSuffix) || normalizePointKey(point.l);
  return normalizedPoint === target.pointKey;
}

/** Resolve canonical library document ID that best matches a citation against loaded library docs. */
export function resolveLibraryDocId(citation: Citation, libraryDocs: readonly LawDoc[]): string | null {
  const exact = libraryDocs.find((d) => doesCitationMatchDoc(citation, d));
  if (exact) return exact.id;
  const target = parseCitationTarget(citation);
  return target.docId || target.legacyDocId;
}

/** Build ordered unique highlight anchors inside a document for in-drawer navigation. */
export function collectDocCitationMatches(doc: LawDoc, citations: readonly Citation[]): DocCitationMatch[] {
  const seen = new Set<string>();
  const matches: DocCitationMatch[] = [];
  const docCitations = citations.filter((c) => doesCitationMatchDoc(c, doc));

  for (const ch of doc.chapters) {
    for (const art of ch.articles) {
      for (const c of docCitations) {
        if (!doesArticleMatchCitation(art, c)) continue;
        const target = parseCitationTarget(c);
        const key = `${art.id}|${target.clauseKey || "*"}|${target.pointKey || "*"}`;
        if (seen.has(key)) continue;
        seen.add(key);

        let matchedClause: Clause | undefined;
        let matchedPoint: Point | undefined;
        if (target.clauseKey) {
          matchedClause = art.clauses.find((cl) => doesClauseMatchCitation(cl, art, c));
          if (matchedClause && target.pointKey) {
            matchedPoint = matchedClause.points?.find((pt) => doesPointMatchCitation(pt, matchedClause!, art, c));
          }
        }
        matches.push({
          key,
          citation: c,
          articleId: art.id,
          clauseId: matchedClause?.id || target.clauseId || undefined,
          pointId: matchedPoint?.id || target.pointId || undefined,
          label: c.label,
        });
      }
    }
  }
  return matches;
}

/** Select the exact cited Clause(s) for Related Drawer preview instead of truncating at clauses.slice(0, 2). */
export function getPreviewClausesForCitation(article: Article, citation: Citation): PreviewClausesResult {
  const target = parseCitationTarget(citation);
  if (target.clauseKey) {
    const matchedIndices = new Set<number>();
    const matched: Clause[] = [];
    article.clauses.forEach((cl, idx) => {
      if (doesClauseMatchCitation(cl, article, citation)) {
        matchedIndices.add(idx);
        matched.push(cl);
      }
    });
    if (matched.length > 0) {
      return {
        clauses: matched,
        matchedIndices: new Set(matched.map((_, i) => i)),
        isExactClauseMatch: true,
        omittedCount: Math.max(0, article.clauses.length - matched.length),
      };
    }
  }

  const preview = article.clauses.slice(0, 2);
  return {
    clauses: preview,
    matchedIndices: new Set<number>(),
    isExactClauseMatch: false,
    omittedCount: Math.max(0, article.clauses.length - preview.length),
  };
}

/** Map all 33 staging and 5 demo document IDs into 4 non-overlapping categories. */
export function getDocCategory(id: string): Exclude<DocCategory, "all"> {
  if (id.startsWith("LUAT_") || id === "l48" || id === "l13") return "luat";
  if (id.startsWith("ND_") || id === "nd181" || id === "nd359" || id === "nd144") return "nghidinh";
  if (id.startsWith("TT_") || id === "tt69") return "thongtu";
  return "other";
}

/** Split trailing unverified/container diagnostic lines out of deterministic conclusion blocks. */
export function splitTechnicalDiagnosticLines(text: string): {
  primaryText: string;
  diagnosticLines: string[];
} {
  if (!text) return { primaryText: "", diagnosticLines: [] };
  const lines = text.split("\n");
  const primary: string[] = [];
  const diagnosticLines: string[] = [];

  for (const line of lines) {
    const trimmed = line.trim();
    const isTechLine =
      /^(?:Hệ thống chưa xác minh được chứng cứ(?:\s|:|$)|Container is not citable|changed or blocked descendants:|\[audit\]|excluded_by_budget)/i.test(
        trimmed
      ) ||
      (/^(?:LUAT|ND|TT|NQ|QD|VBHN)\s+[0-9A-Z\s]+:\s+(?:ngày (?:bắt đầu|kết thúc) hiệu lực theo dữ liệu là|Consolidation reference)/i.test(
        trimmed
      )) ||
      (/(?:Container is not citable|Consolidation reference is not operative)/i.test(trimmed) && trimmed.length > 50);

    if (isTechLine) {
      diagnosticLines.push(trimmed);
    } else {
      primary.push(line);
    }
  }

  return {
    primaryText: primary.join("\n").trim() || text.trim(),
    diagnosticLines,
  };
}

const INLINE_CITATION_TAG_RE =
  /\[([A-Z0-9_]+(?:_D\d+[a-z]?(?:_K\d+[a-z]?(?:_(?:[a-zđ]|d_1))?)?)?)\](?!\()/gi;

function isRecognizedInlineCitation(parsed: Citation): boolean {
  return parsed.label !== parsed.id || Object.hasOwn(DOCUMENT_LABELS, parsed.id);
}

/** Convert recognized [DOC_ID_...] tags into clickable `#cite:` Markdown links while leaving unknown tags untouched. */
export function formatInlineCitationsToMarkdownLinks(text: string): string {
  if (!text) return "";
  return text.replace(INLINE_CITATION_TAG_RE, (full, rawId: string) => {
    const parsed = parseCitationId(rawId);
    if (!isRecognizedInlineCitation(parsed)) return full;
    return `[${parsed.label}](#cite:${encodeURIComponent(parsed.id)})`;
  });
}

/** Replace recognized [DOC_ID_...] tags with clean Vietnamese labels for plain-text clipboard export. */
export function formatInlineCitationsPlainText(text: string): string {
  if (!text) return "";
  return text
    .split("\n")
    .map((line) => {
      const tokens = line.trim().split(/\s+/);
      const allBracketTags =
        tokens.length > 1 && tokens.every((tok) => INLINE_CITATION_TAG_RE.test(tok) && (INLINE_CITATION_TAG_RE.lastIndex = 0) === 0);
      if (allBracketTags) {
        return tokens
          .map((tok) => {
            const inner = tok.slice(1, -1);
            const parsed = parseCitationId(inner);
            return isRecognizedInlineCitation(parsed) ? parsed.label : tok;
          })
          .join("; ");
      }
      return line.replace(INLINE_CITATION_TAG_RE, (full, rawId: string) => {
        const parsed = parseCitationId(rawId);
        return isRecognizedInlineCitation(parsed) ? parsed.label : full;
      });
    })
    .join("\n");
}

export const store = {
  get<T>(key: string, fallback: T): T {
    try {
      const raw = localStorage.getItem(key);
      return raw ? (JSON.parse(raw) as T) : fallback;
    } catch {
      return fallback;
    }
  },
  set<T>(key: string, value: T): boolean {
    try {
      localStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch {
      return false;
    }
  },
};

/** Persist conversation history while capping count to avoid QuotaExceededError. */
export function saveConversations<T>(key: string, convs: readonly T[]): void {
  let trimmed = convs.slice(0, CONVERSATION_STORAGE_LIMIT);
  if (store.set(key, trimmed)) return;
  while (trimmed.length > 1) {
    trimmed = trimmed.slice(0, Math.max(1, Math.floor(trimmed.length / 2)));
    if (store.set(key, trimmed)) return;
  }
}

