from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .core import load_json

AI_REVIEW_POLICY_VERSION = "simple-logic-v1"
SPECIAL_REQUIREMENT_COLLECTIONS = (
    "entry_selection_constraints",
    "course_count_constraints",
    "program_course_selection_constraints",
    "named_group_selection_constraints",
    "no_double_count_constraints",
    "manual_requirements",
    "source_conflicts",
)
SPECIAL_NOTE_PATTERN = re.compile(
    r"(?:[一二三四五六七八九十兩0-9]+[擇選][一二三四五六七八九十兩0-9]+|"
    r"任選|至少.{0,12}[一二三四五六七八九十兩0-9]+\s*(?:門|科)|"
    r"必修.{0,12}[一二三四五六七八九十兩0-9]+\s*門|"
    r"不得重複|不可重複|至多|最多|上限|"
    r"其餘.{0,20}(?:計入|列入|納入)|可多修|為相同課程)"
)


class AIReviewAuditError(RuntimeError):
    """Raised when the pinned AI-review audit no longer matches generated data."""


def _is_simple_credit_constraint(constraint: dict) -> bool:
    if constraint.get("kind") != "minimum_credits":
        return False
    scope = constraint.get("scope", {})
    kind = scope.get("kind")
    if kind == "program":
        return set(scope) == {"kind"}
    if kind == "catalog_filter":
        groups = scope.get("requirement_groups", [])
        return (
            bool(groups)
            and set(groups) <= {"core", "elective"}
            and set(scope) == {"kind", "requirement_groups"}
        )
    if kind == "course_eligibility":
        affiliations = scope.get("excluded_affiliations", [])
        return (
            bool(affiliations)
            and set(affiliations) <= {"home_department", "double_major", "minor"}
            and scope.get("excluded_course_roles") == ["all"]
            and set(scope)
            == {"kind", "excluded_affiliations", "excluded_course_roles"}
        )
    return False


def simple_logic_disqualifiers(program: dict) -> list[str]:
    """Return conservative reasons a program still requires targeted review."""
    requirements = program.get("structured_requirements", {})
    reasons = []
    if program.get("status") != "active":
        reasons.append("not_active")
    if program.get("warnings"):
        reasons.append("parser_warning")
    if not program.get("course_catalog"):
        reasons.append("empty_course_catalog")
    if requirements.get("minimum_total_credits") is None:
        reasons.append("missing_total_minimum")
    for field in (
        "maximum_total_credits",
        "maximum_core_credits",
        "maximum_elective_credits",
        "minimum_core_courses",
        "minimum_elective_courses",
    ):
        if requirements.get(field) is not None:
            reasons.append(field)
    for collection in SPECIAL_REQUIREMENT_COLLECTIONS:
        if requirements.get(collection):
            reasons.append(collection)
    if any(
        not _is_simple_credit_constraint(constraint)
        for constraint in requirements.get("credit_constraints", [])
    ):
        reasons.append("non_standard_credit_constraint")
    if any(
        course.get("requirement_group") not in {"core", "elective"}
        for course in program.get("course_catalog", [])
    ):
        reasons.append("unclassified_course")
    if any(
        SPECIAL_NOTE_PATTERN.search(course.get("notes") or "")
        for course in program.get("course_catalog", [])
    ):
        reasons.append("special_rule_text_in_course_note")
    if program.get("review", {}).get("override_path"):
        reasons.append("human_review_override")
    return sorted(set(reasons))


def simple_logic_candidate_ids(programs: list[dict]) -> list[str]:
    return sorted(
        program["program_id"]
        for program in programs
        if not simple_logic_disqualifiers(program)
    )


def candidate_set_sha256(candidate_ids: list[str]) -> str:
    canonical = json.dumps(
        sorted(candidate_ids), ensure_ascii=False, separators=(",", ":")
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def apply_ai_review_audit(root: Path, academic_version: str, programs: list[dict]) -> int:
    """Apply a fail-closed, sampled AI approval to the exact simple-rule set."""
    path = root / "data" / "ai-review" / f"{academic_version}.json"
    audit = load_json(path, None)
    if audit is None:
        return 0
    if audit.get("academic_version") != academic_version:
        raise AIReviewAuditError(f"{path}: academic_version does not match")
    if audit.get("policy_version") != AI_REVIEW_POLICY_VERSION:
        raise AIReviewAuditError(f"{path}: unsupported policy_version")
    if audit.get("audit_status") != "passed":
        raise AIReviewAuditError(f"{path}: audit has not passed")

    candidate_ids = simple_logic_candidate_ids(programs)
    if audit.get("candidate_count") != len(candidate_ids):
        raise AIReviewAuditError(f"{path}: candidate count no longer matches")
    if audit.get("candidate_set_sha256") != candidate_set_sha256(candidate_ids):
        raise AIReviewAuditError(f"{path}: candidate set no longer matches")

    by_id = {program["program_id"]: program for program in programs}
    sample = audit.get("sample", [])
    if len(sample) != 3 or len({item.get("program_id") for item in sample}) != 3:
        raise AIReviewAuditError(f"{path}: exactly three unique samples are required")
    for item in sample:
        program_id = item.get("program_id")
        if program_id not in candidate_ids or program_id not in by_id:
            raise AIReviewAuditError(f"{path}: sampled program is not a candidate")
        program = by_id[program_id]
        source = program.get("source", {})
        expected = {
            "pdf_binary_sha256": source.get("pdf_binary_sha256"),
            "normalized_text_sha256": source.get("normalized_text_sha256"),
            "selected_pdf_academic_version": program.get(
                "selected_pdf_academic_version"
            ),
        }
        if any(item.get(field) != value for field, value in expected.items()):
            raise AIReviewAuditError(f"{path}: sampled source evidence is stale")
        if item.get("result") != "passed":
            raise AIReviewAuditError(f"{path}: sampled review did not pass")

    audit_path = str(path.relative_to(root)).replace("\\", "/")
    approved_count = 0
    for program_id in candidate_ids:
        program = by_id[program_id]
        if program.get("review_status") != "needs_review":
            continue
        program["review_status"] = "ai_approved"
        program["review"] = {
            "method": "random_sampled_simple_logic",
            "policy_version": AI_REVIEW_POLICY_VERSION,
            "audit_path": audit_path,
            "candidate_count": len(candidate_ids),
            "sample_size": len(sample),
        }
        program["rules"] = {
            "kind": "manual_review",
            "reason": (
                "Legacy course-code evaluator is unavailable; AI-Approved status "
                "applies to structured_requirements"
            ),
        }
        approved_count += 1
    return approved_count
