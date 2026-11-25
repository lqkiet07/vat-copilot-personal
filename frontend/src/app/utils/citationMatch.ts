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

/** Split trailing internal/container diagnostic lines out of conclusion blocks while keeping user-facing temporal explanations visible. */
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
    const isTemporalReason =
      /^(?:LUAT|ND|TT|NQ|QD|VBHN)\s+[0-9A-Z\s]+:\s+ngày (?:bắt đầu|kết thúc) hiệu lực theo dữ liệu là/i.test(
        trimmed
      );
    if (isTemporalReason) {
      const formattedTemporal = formatTechnicalDiagnosticText(trimmed).replace(
        /\b(\d{4})-(\d{2})-(\d{2})\b/g,
        (_, y: string, m: string, d: string) => `${d}/${m}/${y}`
      );
      primary.push(formattedTemporal);
      continue;
    }

    const isTechLine =
      /^(?:Hệ thống chưa xác minh được chứng cứ(?:\s|:|$)|Container is not citable|changed or blocked descendants:|\[audit\]|excluded_by_budget)/i.test(
        trimmed
      ) ||
      (/^(?:LUAT|ND|TT|NQ|QD|VBHN)\s+[0-9A-Z\s]+:\s+Consolidation reference/i.test(trimmed)) ||
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

const CANONICAL_DB_ID_RE =
  /\b(?:LUAT|ND|TT|NQ|QD|VBHN)_[0-9]+_\d{4}(?:_D\d+[a-z]?(?:_K\d+[a-z]?(?:_(?:[a-zđ]|d_1))?)?)?\b/gi;
const SPACED_DB_ID_RE =
  /\b(LUAT|ND|TT|NQ|QD|VBHN)\s+(\d+)\s+(\d{4})(?:\s+D(\d+[a-z]?)(?:\s+K(\d+[a-z]?)(?:\s+([a-zđ]|d_1))?)?)?\b/gi;

/** Translate technical English diagnostics and replace raw DB identifiers with readable Vietnamese labels. */
export function formatTechnicalDiagnosticText(raw: string): string {
  if (!raw) return "";
  let formatted = raw
    .replace(
      /Container is not citable;\s*changed or blocked descendants:/gi,
      "Điều/Khoản cha không trích dẫn gộp do có khoản/điểm con đã sửa đổi hoặc cần xác minh riêng:"
    )
    .replace(
      /Consolidation reference is not operative evidence/gi,
      "Văn bản hợp nhất chỉ mang tính tham khảo, không thay thế căn cứ gốc có hiệu lực"
    )
    .replace(/missing explicit authority/gi, "không tìm thấy văn bản được nêu trong cơ sở dữ liệu")
    .replace(/missing explicit unit/gi, "không tìm thấy điều khoản được nêu trong cơ sở dữ liệu")
    .replace(/LLM_PROVIDER_UNAVAILABLE:\s*/g, "");

  formatted = formatted.replace(CANONICAL_DB_ID_RE, (token) => {
    const parsed = parseCitationId(token);
    return isRecognizedInlineCitation(parsed) ? parsed.label : token;
  });

  formatted = formatted.replace(
    SPACED_DB_ID_RE,
    (full, prefix: string, num: string, year: string, art?: string, clause?: string, point?: string) => {
      let canonical = `${prefix.toUpperCase()}_${num}_${year}`;
      if (art) canonical += `_D${art.toLowerCase()}`;
      if (clause) canonical += `_K${clause.toLowerCase()}`;
      if (point) canonical += `_${point.toLowerCase()}`;
      const parsed = parseCitationId(canonical);
      return isRecognizedInlineCitation(parsed) ? parsed.label : full;
    }
  );

  return formatted;
}

/** Extract user-facing notice when the active AI provider returned no output or hit quota limits. */
export function extractProviderNotice(audit?: readonly { text: string }[]): string | null {
  if (!audit || audit.length === 0) return null;
  const hit = audit.some((item) => item.text.includes("LLM_PROVIDER_UNAVAILABLE"));
  if (!hit) return null;
  return "Mô hình AI tạm thời không phản hồi hoặc hết hạn mức. Bên dưới là trích đoạn các điều khoản gốc đã được đối chiếu hiệu lực.";
}

