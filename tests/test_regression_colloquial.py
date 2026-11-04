import pytest
from src.ui.demo_service import execute_demo_query


def test_colloquial_query_normalization_pure():
    from src.api.chat_domain import normalize_query
    q = "tôi mở tiệm net dưới quê thì có phải đóng thuế kh và đóng bao nhiêu tiền"
    normalized = normalize_query(q)
    assert "thuế không" in normalized
    assert " đóng bao nhiêu tiền" in normalized


@pytest.mark.requires_neo4j
def test_tiem_net_regression_p3_verifiable_outcome():
    q = "tôi mở tiệm net dưới quê thì có phải đóng thuế kh và đóng bao nhiêu tiền"
    res = execute_demo_query(
        query=q,
        as_of_date="2026-03-20",
        pipeline="P3_FullSystem",
        use_llm=False,
    )
    citations = res.get("citations", [])
    # 1. No duplicates
    assert len(citations) == len(set(citations)), f"Duplicates found in citations: {citations}"
    # 2. No irrelevant ND_144 insurance agent commission
    assert not any("ND_144" in cid for cid in citations), f"Found irrelevant citation in {citations}"
    # 2b. No export rate provisions (D9)
    assert not any("D9" in cid for cid in citations), f"Found export citation in {citations}"
    # 3. Core legal provisions included
    assert any("LUAT_48_2024_D4" in c for c in citations), f"Missing D4 in {citations}"
    assert any("LUAT_48_2024_D5" in c for c in citations), f"Missing D5 in {citations}"

    irac = res.get("irac_sections", {})
    conc = irac.get("conclusion", "")
    app = irac.get("application", "")
    rule = irac.get("rule", "")
    combined_text = f"{conc} {app}".lower()

    # Must NOT contain emojis
    assert "👉" not in conc, "Conclusion must not contain hand emoji"
    # Must NOT claim 0% export rate or export services
    assert "thuế suất 0%" not in conc, "Conclusion must not claim 0% export rate for domestic household"
    assert "xuất khẩu" not in conc, "Conclusion must not refer to export services for domestic household"
    # Rule must NOT duplicate 'Căn cứ pháp lý viện dẫn:' since UI already prefixes 'Căn cứ:'
    assert not rule.startswith("Căn cứ pháp lý viện dẫn:"), "Rule must not have redundant prefix"
    # Legal accuracy
    assert "không phải đóng thuế gtgt" not in combined_text
    assert "200 triệu" in combined_text
    assert "không chịu thuế gtgt" in combined_text


def test_household_query_deterministic_fallback_no_export_leakage():
    from src.ui.demo_service import _build_irac_sections
    q = "tôi mở tiệm net dưới quê thì có phải đóng thuế kh"
    irac = _build_irac_sections(q, "2026-03-20", ["LUAT_48_2024_D5", "LUAT_48_2024_D9"], [])
    conc = irac.get("conclusion", "")
    assert "thuế suất 0%" not in conc, "Conclusion must not claim 0% export rate for domestic household"
    assert "xuất khẩu" not in conc, "Conclusion must not refer to export services for domestic household"
    assert "200 triệu" in conc, "Conclusion must default to household 200M threshold"

