import { type Result, type Citation, type AuditItem } from "./data";

export function parseCitationId(cid: string): Citation {
  let doc = "l48";
  let label = cid;
  let art = cid.toLowerCase().replace(/_/g, "-");

  const match = cid.match(/^(LUAT_48_2024|ND_181_2025|TT_69_2025|ND_359_2025|ND_144_2026)_D(\d+)(?:_K(\d+))?(?:_([a-z0-9]+))?/i);
  if (match) {
    const docKey = match[1].toUpperCase();
    const articleNum = match[2];
    const clauseNum = match[3];
    const pointLetter = match[4];

    const prefix = pointLetter
      ? `Điểm ${pointLetter} Khoản ${clauseNum} `
      : clauseNum
      ? `Khoản ${clauseNum} `
      : "";

    const docLabels: Record<string, [string, string]> = {
      LUAT_48_2024: ["l48", "Luật 48/2024"],
      ND_181_2025: ["nd181", "NĐ 181/2025"],
      TT_69_2025: ["tt69", "TT 69/2025"],
      ND_359_2025: ["nd359", "NĐ 359/2025"],
      ND_144_2026: ["nd144", "NĐ 144/2026"],
    };

    if (docLabels[docKey]) {
      const [docId, docName] = docLabels[docKey];
      doc = docId;
      label = `${prefix}Điều ${articleNum} ${docName}`;
      art = `${docId}-d${articleNum}`;
    }
  }

  return { label, doc, art };
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
      throw new Error("Không thể kết nối máy chủ thẩm định pháp lý (Backend offline).");
    }

    const data = await res.json();

    // 1. Out of domain query handling
    if (data.is_out_of_domain) {
      return {
        score: 0,
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
        rule: "Cơ sở tri thức hiệu lực: Luật Thuế GTGT 48/2024/QH15, Nghị định 181/2025, Thông tư 69/2025, Nghị định 359/2025, Nghị định 144/2026.",
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
          { tone: "ok", text: "Hệ thống đã kết nối trực tiếp với Neo4j Knowledge Graph và FastAPI Backend." },
          { tone: "ok", text: "Trạng thái đồ thị: 5 văn bản pháp luật, 76 điều luật, 271 khoản sẵn sàng tra cứu." },
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
        text: `Đã đối chiếu ${citations.length} điều khoản viện dẫn qua Đồ thị Tri thức Neo4j tại mốc ${asOfDate}.`,
      });
    } else if (citations.length === 0) {
      audit.push({
        tone: "warn",
        text: `Chưa xác định được điều khoản pháp luật thuế GTGT điều chỉnh trực tiếp tại mốc ${asOfDate}.`,
      });
    }

    let trap: string | undefined = undefined;
    if (data.risk_score >= 60 || data.risk_level === "CRITICAL" || data.risk_level === "HIGH") {
      trap = "CẢNH BÁO RỦI RO PHÁP LÝ: CĂN CỨ TRÍCH DẪN CÓ NGUY CƠ HẾT HIỆU LỰC HOẶC CHƯA PHÁT SINH HIỆU LỰC TẠI MỐC ĐÃ CHỌN!";
    }

    return {
      score: data.risk_score ?? 0,
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
    throw new Error("Không thể kết nối máy chủ thẩm định pháp lý (Backend offline).");
  }
}