/** Strip a duplicate leading clause/point marker (e.g. "1. " or "a) ") when rendered outside the body span. */
export function stripLeadingClauseNumber(text: string, label: string): string {
  if (!text) return "";
  const trimmed = text.trimStart();
  const cleanLabel = (label || "").trim().replace(/[.)]+$/, "");
  if (!cleanLabel) return trimmed;
  const escaped = cleanLabel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const prefixRe = new RegExp(`^${escaped}[.)]\\s+`, "i");
  return trimmed.replace(prefixRe, "");
}

export type HighlightSegment = {
  text: string;
  highlight: boolean;
};

const OPERATIVE_STOPWORDS = new Set([
  "của", "và", "các", "cho", "theo", "quy", "định", "tại", "với", "về",
  "là", "có", "phải", "hoặc", "trong", "khi", "nếu", "thì", "từ", "trên",
  "dưới", "này", "đó", "một", "những", "được", "bằng", "như", "sau", "trước",
  "đối", "vào", "ra", "nên", "mà", "đã", "đang", "sẽ", "để", "gồm", "bao",
  "trường", "hợp", "hoạt", "động", "mức", "số", "việc", "nội", "dung",
  "khoản", "điều", "điểm", "thuộc",
]);

const DANGLE_START_TOKENS = new Set(["tượng", "trị", "gia", "tăng", "cá", "nhân", "hội"]);
const DANGLE_END_TOKENS = new Set(["kinh", "giá", "trị", "trực", "cá", "khoản", "điều", "chịu"]);

const GENERIC_LEGAL_PHRASES = new Set([
  "thuế giá trị gia tăng",
  "giá trị gia tăng",
  "thuế gtgt",
  "cơ quan thuế",
  "quy định pháp luật",
]);

// Whole grammatical VAT legal phrases highlighted cleanly when query/conclusion touches that domain.
const CANONICAL_VAT_PHRASES = [
  "không chịu thuế giá trị gia tăng",
  "chịu thuế giá trị gia tăng",
  "phương pháp tính trực tiếp theo doanh thu",
  "phương pháp khấu trừ thuế",
  "hộ kinh doanh, cá nhân kinh doanh",
  "mức thuế suất cao nhất",
  "thuế suất 0%",
  "thuế suất 5%",
  "thuế suất 8%",
  "thuế suất 10%",
  "chứng từ thanh toán không dùng tiền mặt",
  "không dùng tiền mặt",
  "hóa đơn điện tử",
  "tiêu dùng ở ngoài việt nam",
];

const NUMERIC_THRESHOLD_RE =
  /(?:\b\d+(?:[.,]\d+)?\s*(?:%|tỷ\s+đồng|triệu\s+đồng|nghìn\s+đồng)\b|\btỷ\s+lệ\s*%)/gi;

function normalizeNumericToken(raw: string): string {
  return raw
    .toLowerCase()
    .replace(/\s+/g, " ")
    .replace(/\b0+(\d)/g, "$1")
    .trim();
}

