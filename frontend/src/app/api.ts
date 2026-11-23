import {
  type Result,
  type Citation,
  type AuditItem,
  type LawDoc,
  type Article,
  type Clause,
  DOCUMENT_LABELS,
  type DocumentLabel,
} from "./data";
export { DOCUMENT_LABELS, type DocumentLabel };

export type ApiDocumentSummary = {
  id: string;
  code: string;
  title: string;
  valid_from: string;
  valid_to: string;
};

type ApiPoint = { id?: string; code?: string; content?: string };
type ApiClause = { id?: string; code?: string; content?: string; points?: ApiPoint[] };
type ApiArticle = {
  id: string;
  code?: string;
  title?: string;
  content?: string;
  lead?: string;
  clauses?: ApiClause[];
};
export type ApiDocumentDetail = ApiDocumentSummary & {
  status?: string;
  chapters?: Array<{ title?: string; articles?: ApiArticle[] }>;
  articles?: ApiArticle[];
};

class HttpApiError extends Error {}

export function parseCitationId(cid: string): Citation {
  if (!cid || typeof cid !== "string") {
    return { id: cid || "", label: cid || "", doc: null, art: null };
  }

  const documentId = cid.toUpperCase();
  if (Object.hasOwn(DOCUMENT_LABELS, documentId)) {
    return {
      id: documentId,
      label: DOCUMENT_LABELS[documentId].label,
      doc: DOCUMENT_LABELS[documentId].libraryDocId,
      art: null,
    };
  }

  const match = cid.match(/^([A-Z0-9_]+)_D(\d+[a-z]?)(?:_K(\d+[a-z]?)(?:_([a-zđ]))?)?$/i);
  if (match) {
    const docKey = match[1].toUpperCase();
    const articleNum = match[2].toLowerCase();
    const clauseNum = match[3]?.toLowerCase();
    const pointLetter = match[4]?.toLowerCase();

    const docMeta = Object.hasOwn(DOCUMENT_LABELS, docKey) ? DOCUMENT_LABELS[docKey] : null;
    if (docMeta) {
      const prefix = pointLetter
        ? `Điểm ${pointLetter} Khoản ${clauseNum} `
        : clauseNum
        ? `Khoản ${clauseNum} `
        : "";

      const docShortName = docMeta.libraryDocId === "l48"
        ? "Luật 48/2024"
        : docMeta.label;

      const label = `${prefix}Điều ${articleNum} ${docShortName}`;
      const doc = docMeta.libraryDocId;
      const art = doc ? `${doc}-d${articleNum}` : null;

      const id = `${docKey}_D${articleNum}${clauseNum ? `_K${clauseNum}` : ""}${pointLetter ? `_${pointLetter}` : ""}`;
      return { id, label, doc, art };
    }
  }

  return { id: cid, label: cid, doc: null, art: null };
}

export function findCitationBody(
  citation: Citation,
  docs: readonly LawDoc[]
): { document: LawDoc; article: Article } | null {
  const cidMatch = citation.id?.match(/^([A-Z0-9_]+)_D(\d+[a-z]?)(?:_|$)/i);
  const canonicalDocId = cidMatch ? cidMatch[1].toUpperCase() : null;
  const canonicalArtId = cidMatch ? `${canonicalDocId}_D${cidMatch[2].toLowerCase()}` : null;
  if (!citation.art && !canonicalArtId) return null;
  const document = docs.find(
    (d) => (citation.doc && d.id === citation.doc) || (canonicalDocId && d.id === canonicalDocId)
  );
  if (!document) return null;
  for (const ch of document.chapters) {
    for (const a of ch.articles) {
      if (
        (citation.art && a.id === citation.art) ||
        (canonicalArtId && a.id.toLowerCase() === canonicalArtId.toLowerCase())
      ) {
        return { document, article: a };
      }
    }
  }
  return null;
}

function extractOrderNumber(raw: string | undefined, pattern: RegExp, fallback: number): number {
  const matched = raw?.match(pattern);
  return matched ? parseInt(matched[1], 10) : fallback;
}

function mapApiArticle(raw: ApiArticle, idx: number): Article {
  const no = extractOrderNumber(raw.id, /_D(\d+)/i, extractOrderNumber(raw.code, /(\d+)/, idx + 1));
  const clauses: Clause[] = (raw.clauses || []).map((cl, cIdx) => ({
    n: extractOrderNumber(cl.id, /_K(\d+)/i, extractOrderNumber(cl.code, /(\d+)/, cIdx + 1)),
    text: cl.content || "",
    points: cl.points?.map((pt, pIdx) => {
      const letterMatch = pt.code?.match(/\b([a-zđ])$/i) || pt.id?.match(/_([a-zđ])$/i);
      return {
        l: letterMatch ? letterMatch[1].toLowerCase() : String(pIdx + 1),
        text: pt.content || "",
      };
    }),
  }));
  const fallbackText = raw.content || raw.lead || "";
  if (clauses.length === 0 && fallbackText) {
    clauses.push({ n: 1, text: fallbackText });
  }
  return {
    id: raw.id,
    no,
    title: raw.title || raw.code || `Điều ${no}`,
    clauses,
  };
}

