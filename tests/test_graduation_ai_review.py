from __future__ import annotations

from copy import deepcopy

import pytest

from nsysu_program_api.core import write_json
from nsysu_program_api.graduation_ai_review import (
    GRADUATION_AI_REVIEW_POLICY_VERSION,
    GraduationAIReviewAuditError,
    apply_graduation_ai_review_audit,
    graduation_rule_disqualifiers,
    graduation_ruleset_sha256,
)


def department(code: str, *, blocked: bool = False) -> dict:
    source_id = f"official-required-subjects-113-{code.casefold()}"
    return {
        "schema_version": "1.0",
        "rule_set_id": f"nsysu-113-bachelor-{code}",
        "rule_type": "department",
        "entry_year": "113",
        "degree_level": "bachelor",
        "department_code": code,
        "department_name_zh": f"測試系所 {code}",
        "department_name_en": None,
        "common_rule_ref": "../../common/113-plus.json",
        "review_status": "manual_review_required",
        "reviewed_at": "2026-08-22",
        "coverage": "partial",
        "credit_requirements": {
            "minimum_graduation_credits": None if blocked else 128,
            "minimum_required_credits": None,
            "minimum_elective_credits": None,
            "minimum_department_elective_credits": None,
            "minimum_department_professional_credits": None,
        },
        "sources": [
            {
                "source_id": source_id,
                "title": "官方必修科目表",
                "url": "https://example.test/rules",
                "document_type": "html",
                "reviewed_at": "2026-08-22",
                "sha256": "a" * 64,
            }
        ],
        "courses": []
        if blocked
        else [
            {
                "course_id": f"{code.casefold()}_course",
                "canonical_name_zh": "測試課程",
                "canonical_name_en": None,
                "known_aliases": [],
                "credits": 3,
                "curriculumRequirement": "department_required",
                "recommendedYear": 1,
                "recommendedSemester": "fall",
                "source_document": source_id,
                "notes": [],
                "alternatives": [],
                "manual_review_required": False,
            }
        ],
        "course_groups": [],
        "prerequisites": [],
        "non_duplicated_counting_groups": [],
        "manual_review_rules": [
            {
                "rule_id": f"{code.casefold()}_course_table_unavailable"
                if blocked
                else f"{code.casefold()}_official_table_review_pending",
                "description": "等待確認",
                "reason": "尚未核對",
                "source_document": source_id,
                "resolution": "核對官方文件",
            }
        ],
        "additional_credit_rules": [],
    }


def audit_for(rules: list[dict]) -> dict:
    departments = []
    for rule in rules:
        reasons = graduation_rule_disqualifiers(rule)
        approved = not reasons
        departments.append(
            {
                "department_code": rule["department_code"],
                "department_name_zh": rule["department_name_zh"],
                "decision": "ai_approved" if approved else "manual_review_required",
                "source_hashes": {
                    rule["sources"][0]["source_id"]: rule["sources"][0]["sha256"]
                },
                "blocking_reasons": reasons,
                "checks": {
                    "official_source_hash_matched": approved,
                    "parser_roundtrip_matched": approved,
                },
            }
        )
    return {
        "entry_year": "113",
        "policy_version": GRADUATION_AI_REVIEW_POLICY_VERSION,
        "audit_status": "passed",
        "reviewed_at": "2026-08-22",
        "department_count": len(rules),
        "ruleset_sha256": graduation_ruleset_sha256(rules),
        "online_errors": [],
        "departments": departments,
    }


def test_complete_audit_approves_safe_rule_and_preserves_blocked_rule(tmp_path) -> None:
    rules = [department("B9000"), department("B9001", blocked=True)]
    write_json(tmp_path / "data/graduation-ai-review/113.json", audit_for(rules))

    assert apply_graduation_ai_review_audit(tmp_path, "113", rules) == 1
    assert rules[0]["review_status"] == "ai_approved"
    assert rules[0]["coverage"] == "complete"
    assert rules[0]["manual_review_rules"] == []
    assert rules[1]["review_status"] == "manual_review_required"
    assert rules[1]["ai_review"]["blocking_reasons"] == [
        "course_table_unavailable",
        "empty_course_table",
        "missing_minimum_graduation_credits",
    ]
    assert rules[1]["ai_review"]["blocking_reason_details"][0]["message"]


def test_changed_parsed_rule_fails_closed(tmp_path) -> None:
    rules = [department("B9000")]
    write_json(tmp_path / "data/graduation-ai-review/113.json", audit_for(rules))
    changed = deepcopy(rules)
    changed[0]["courses"][0]["credits"] = 4

    with pytest.raises(GraduationAIReviewAuditError, match="stale"):
        apply_graduation_ai_review_audit(tmp_path, "113", changed)
