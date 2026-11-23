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

const { apiDocToLawDoc, parseCitationId } = await load("../src/app/api.ts");
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
  assert.equal(split.primaryText, "Chưa đủ căn cứ để kết luận pháp lý cho tình huống.");
  assert.equal(split.diagnosticLines.length, 3);
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

