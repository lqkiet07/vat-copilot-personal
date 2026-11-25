import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import esbuild from "esbuild";

const dirname = path.dirname(fileURLToPath(import.meta.url));
async function load(relative) {
  const bundle = await esbuild.build({
    entryPoints: [path.resolve(dirname, relative)],
    bundle: true,
    write: false,
    format: "esm",
    platform: "node",
    target: "node18",
    packages: "external",
  });
  return import("data:text/javascript;base64," + Buffer.from(bundle.outputFiles[0].text).toString("base64"));
}

const { apiDocToLawDoc, mapApiQueryResponseToResult, parseCitationId } = await load("../src/app/api.ts");
const {
  parseCitationTarget,
  doesClauseMatchCitation,
  doesPointMatchCitation,
  collectDocCitationMatches,
  getPreviewClausesForCitation,
  getDocCategory,
  splitTechnicalDiagnosticLines,
  formatInlineCitationsToMarkdownLinks,
  formatInlineCitationsPlainText,
  formatTechnicalDiagnosticText,
  extractProviderNotice,
  highlightOperativeSpans,
  stripLeadingClauseNumber,
} = await load("../src/app/utils/citationMatch.ts");

const inventory = JSON.parse(
  fs.readFileSync(path.resolve(dirname, "../../data/source_inventory.json"), "utf8")
);

test("mapApiArticle preserves IDs, lettered clause labels, and separates d vs d_1 points", () => {
  const doc = apiDocToLawDoc({
    id: "ND_181_2025",
    code: "181/2025/NĐ-CP",
    title: "Nghị định quy định chi tiết Luật Thuế GTGT",
    valid_from: "2025-07-01",
    valid_to: "9999-12-31",
    status: "ACTIVE",
    chapters: [],
    articles: [
      {
        id: "ND_181_2025_D4",
        code: "Dieu 4",
        title: "Đối tượng không chịu thuế",
        clauses: [
          { id: "ND_181_2025_D4_K1", code: "Khoan 1", content: "Khoản 1" },
          { id: "ND_181_2025_D4_K1b", code: "Khoan 1b", content: "Khoản 1b" },
          {
            id: "ND_181_2025_D4_K4",
            code: "Khoan 4",
            content: "Khoản 4",
            points: [
              { id: "ND_181_2025_D4_K4_d", code: "Diem d", content: "Nội dung điểm d" },
              { id: "ND_181_2025_D4_K4_d_1", code: "Diem d", content: "Nội dung điểm đ" },
            ],
          },
        ],
      },
    ],
  });

  const d4 = doc.chapters[0].articles[0];
  assert.equal(d4.clauses[1].id, "ND_181_2025_D4_K1b");
  assert.equal(d4.clauses[1].label, "1b");

  const k4Points = d4.clauses[2].points;
  assert.equal(k4Points.length, 2);
  assert.equal(k4Points[0].id, "ND_181_2025_D4_K4_d");
  assert.equal(k4Points[0].l, "d");
  assert.equal(k4Points[1].id, "ND_181_2025_D4_K4_d_1");
  assert.equal(k4Points[1].l, "đ");
});

