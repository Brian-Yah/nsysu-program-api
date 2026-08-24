from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "review_dashboard.py"
SPEC = importlib.util.spec_from_file_location("review_dashboard", MODULE_PATH)
review_dashboard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(review_dashboard)


def test_queue_contains_remaining_conflicted_program() -> None:
    payload = review_dashboard.queue_payload("115-1")
    conflicted = [item for item in payload["programs"] if item["model_status"] == "conflicted"]
    assert {item["program_id"] for item in conflicted} == {"prog_6cd8c9bf1c955907"}
    assert sum(item["conflict_count"] for item in conflicted) == 2


def test_graduation_queue_contains_only_blocked_departments() -> None:
    payload = review_dashboard.graduation_queue_payload("113")
    assert payload["total"] == 6
    assert sum(payload["counts"].values()) == 6
    needs_evidence = {
        item["department_code"]
        for item in payload["departments"]
        if item["needs_official_evidence"]
    }
    assert needs_evidence == set()
    assert all(item["reason_details"] for item in payload["departments"])


def test_graduation_missing_source_approval_requires_official_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rule = {
        "department_code": "B0000",
        "department_name_zh": "測試學系",
        "sources": [],
        "ai_review": {"blocking_reasons": ["course_table_unavailable"]},
    }
    audit = {
        "policy_version": "test-v1",
        "ruleset_sha256": "rules",
        "departments": [
            {"department_code": "B0000", "decision": "manual_review_required"}
        ],
    }
    monkeypatch.setattr(review_dashboard, "ROOT", tmp_path)
    rule_path = (
        tmp_path
        / "api"
        / "v1"
        / "graduation-rules"
        / "113"
        / "bachelor"
        / "B0000.json"
    )
    rule_path.parent.mkdir(parents=True)
    rule_path.write_text(json.dumps(rule), encoding="utf-8")
    audit_path = tmp_path / "reports" / "graduation-ai-audit-113.json"
    audit_path.parent.mkdir(parents=True)
    audit_path.write_text(json.dumps(audit), encoding="utf-8")

    with pytest.raises(ValueError, match="official evidence URL is required"):
        review_dashboard.save_graduation_decision(
            "113",
            "B0000",
            {
                "decision": "approved",
                "reviewer": "Reviewer",
                "notes": "已確認",
            },
        )

    decision = review_dashboard.save_graduation_decision(
        "113",
        "B0000",
        {
            "decision": "approved",
            "reviewer": "Reviewer",
            "notes": "官方 PDF 第 2 頁列出課表與最低學分",
            "evidence_url": "https://example.nsysu.edu.tw/rules.pdf",
        },
    )
    assert decision["evidence_url"] == "https://example.nsysu.edu.tw/rules.pdf"
    assert review_dashboard.graduation_is_stale(decision, "113", rule, audit) is False
    rule["department_name_zh"] = "變更後名稱"
    assert review_dashboard.graduation_is_stale(decision, "113", rule, audit) is True


def test_cautious_use_requires_notes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    program = {
        "program_id": "prog_0000000000000000",
        "name_zh": "測試",
        "source": {
            "pdf_binary_sha256": "a",
            "normalized_text_sha256": "b",
            "parser_version": "0.3.0",
        },
        "selected_pdf_academic_version": "115-1",
        "structured_requirements": {"source_conflicts": []},
    }
    monkeypatch.setattr(review_dashboard, "ROOT", tmp_path)
    path = tmp_path / "data" / "published" / "115-1" / "prog_0000000000000000.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(program), encoding="utf-8")
    with pytest.raises(ValueError, match="notes are required"):
        review_dashboard.save_decision(
            "115-1",
            "prog_0000000000000000",
            {"decision": "cautious_use", "reviewer": "Reviewer", "notes": ""},
        )


