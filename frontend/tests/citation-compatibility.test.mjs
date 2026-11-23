import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import esbuild from "esbuild";

const dirname = path.dirname(fileURLToPath(import.meta.url));
async function load(relative) {
  const bundle = await esbuild.build({
    entryPoints: [path.resolve(dirname, relative)], bundle: true, write: false,
    format: "esm", platform: "node", target: "node18", packages: "external",
  });
  return import("data:text/javascript;base64," + Buffer.from(bundle.outputFiles[0].text).toString("base64"));
}
const { parseCitationId, DOCUMENT_LABELS, findCitationBody, queryLegalApi, citationBodyMessage, apiDocToLawDoc } = await load("../src/app/api.ts");
const { DOCS, resolve } = await load("../src/app/data.ts");
const appSource = fs.readFileSync(path.resolve(dirname, "../src/app/App.tsx"), "utf8");
const inventory = JSON.parse(fs.readFileSync(path.resolve(dirname, "../../data/source_inventory.json"), "utf8"));

test("Step8.3 greeting does not claim verified knowledge or connectivity counts", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ is_greeting: true }) });
  try {
    const r = await queryLegalApi("xin chào", "2026-11-06", "P3");
    assert.equal(r.is_greeting, true);
    assert.deepEqual(r.citations, []);
    assert.doesNotMatch(JSON.stringify(r), /cơ sở tri thức hiệu lực|48\/2024|181\/2025|đã xác minh|đã kiểm chứng|kết nối.*\d|thẩm định pháp lý qua Đồ thị/i);
    assert.match(JSON.stringify(r), /chưa.*xác minh/i);
  } finally { globalThis.fetch = original; }
});

test("Step8.3 audit reports received IDs without asserting validity and preserves point uniqueness", async () => {
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, body: JSON.parse(options.body) });
    return { ok: true, json: async () => ({ citations: ["luat_48_2024_d9_k1_A", "LUAT_48_2024_D9_K1_a", "LUAT_48_2024_D9_K1_b", "BOGUS_D9"], risk_score: 70, warnings: ["Cần đối chiếu nguồn"], irac_sections: {} }) };
  };
  try {
    const r = await queryLegalApi("thuế", "2026-11-06", "P3");
    assert.equal(requests[0].url, "/api/query");
    assert.equal(requests[0].body.as_of_date, "2026-11-06");
    assert.deepEqual(r.citations.map(c => c.id), ["LUAT_48_2024_D9_K1_a", "LUAT_48_2024_D9_K1_b", "BOGUS_D9"]);
    assert.match(r.audit.map(a => a.text).join(" "), /Đã nhận 3 mã trích dẫn/);
    assert.match(r.audit.map(a => a.text).join(" "), /chưa.*xác minh/i);
    assert.ok(r.audit.some(a => a.text === "Cần đối chiếu nguồn"));
    assert.doesNotMatch(r.trap, /rủi ro pháp lý|nguy cơ/i);
  } finally { globalThis.fetch = original; }
});

test("Step8.3 missing-body copy distinguishes unknown from identified, including old persisted citations", () => {
  assert.match(citationBodyMessage(parseCitationId("ND_68_2026_D3")), /Đã nhận diện/);
  assert.match(citationBodyMessage(parseCitationId("LUAT_48_2024_D99")), /chưa có nội dung điều khoản/);
  assert.match(citationBodyMessage(parseCitationId("BOGUS_D9")), /Chưa nhận diện/);
  assert.match(citationBodyMessage(parseCitationId("TT_89_2026_PL1")), /Chưa nhận diện/);
  assert.match(citationBodyMessage({ label: "Cũ", doc: "l48", art: "l48-d9" }), /Chưa nhận diện/);
});