export function apiDocToLawDoc(doc: ApiDocumentDetail): LawDoc {
  const meta = Object.hasOwn(DOCUMENT_LABELS, doc.id) ? DOCUMENT_LABELS[doc.id] : null;
  const nonEmptyChapters = (doc.chapters || []).filter((ch) => (ch.articles || []).length > 0);
  const chapters = nonEmptyChapters.length > 0
    ? nonEmptyChapters.map((ch, idx) => ({
        title: ch.title || `Phần ${idx + 1}`,
        articles: (ch.articles || []).map(mapApiArticle),
      }))
    : [{
        title: "Nội dung văn bản",
        articles: (doc.articles || []).map(mapApiArticle),
      }];
  const isLimited = doc.status === "EXPIRED" || (Boolean(doc.valid_to) && doc.valid_to !== "9999-12-31");
  return {
    id: doc.id,
    tab: meta?.label || doc.code || doc.id,
    full: doc.title ? `${doc.code} — ${doc.title}` : doc.code || doc.id,
    badge: {
      text: isLimited ? `Hiệu lực đến ${doc.valid_to}` : "Đang áp dụng",
      tone: isLimited ? "amber" : "green",
    },
    effective: doc.valid_from || "",
    chapters,
  };
}

export async function fetchDocuments(): Promise<ApiDocumentSummary[]> {
  const res = await fetch("/api/documents");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchDocumentDetail(docId: string): Promise<LawDoc> {
  const res = await fetch(`/api/documents/${encodeURIComponent(docId)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return apiDocToLawDoc(await res.json());
}

export function citationBodyMessage(citation: Citation): string {
  const parsed = parseCitationId(citation.id);
  const documentId = parsed.id.split("_D")[0];
  return Object.hasOwn(DOCUMENT_LABELS, documentId) && parsed.label !== parsed.id
    ? "Đã nhận diện văn bản trích dẫn; chưa có nội dung điều khoản trong thư viện hiện tại."
    : "Chưa nhận diện được mã trích dẫn; chưa có nội dung điều khoản trong thư viện hiện tại.";
}

export async function queryLegalApi(
  query: string,
  asOfDate: string,
  pipeline: "P3" | "P2" | "P1",
  useLlm: boolean = false,
  history: Array<{ role: string; content: string }> = []
): Promise<Result> {
  const pipelineMap: Record<string, string> = {
    P3: "P3_FullSystem",
    P2: "P2_TemporalGraphRAG",
    P1: "P1_FlatRAG",
  };

  try {
    const res = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        as_of_date: asOfDate,
        pipeline: pipelineMap[pipeline] || "P3_FullSystem",
        use_llm: useLlm,
        model: "qwen2.5:7b",
        history,
      }),
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => null);
      const detail = typeof errData?.detail === "string" ? errData.detail : "";
      if (res.status >= 500) {
        throw new HttpApiError(
          `Không thể kết nối máy chủ thẩm định pháp lý (HTTP ${res.status}${detail ? `: ${detail}` : ""}).`
        );
      }
      throw new HttpApiError(
        `Yêu cầu không hợp lệ (HTTP ${res.status}${detail ? `: ${detail}` : ""}).`
      );
    }

    const data = await res.json();

    // 1. Out of domain query handling
    if (data.is_out_of_domain) {
      return {
        score: 0,
        answer_status: data.answer_status || "OUT_OF_SCOPE",
        issue: data.irac_sections?.issue || `Kiểm tra phạm vi áp dụng: "${query}"`,
        rule: data.irac_sections?.rule || "Không có quy phạm pháp luật thuế GTGT điều chỉnh nội dung này.",
        citations: [],
        application: [
          data.irac_sections?.application ||
            "Câu hỏi của bạn không thuộc phạm vi điều chỉnh của Hệ thống Pháp lý Thuế Giá trị gia tăng (GTGT).",
        ],
        conclusion:
          data.irac_sections?.conclusion ||
          "Câu hỏi của bạn không thuộc phạm vi điều chỉnh của Hệ thống Pháp lý Thuế Giá trị gia tăng (GTGT). Vui lòng đặt câu hỏi liên quan đến chính sách thuế, thuế suất hoặc quy định hóa đơn chứng từ.",
        audit: [
          {
            tone: "warn",
            text: "Phát hiện câu hỏi nằm ngoài phạm vi nghiệp vụ Pháp lý Thuế GTGT.",
          },
          {
            tone: "ok",
            text: "Màng lọc Domain Guardrail đã kích hoạt để bảo vệ tính chính xác của cơ sở tri thức.",
          },
        ],
        is_out_of_domain: true,
      };
    }

    // 2. Conversational greeting handling
    if (data.is_greeting) {
      return {
        score: 0,
        issue: "Hỏi đáp & Hướng dẫn sử dụng Hệ thống Thẩm định Pháp lý Thuế GTGT",
        rule: "Hướng dẫn sử dụng; chưa thực hiện tra cứu hoặc xác minh căn cứ pháp lý.",
        citations: [],
        application: [
          "Xin chào Quý khách / Quý Doanh nghiệp! Tôi là Trợ lý Pháp lý ảo về Thuế Giá trị gia tăng (GTGT) Việt Nam, được vận hành bởi Đồ thị Tri thức Thời gian (Temporal GraphRAG).",
          "Hệ thống có thể hỗ trợ bạn trực tiếp:",
          "• Thẩm định thuế suất GTGT cho từng trường hợp gia công, xuất khẩu, thương mại dịch vụ.",
          "• Tra cứu tính hiệu lực thời gian của các điều luật tại bất kỳ mốc sự việc nào (tự động phát hiện bẫy niên đại).",
          "• Đối chiếu quy trình xử lý và lập hóa đơn điều chỉnh theo các nghị định mới nhất.",
        ],
        conclusion: "Mời bạn nhập câu hỏi cụ thể về tình huống thuế GTGT hoặc chọn các kịch bản gợi ý bên dưới để bắt đầu thẩm định.",
        audit: [
          { tone: "warn", text: "Phản hồi chào hỏi, chưa tra cứu hoặc xác minh căn cứ pháp lý." },
        ],
        is_greeting: true,
      };
    }

    // 3. Normal in-domain tax query
    const rawCitations: string[] = data.citations || [];
    const seenLabels = new Set<string>();
    const citations = rawCitations
      .map(parseCitationId)
      .filter((c) => {
        if (!c.label || seenLabels.has(c.label)) return false;
        seenLabels.add(c.label);
        return true;
      });

    const appText = data.irac_sections?.application || "";
    const application = appText
      ? appText.split(/\n+/).filter((s: string) => s.trim().length > 0)
      : [];

    const audit: AuditItem[] = [];
    const hasNoCitationWarning = Array.isArray(data.warnings) && data.warnings.some((w: string) =>
      w.toLowerCase().includes("không viện dẫn bất kỳ căn cứ")
    );

    if (Array.isArray(data.warnings) && data.warnings.length > 0) {
      for (const w of data.warnings) {
        audit.push({
          tone: data.risk_score >= 60 ? "bad" : "warn",
          text: w,
        });
      }
    }

    if (citations.length > 0 && !hasNoCitationWarning) {
      audit.push({
        tone: "ok",
        text: `Đã nhận ${citations.length} mã trích dẫn từ API cho mốc ${asOfDate}; việc hiển thị chưa xác minh hiệu lực hoặc nội dung.`,
      });
    } else if (citations.length === 0) {
      audit.push({
        tone: "warn",
        text: `Chưa xác định được điều khoản pháp luật thuế GTGT điều chỉnh trực tiếp tại mốc ${asOfDate}.`,
      });
    }

    let trap: string | undefined = undefined;
    if (data.risk_score >= 60 || data.risk_level === "CRITICAL" || data.risk_level === "HIGH") {
      trap = "API trả về lưu ý cần rà soát; hãy đối chiếu nội dung và hiệu lực căn cứ tại mốc đã chọn.";
    }

    return {
      score: data.risk_score ?? 0,
      answer_status: data.answer_status || "ANSWERED",
      issue: data.irac_sections?.issue || `Xác định quy định pháp lý cho: "${query}"`,
      rule: data.irac_sections?.rule || "Chưa xác định được điều khoản viện dẫn cụ thể.",
      citations,
      application: application.length > 0 ? application : [appText || "Không có nội dung phân tích chi tiết từ mô hình."],
      conclusion:
        data.irac_sections?.conclusion ||
        "Hệ thống đã thẩm định nhưng chưa tổng hợp được kết luận theo cấu trúc IRAC. Vui lòng thử lại hoặc tắt chế độ LLM.",
      audit,
      trap,
    };
  } catch (err) {
    if (err instanceof HttpApiError) {
      throw err;
    }
    throw new Error("Không thể kết nối máy chủ thẩm định pháp lý (Backend offline).");
  }
}