/** Highlight high-signal monetary thresholds, VAT legal statuses, and whole shared key phrases inside a cited clause/point. */
export function highlightOperativeSpans(clauseText: string, contextText: string): HighlightSegment[] {
  if (!clauseText) return [];
  const trimmedCtx = (contextText || "").trim();
  if (!trimmedCtx) return [{ text: clauseText, highlight: false }];

  const ctxTokens: Array<{ norm: string }> = [];
  for (const m of trimmedCtx.matchAll(/[\p{L}\p{N}%/-]+/gu)) {
    ctxTokens.push({ norm: normalizeNumericToken(m[0]) });
  }
  const hasContentTokenInCtx = ctxTokens.some(
    (t) => t.norm.length >= 2 && !OPERATIVE_STOPWORDS.has(t.norm)
  );
  if (!hasContentTokenInCtx) {
    return [{ text: clauseText, highlight: false }];
  }

  const normalizedCtx = normalizeNumericToken(trimmedCtx);
  const ctxNgrams = new Set<string>();
  for (let n = 2; n <= 8; n++) {
    for (let i = 0; i <= ctxTokens.length - n; i++) {
      ctxNgrams.add(ctxTokens.slice(i, i + n).map((t) => t.norm).join(" "));
    }
  }

  type CandidateSpan = { start: number; end: number; priority: number };
  const candidates: CandidateSpan[] = [];
  const lowerClause = clauseText.toLowerCase();

  // 1. Numeric monetary thresholds & tax rates (always highlight inside cited clause when context discusses tax/amounts).
  const ctxMentionsNumbersOrTax = /\d|thuế|doanh thu|tỷ|triệu|hộ kinh doanh/.test(normalizedCtx);
  NUMERIC_THRESHOLD_RE.lastIndex = 0;
  let numMatch: RegExpExecArray | null;
  while ((numMatch = NUMERIC_THRESHOLD_RE.exec(clauseText)) !== null) {
    const start = numMatch.index;
    const end = start + numMatch[0].length;
    const normFigure = normalizeNumericToken(numMatch[0]);
    if (ctxMentionsNumbersOrTax || normalizedCtx.includes(normFigure) || ctxNgrams.has(normFigure)) {
      candidates.push({ start, end, priority: 150 + (end - start) });
    }
  }

  // 2. Whole canonical VAT legal phrases when context discusses VAT/tax/invoices/households.
  if (/thuế|gtgt|hộ|doanh thu|xuất khẩu|hóa đơn|khấu trừ|tiền/.test(normalizedCtx)) {
    for (const phrase of CANONICAL_VAT_PHRASES) {
      let searchFrom = 0;
      while (searchFrom < lowerClause.length) {
        const idx = lowerClause.indexOf(phrase, searchFrom);
        if (idx === -1) break;
        candidates.push({ start: idx, end: idx + phrase.length, priority: 110 + phrase.length });
        searchFrom = idx + phrase.length;
      }
    }
  }

  // 3. Whole n-gram overlap (without chopping compound words at dangling boundaries).
  const clauseTokens: Array<{ norm: string; start: number; end: number }> = [];
  for (const m of clauseText.matchAll(/[\p{L}\p{N}%/-]+/gu)) {
    if (m.index === undefined) continue;
    clauseTokens.push({
      norm: normalizeNumericToken(m[0]),
      start: m.index,
      end: m.index + m[0].length,
    });
  }

  for (let n = 8; n >= 2; n--) {
    for (let i = 0; i <= clauseTokens.length - n; i++) {
      const slice = clauseTokens.slice(i, i + n);
      const first = slice[0].norm;
      const last = slice[slice.length - 1].norm;
      if (DANGLE_START_TOKENS.has(first) || DANGLE_END_TOKENS.has(last)) continue;
      if (OPERATIVE_STOPWORDS.has(first) || OPERATIVE_STOPWORDS.has(last)) continue;

      const phrase = slice.map((t) => t.norm).join(" ");
      if (!ctxNgrams.has(phrase) || GENERIC_LEGAL_PHRASES.has(phrase)) continue;

      const contentCount = slice.filter((t) => !OPERATIVE_STOPWORDS.has(t.norm) && t.norm.length > 1).length;
      if (contentCount < 2) continue;

      const start = slice[0].start;
      const end = slice[slice.length - 1].end;
      candidates.push({ start, end, priority: 20 + n * 5 + contentCount });
    }
  }

  if (candidates.length === 0) {
    return [{ text: clauseText, highlight: false }];
  }

  // Pick highest-priority non-overlapping spans up to a readable cap so the whole clause is not painted.
  candidates.sort((a, b) => b.priority - a.priority || a.start - b.start);
  const chosen: Array<{ start: number; end: number }> = [];
  const maxHighlightChars = Math.max(85, Math.floor(clauseText.length * 0.48));
  let totalHighlighted = 0;

  for (const cand of candidates) {
    if (chosen.some((c) => !(cand.end <= c.start || cand.start >= c.end))) continue;
    const spanLen = cand.end - cand.start;
    if (totalHighlighted > 0 && totalHighlighted + spanLen > maxHighlightChars) continue;
    chosen.push({ start: cand.start, end: cand.end });
    totalHighlighted += spanLen;
  }

  if (chosen.length === 0) {
    return [{ text: clauseText, highlight: false }];
  }

  chosen.sort((a, b) => a.start - b.start);
  const merged: Array<{ start: number; end: number }> = [chosen[0]];
  for (let i = 1; i < chosen.length; i++) {
    const prev = merged[merged.length - 1];
    const curr = chosen[i];
    if (curr.start - prev.end <= 1 && /^\s*$/.test(clauseText.slice(prev.end, curr.start))) {
      prev.end = curr.end;
    } else {
      merged.push(curr);
    }
  }

  const segments: HighlightSegment[] = [];
  let cursor = 0;
  for (const span of merged) {
    if (span.start > cursor) {
      segments.push({ text: clauseText.slice(cursor, span.start), highlight: false });
    }
    segments.push({ text: clauseText.slice(span.start, span.end), highlight: true });
    cursor = span.end;
  }
  if (cursor < clauseText.length) {
    segments.push({ text: clauseText.slice(cursor), highlight: false });
  }
  return segments;
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