def test_saved_decision_is_source_pinned(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    program = {
        "program_id": "prog_0000000000000000",
        "name_zh": "測試",
        "source": {
            "pdf_binary_sha256": "a",
            "normalized_text_sha256": "b",
            "parser_version": "0.3.0",
        },
        "selected_pdf_academic_version": "115-1",
        "structured_requirements": {"source_conflicts": []},
    }
    monkeypatch.setattr(review_dashboard, "ROOT", tmp_path)
    path = tmp_path / "data" / "published" / "115-1" / "prog_0000000000000000.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(program), encoding="utf-8")
    record = review_dashboard.save_decision(
        "115-1",
        "prog_0000000000000000",
        {"decision": "approved", "reviewer": "Reviewer", "notes": "checked"},
    )
    assert record["based_on"]["pdf_binary_sha256"] == "a"
    assert record["decision"] == "approved"
    assert review_dashboard.is_stale(record, program) is False
    program["source"]["parser_version"] = "0.4.0"
    assert review_dashboard.is_stale(record, program) is True


def test_reviewed_output_change_makes_old_decision_stale() -> None:
    program = {
        "source": {
            "pdf_binary_sha256": "a",
            "normalized_text_sha256": "b",
            "parser_version": "0.3.0",
        },
        "selected_pdf_academic_version": "115-1",
        "course_catalog": [{"catalog_entry_id": "entry_a"}],
        "structured_requirements": {"source_conflicts": []},
        "review": {"override_path": "data/reviewed/115-1/prog_test.json"},
    }
    old_decision = {
        "based_on": {
            "pdf_binary_sha256": "a",
            "normalized_text_sha256": "b",
            "selected_pdf_academic_version": "115-1",
            "parser_version": "0.3.0",
        }
    }
    assert review_dashboard.is_stale(old_decision, program) is True
    old_decision["based_on"] = review_dashboard.current_source_snapshot(program)
    assert review_dashboard.is_stale(old_decision, program) is False


def test_rejects_invalid_candidate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    program = {
        "program_id": "prog_0000000000000000",
        "name_zh": "測試",
        "source": {},
        "selected_pdf_academic_version": None,
        "structured_requirements": {
            "source_conflicts": [
                {
                    "conflict_id": "conflict_a",
                    "candidates": [{"candidate_id": "candidate_a"}],
                }
            ]
        },
    }
    monkeypatch.setattr(review_dashboard, "ROOT", tmp_path)
    path = tmp_path / "data" / "published" / "115-1" / "prog_0000000000000000.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(program), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid conflict candidate"):
        review_dashboard.save_decision(
            "115-1",
            "prog_0000000000000000",
            {
                "decision": "approved",
                "reviewer": "Reviewer",
                "conflict_choices": {"conflict_a": "candidate_wrong"},
            },
        )


def test_export_review_results_requires_current_source_pins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    program_id = "prog_0000000000000000"
    program = {
        "program_id": program_id,
        "name_zh": "測試學程",
        "source": {
            "pdf_binary_sha256": "a",
            "normalized_text_sha256": "b",
            "parser_version": "0.3.0",
        },
        "selected_pdf_academic_version": "115-1",
        "course_catalog": [],
        "structured_requirements": {"source_conflicts": []},
    }
    report = {
        "generated_at": "2026-08-16T00:00:00Z",
        "programs": [
            {
                "program_id": program_id,
                "name_zh": "測試學程",
                "model_status": "partial",
                "reasons": ["manual_requirements"],
            }
        ],
    }
    decision = {
        "program_id": program_id,
        "program_name_zh": "測試學程",
        "decision": "approved",
        "reviewer": "Reviewer",
        "reviewed_at": "2026-08-16T01:00:00Z",
        "notes": "checked",
        "conflict_choices": {},
        "based_on": review_dashboard.current_source_snapshot(program),
    }
    monkeypatch.setattr(review_dashboard, "ROOT", tmp_path)
    program_path = tmp_path / "data" / "published" / "115-1" / f"{program_id}.json"
    program_path.parent.mkdir(parents=True)
    program_path.write_text(json.dumps(program), encoding="utf-8")
    report_path = tmp_path / "reports" / "manual-review-115-1.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(json.dumps(report), encoding="utf-8")
    decision_path = tmp_path / "data" / "review-decisions" / "115-1" / f"{program_id}.json"
    decision_path.parent.mkdir(parents=True)
    decision_path.write_text(json.dumps(decision), encoding="utf-8")

    output_path = tmp_path / "reports" / "manual-review-results-115-1.json"
    exported = review_dashboard.export_review_results("115-1", output_path)
    assert exported["total"] == 1
    assert exported["counts"]["approved"] == 1
    assert exported["formal_approval"] is False
    assert output_path.is_file()