test("Step8.3 drawer navigation requires exact body and makes no blanket verified claims", () => {
  assert.doesNotMatch(appSource, /match \? match.document :/);
  assert.match(appSource, /\{match \? \(/);
  assert.match(appSource, /citationBodyMessage\(c\)/);
  assert.doesNotMatch(appSource, /AI và Đồ thị tri thức đối chiếu trực tiếp/);
  assert.match(appSource, /bản trích demo/);
  assert.doesNotMatch(appSource, /setTimeout\(\(\) => \{\s*const el = document.getElementById\(`lib-art-/);
});

test("Step8.3 trace is simulated, generic and never reconstructs canonical IDs", () => {
  assert.match(appSource, /MATCH \(citation\)/);
  assert.doesNotMatch(appSource, /art:Article|c\.art\.toUpperCase/);
  assert.match(appSource, /parseCitationId\(c.id\)/);
  assert.match(appSource, /chưa có ID chuẩn/);
  assert.match(appSource, /không phải kết quả xác minh/);
  assert.doesNotMatch(appSource, /Cảnh báo rủi ro|Kiểm định căn cứ pháp lý đồ thị/);
});

test("Step8.2 registry matches all 33 inventory IDs and exact codes", () => {
  assert.equal(inventory.documents.length, 33);
  assert.deepEqual(Object.keys(DOCUMENT_LABELS).sort(), inventory.documents.map(d => d.id).sort());
  for (const d of inventory.documents) assert.equal(DOCUMENT_LABELS[d.id].code, d.code, d.id);
});

test("Step8.2 all five legacy anchors retain exact article bodies", () => {
  for (const [id, doc, article] of [
    ["LUAT_48_2024", "l48", 9], ["ND_181_2025", "nd181", 5],
    ["TT_69_2025", "tt69", 3], ["ND_359_2025", "nd359", 1], ["ND_144_2026", "nd144", 1],
  ]) {
    const c = parseCitationId(`${id}_D${article}_K1_a`);
    assert.equal(c.doc, doc);
    assert.equal(c.art, `${doc}-d${article}`);
    assert.equal(findCitationBody(c, DOCS)?.article.id, c.art);
  }
});

test("Step8.2 document-only IDs are readable without article anchors", () => {
  for (const [id, meta] of Object.entries(DOCUMENT_LABELS)) {
    assert.deepEqual(parseCitationId(id), { id, label: meta.label, doc: meta.libraryDocId, art: null });
  }
});

test("Step8.2 lowercase inputs normalize to canonical document and point IDs", () => {
  assert.equal(parseCitationId("luat_48_2024").id, "LUAT_48_2024");
  assert.equal(parseCitationId("luat_48_2024_d9_k1_A").id, "LUAT_48_2024_D9_K1_a");
  assert.equal(parseCitationId("luat_48_2024_d9_k1_A").art, "l48-d9");
});

test("Step8.2 malformed and unsupported appendix IDs remain opaque", () => {
  for (const id of ["LUAT_48_2024_D9_a", "LUAT_48_2024_D9_K1_a_junk", "LUAT_48_2024_D9!junk", "LUAT_48_2024_D9_K", "LUAT_48_2024_D9_K1_abc", "TT_89_2026_PL1", "BOGUS_D9", "toString", "__proto__", ""]) {
    assert.deepEqual(parseCitationId(id), { id, label: id, doc: null, art: null }, id);
  }
});

test("Step8.2 points have unique canonical IDs and labels", () => {
  const points = ["a", "b", "đ"].map(p => parseCitationId(`LUAT_48_2024_D9_K1_${p}`));
  assert.equal(new Set(points.map(c => c.id)).size, 3);
  assert.equal(new Set(points.map(c => c.label)).size, 3);
  assert.match(points[2].label, /Điểm đ Khoản 1/);
});

test("Step8.2 body lookup rejects absent articles, absent documents and document-only citations", () => {
  const before = JSON.stringify(DOCS);
  for (const id of ["LUAT_48_2024_D99", "ND_68_2026_D3_K1", "BOGUS_D9", "LUAT_48_2024"]) {
    assert.equal(findCitationBody(parseCitationId(id), DOCS), null);
  }
  assert.equal(findCitationBody(parseCitationId("LUAT_48_2024_D9"), []), null);
  assert.equal(JSON.stringify(DOCS), before);
});

test("Step8.2 in-file demo citations carry canonical IDs", () => {
  for (const [q, date] of [["xuất khẩu", "2026-03-20"], ["", "2025-06-15"], ["hóa đơn", "2026-05-10"]]) {
    for (const c of resolve(q, date, "P3").citations) {
      assert.equal(typeof c.id, "string");
      assert.deepEqual(parseCitationId(c.id).art, c.art);
      assert.ok(DOCUMENT_LABELS[c.id.split("_D")[0]]);
    }
  }
});

test("Task6 live document mapping, answer_status, and HTTP error messages work cleanly", async () => {
  const mapped = apiDocToLawDoc({
    id: "ND_68_2026",
    code: "68/2026/NĐ-CP",
    title: "Nghị định về hộ kinh doanh",
    valid_from: "2026-03-05",
    valid_to: "9999-12-31",
    status: "ACTIVE",
    chapters: [],
    articles: [
      {
        id: "ND_68_2026_D12",
        code: "Dieu 12",
        title: "Xử lý số thuế nộp thừa",
        clauses: [
          {
            id: "ND_68_2026_D12_K1",
            code: "Khoan 1",
            content: "Hộ kinh doanh được xử lý số thuế nộp thừa.",
            points: [{ id: "ND_68_2026_D12_K1_a", code: "Diem a", content: "Bù trừ hoặc hoàn trả." }],
          },
        ],
      },
    ],
  });
  assert.equal(mapped.id, "ND_68_2026");
  assert.equal(mapped.badge.tone, "green");
  assert.equal(mapped.chapters[0].articles[0].clauses[0].points?.[0].l, "a");

  const hit = findCitationBody(parseCitationId("ND_68_2026_D12_K1"), [mapped]);
  assert.equal(hit?.article.id, "ND_68_2026_D12");
  assert.equal(findCitationBody(parseCitationId("ND_68_2026"), [mapped]), null);

  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => ({
      ok: false,
      status: 400,
      json: async () => ({ detail: "Staging supports P3 only" }),
    });
    await assert.rejects(() => queryLegalApi("thuế", "2026-11-06", "P1"), /HTTP 400: Staging supports P3 only/);

    globalThis.fetch = async () => ({
      ok: false,
      status: 503,
      json: async () => ({ detail: "Neo4j timeout" }),
    });
    await assert.rejects(() => queryLegalApi("thuế", "2026-11-06", "P3"), /Không thể kết nối.*HTTP 503: Neo4j timeout/);

    globalThis.fetch = async () => ({
      ok: true,
      json: async () => ({
        citations: ["ND_68_2026_D12_K1"],
        risk_score: 65,
        risk_level: "HIGH",
        answer_status: "INSUFFICIENT_BASIS",
        warnings: [],
        irac_sections: { issue: "I", rule: "R", application: "A", conclusion: "C" },
      }),
    });
    const res = await queryLegalApi("thuế", "2026-11-06", "P3");
    assert.equal(res.answer_status, "INSUFFICIENT_BASIS");
  } finally {
    globalThis.fetch = original;
  }
});
