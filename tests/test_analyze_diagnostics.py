import json
from pathlib import Path
import pytest
from scripts.analyze_diagnostics import (
    calculate_false_negatives,
    format_report,
    main,
    summarize,
)


def test_summarize_counts_reasons(tmp_path: Path):
    diag_file = tmp_path / "diag.jsonl"
    records = [
        {"query": "q1", "empty_reason": "RETRIEVAL_MISS"},
        {"query": "q2", "empty_reason": "GATING_OVERREJECT"},
        {"query": "q3", "empty_reason": "RETRIEVAL_MISS"},
        {"query": "q4", "empty_reason": "ANSWERED"},
        {"query": "q5", "empty_reason": "LLM_REFUSED_WITH_EVIDENCE"},
    ]
    diag_file.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    counts = summarize(diag_file)
    assert counts == {
        "RETRIEVAL_MISS": 2,
        "GATING_OVERREJECT": 1,
        "ANSWERED": 1,
        "LLM_REFUSED_WITH_EVIDENCE": 1,
    }


def test_summarize_handles_empty_or_malformed_lines(tmp_path: Path):
    diag_file = tmp_path / "diag.jsonl"
    content = (
        '{"query": "q1", "empty_reason": "RETRIEVAL_MISS"}\n'
        '\n'
        '   \n'
        '{malformed json\n'
        '{"query": "q2", "empty_reason": "IRAC_PARSE_FAILED"}\n'
        'not a json\n'
    )
    diag_file.write_text(content, encoding="utf-8")

    counts = summarize(diag_file)
    assert counts == {
        "RETRIEVAL_MISS": 1,
        "IRAC_PARSE_FAILED": 1,
    }


def test_calculate_false_negatives():
    counts = {
        "RETRIEVAL_MISS": 5,
        "GATING_OVERREJECT": 3,
        "LLM_REFUSED_WITH_EVIDENCE": 2,
        "IRAC_PARSE_FAILED": 4,
        "ANSWERED": 10,
        "OUT_OF_SCOPE": 1,
    }
    assert calculate_false_negatives(counts) == 9
    assert calculate_false_negatives({}) == 0


def test_format_report():
    counts = {
        "RETRIEVAL_MISS": 2,
        "GATING_OVERREJECT": 1,
        "ANSWERED": 1,
    }
    report = format_report(counts)
    assert "Reason" in report
    assert "RETRIEVAL_MISS" in report
    assert "GATING_OVERREJECT" in report
    assert "ANSWERED" in report
    assert "False Negative Total: 1" in report


def test_main_file_not_found(capsys, monkeypatch, tmp_path):
    non_existent = tmp_path / "missing.jsonl"
    monkeypatch.setattr("sys.argv", ["analyze_diagnostics.py", str(non_existent)])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "warning" in captured.out.lower() or "not found" in captured.out.lower()


def test_main_success(capsys, monkeypatch, tmp_path):
    diag_file = tmp_path / "diag.jsonl"
    diag_file.write_text('{"query": "q1", "empty_reason": "GATING_OVERREJECT"}\n', encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["analyze_diagnostics.py", str(diag_file)])
    main()
    captured = capsys.readouterr()
    assert "GATING_OVERREJECT" in captured.out
    assert "False Negative Total: 1" in captured.out