test("Provision matching highlights exact clause/point and preview returns cited clause instead of first two", () => {
  const doc = apiDocToLawDoc({
    id: "ND_181_2025",
    code: "181/2025/NĐ-CP",
    title: "Nghị định 181",
    valid_from: "2025-07-01",
    valid_to: "9999-12-31",
    status: "ACTIVE",
    chapters: [],
    articles: [
      {
        id: "ND_181_2025_D8",
        code: "Dieu 8",
        title: "Giá tính thuế đối với bất động sản",
        clauses: [
          { id: "ND_181_2025_D8_K1", code: "Khoan 1", content: "Nội dung khoản 1" },
          { id: "ND_181_2025_D8_K2", code: "Khoan 2", content: "Nội dung khoản 2" },
          { id: "ND_181_2025_D8_K7", code: "Khoan 7", content: "Nội dung khoản 7 về giá đất được trừ" },
        ],
      },
      {
        id: "ND_181_2025_D4",
        code: "Dieu 4",
        title: "Đối tượng không chịu thuế",
        clauses: [
          {
            id: "ND_181_2025_D4_K4",
            code: "Khoan 4",
            content: "Chuyển quyền sử dụng đất",
            points: [
              { id: "ND_181_2025_D4_K4_d", code: "Diem d", content: "Điểm d" },
              { id: "ND_181_2025_D4_K4_d_1", code: "Diem d", content: "Điểm đ" },
            ],
          },
        ],
      },
    ],
  });

  const citK7 = parseCitationId("ND_181_2025_D8_K7");
  const d8 = doc.chapters[0].articles[0];
  const preview = getPreviewClausesForCitation(d8, citK7);
  assert.equal(preview.isExactClauseMatch, true);
  assert.equal(preview.clauses.length, 1);
  assert.equal(preview.clauses[0].id, "ND_181_2025_D8_K7");
  assert.equal(preview.omittedCount, 2);

  const citPointDWithStroke = parseCitationId("ND_181_2025_D4_K4_d_1");
  assert.match(citPointDWithStroke.label, /Điểm đ Khoản 4 Điều 4/);
  assert.equal(parseCitationTarget(citPointDWithStroke).level, "point");

  const d4 = doc.chapters[0].articles[1];
  const k4 = d4.clauses[0];
  assert.equal(doesClauseMatchCitation(k4, d4, citPointDWithStroke), true);
  assert.equal(doesPointMatchCitation(k4.points[0], k4, d4, citPointDWithStroke), false);
  assert.equal(doesPointMatchCitation(k4.points[1], k4, d4, citPointDWithStroke), true);

  const matches = collectDocCitationMatches(doc, [citK7, citPointDWithStroke]);
  assert.equal(matches.length, 2);
  assert.equal(matches[0].clauseId, "ND_181_2025_D8_K7");
  assert.equal(matches[1].pointId, "ND_181_2025_D4_K4_d_1");
});

test("Library categories cover all 33 staging documents including Khác (NQ, QĐ, VBHN)", () => {
  const counts = { luat: 0, nghidinh: 0, thongtu: 0, other: 0 };
  for (const d of inventory.documents) {
    counts[getDocCategory(d.id)] += 1;
  }
  assert.equal(counts.luat + counts.nghidinh + counts.thongtu + counts.other, 33);
  assert.ok(counts.other > 0);
});

test("splitTechnicalDiagnosticLines separates raw container diagnostics from main conclusion", () => {
  const sample =
    "Chưa đủ căn cứ để kết luận pháp lý cho tình huống.\n" +
    "Hệ thống chưa xác minh được chứng cứ ND 181 2025 D4: Container is not citable; changed or blocked descendants: ND_181_2025_D4_K4\n" +
    "Hệ thống chưa xác minh được chứng cứ VBHN 25 2026 D3 K1: Consolidation reference is not operative evidence\n" +
    "LUAT 48 2024 D5 K7: ngày bắt đầu hiệu lực theo dữ liệu là 2025-07-01; ngày tra cứu 2025-06-01 nằm trước mốc này.";
  const split = splitTechnicalDiagnosticLines(sample);
  assert.match(split.primaryText, /^Chưa đủ căn cứ để kết luận pháp lý cho tình huống\./);
  assert.match(split.primaryText, /Khoản 7 Điều 5 Luật 48\/2024: ngày bắt đầu hiệu lực theo dữ liệu là 01\/07\/2025/);
  assert.equal(split.diagnosticLines.length, 2);
});

test("Inline citation formatters convert known Neo4j tags and keep unknown or markdown links intact", () => {
  const prose =
    "Theo [LUAT_48_2024_D5_K7] và [ND_181_2025_D4_K4_d_1], tham khảo [Tài liệu](https://example.com), bỏ qua [BOGUS_D9] và [Khuyên dùng].";
  const linked = formatInlineCitationsToMarkdownLinks(prose);
  assert.match(linked, /\[Khoản 7 Điều 5 Luật 48\/2024\]\(#cite:LUAT_48_2024_D5_K7\)/);
  assert.match(linked, /\[Điểm đ Khoản 4 Điều 4 Nghị định 181\/2025\/NĐ-CP\]\(#cite:ND_181_2025_D4_K4_d_1\)/);
  assert.match(linked, /\[Tài liệu\]\(https:\/\/example\.com\)/);
  assert.match(linked, /\[BOGUS_D9\]/);
  assert.match(linked, /\[Khuyên dùng\]/);

  const ruleLine = "[TT_32_2026_D3_K1] [ND_181_2025_D8_K7] [LUAT_48_2024]";
  assert.equal(
    formatInlineCitationsPlainText(ruleLine),
    "Khoản 1 Điều 3 Thông tư 32/2026/TT-BTC; Khoản 7 Điều 8 Nghị định 181/2025/NĐ-CP; Luật 48/2024/QH15"
  );
  assert.equal(
    formatInlineCitationsPlainText("Kết quả kiểm thử"),
    "Kết quả kiểm thử"
  );
});

test("formatTechnicalDiagnosticText translates English diagnostics and replaces canonical and spaced DB IDs", () => {
  const amended = formatTechnicalDiagnosticText("Điều khoản ND_68_2026_D11_K3 đã bị sửa đổi/bổ sung bởi văn bản sửa đổi.");
  assert.match(amended, /Khoản 3 Điều 11 Nghị định 68\/2026\/NĐ-CP/);
  assert.doesNotMatch(amended, /ND_68_2026_D11_K3/);

  const container = formatTechnicalDiagnosticText(
    "Hệ thống chưa xác minh được chứng cứ LUAT_48_2024_D5: Container is not citable; changed or blocked descendants: LUAT_48_2024_D5_K1, LUAT_48_2024_D5_K25"
  );
  assert.match(container, /Điều 5 Luật 48\/2024/);
  assert.match(container, /Khoản 1 Điều 5 Luật 48\/2024/);
  assert.match(container, /Khoản 25 Điều 5 Luật 48\/2024/);
  assert.match(container, /không trích dẫn gộp/i);
  assert.doesNotMatch(container, /Container is not citable/i);
  assert.doesNotMatch(container, /LUAT_48_2024/);

  const spaced = formatTechnicalDiagnosticText(
    "Hệ thống chưa xác minh được chứng cứ ND 181 2025 D4: Consolidation reference is not operative evidence"
  );
  assert.match(spaced, /Điều 4 Nghị định 181\/2025\/NĐ-CP/);
  assert.match(spaced, /Văn bản hợp nhất chỉ mang tính tham khảo/i);
  assert.doesNotMatch(spaced, /ND 181 2025 D4/);
});

test("mapApiQueryResponseToResult preserves missing_facts, rewritten_query, and amended_citation_ids", () => {
  const mapped = mapApiQueryResponseToResult(
    {
      citations: ["ND_68_2026_D3_K1"],
      risk_score: 12,
      risk_level: "LOW",
      warnings: [],
      irac_sections: {
        issue: "Vấn đề",
        rule: "[ND_68_2026_D3_K1]",
        application: "Phân tích",
        conclusion: "Kết luận",
      },
      answer_status: "ANSWERED",
      missing_facts: ["Doanh thu hằng năm?"],
      rewritten_query: "Hộ kinh doanh dịch vụ internet",
      amended_citation_ids: ["ND_68_2026_D3_K1"],
    },
    "tiệm net",
    "2026-03-20"
  );
  assert.deepEqual(mapped.missing_facts, ["Doanh thu hằng năm?"]);
  assert.equal(mapped.rewritten_query, "Hộ kinh doanh dịch vụ internet");
  assert.deepEqual(mapped.amended_citation_ids, ["ND_68_2026_D3_K1"]);
});

test("extractProviderNotice detects LLM_PROVIDER_UNAVAILABLE audit items cleanly", () => {
  assert.equal(extractProviderNotice([{ text: "Đã kiểm tra xong" }]), null);
  const notice = extractProviderNotice([
    { text: "LLM_PROVIDER_UNAVAILABLE: Mô hình ngôn ngữ tạm thời không phản hồi" },
  ]);
  assert.match(notice || "", /Mô hình AI tạm thời không phản hồi/i);
});

test("mapApiQueryResponseToResult maps resolved_provisions operative text and amending links", () => {
  const mapped = mapApiQueryResponseToResult(
    {
      citations: ["ND_68_2026_D3_K1"],
      risk_score: 0,
      risk_level: "LOW",
      warnings: [],
      irac_sections: { issue: "I", rule: "R", application: "A", conclusion: "C" },
      resolved_provisions: {
        ND_68_2026_D3_K1: {
          text: "1. Doanh thu năm từ 01 tỷ đồng trở xuống không chịu thuế GTGT.",
          status: "AMENDED",
          amended_by: [
            { source_unit: "ND_141_2026_D1_K1", effective_on: "2026-01-01", action: "phrase_replace" },
          ],
        },
      },
    },
    "quán net",
    "2026-03-20"
  );
  assert.equal(mapped.resolvedProvisions?.ND_68_2026_D3_K1?.status, "AMENDED");
  assert.equal(mapped.resolvedProvisions?.ND_68_2026_D3_K1?.amended_by?.[0]?.source_unit, "ND_141_2026_D1_K1");
});

test("highlightOperativeSpans preserves original clause text 100% and highlights whole key phrases", () => {
  const clause =
    "Hộ kinh doanh, cá nhân kinh doanh có hoạt động sản xuất, kinh doanh có mức doanh thu năm từ 01 tỷ đồng trở xuống thuộc đối tượng không chịu thuế giá trị gia tăng.";
  const context =
    "quán net có phải đóng thuế kh\nNếu doanh thu từ 01 tỷ đồng trở xuống thì không phải nộp và thuộc đối tượng không chịu thuế giá trị gia tăng.";
  const segments = highlightOperativeSpans(clause, context);
  assert.equal(segments.map((s) => s.text).join(""), clause);
  const highlighted = segments.filter((s) => s.highlight).map((s) => s.text).join(" | ");
  assert.match(highlighted, /01 tỷ đồng/);
  assert.match(highlighted, /không chịu thuế giá trị gia tăng/i);
  assert.doesNotMatch(highlighted, /tượng không chịu/i);
});

test("highlightOperativeSpans does not emit false highlights when context is empty or stopwords only", () => {
  const clause = "Thuế suất 0% áp dụng đối với hàng hóa, dịch vụ xuất khẩu.";
  assert.deepEqual(highlightOperativeSpans(clause, ""), [{ text: clause, highlight: false }]);
  assert.deepEqual(highlightOperativeSpans(clause, "của và cho theo quy định tại"), [
    { text: clause, highlight: false },
  ]);
});

test("stripLeadingClauseNumber removes matching clause prefix without mangling body text", () => {
  assert.equal(stripLeadingClauseNumber("1. Hộ kinh doanh có doanh thu.", "1"), "Hộ kinh doanh có doanh thu.");
  assert.equal(stripLeadingClauseNumber("a) Hàng hóa xuất khẩu.", "a"), "Hàng hóa xuất khẩu.");
  assert.equal(stripLeadingClauseNumber("10. Trường hợp khác.", "1"), "10. Trường hợp khác.");
});

test("formatInlineCitationsToMarkdownLinks uses compact numbered markers when indexed and keeps 1-arg compatibility", () => {
  const indexMap = new Map([
    ["ND_68_2026_D3_K1", 1],
    ["ND_68_2026_D3_K2", 2],
  ]);
  const text =
    "- Dưới 01 tỷ đồng [ND_68_2026_D3_K1] , [ND_68_2026_D3_K2] .\n" +
    "- Tham khảo thêm [LUAT_48_2024_D5] .";

  const indexed = formatInlineCitationsToMarkdownLinks(text, indexMap);
  assert.equal(
    indexed,
    "- Dưới 01 tỷ đồng [1](#cite:ND_68_2026_D3_K1)[2](#cite:ND_68_2026_D3_K2).\n" +
      "- Tham khảo thêm [Điều 5 Luật 48/2024](#cite:LUAT_48_2024_D5)."
  );

  // Single-argument behavior remains full-label Markdown link unchanged.
  const unindexed = formatInlineCitationsToMarkdownLinks("Theo [ND_68_2026_D3_K1].");
  assert.equal(unindexed, "Theo [Khoản 1 Điều 3 Nghị định 68/2026/NĐ-CP](#cite:ND_68_2026_D3_K1).");
});

test("formatInlineCitationsToMarkdownLinks and PlainText expand comma/semicolon-separated canonical IDs", () => {
  const indexMap = new Map([
    ["ND_68_2026_D3_K2", 1],
    ["ND_68_2026_D10_K1_a", 2],
    ["LUAT_108_2025_D13_K1_b", 3],
  ]);
  const grouped = "Nộp thuế [ND_68_2026_D3_K2, ND_68_2026_D10_K1_a; LUAT_108_2025_D13_K1_b].";
  assert.equal(
    formatInlineCitationsToMarkdownLinks(grouped, indexMap),
    "Nộp thuế [1](#cite:ND_68_2026_D3_K2)[2](#cite:ND_68_2026_D10_K1_a)[3](#cite:LUAT_108_2025_D13_K1_b)."
  );
  assert.match(
    formatInlineCitationsPlainText(grouped),
    /Khoản 2 Điều 3 Nghị định 68\/2026\/NĐ-CP; Điểm a Khoản 1 Điều 10 Nghị định 68\/2026\/NĐ-CP; Điểm b Khoản 1 Điều 13 Luật 108\/2025\/QH15/
  );
  assert.equal(
    formatInlineCitationsToMarkdownLinks("Giữ nguyên [BOGUS_D9, BOGUS_D10] và [Khuyên dùng]."),
    "Giữ nguyên [BOGUS_D9, BOGUS_D10] và [Khuyên dùng]."
  );
});


