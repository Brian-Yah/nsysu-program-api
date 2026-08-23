from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from .core import load_json

GRADUATION_AI_REVIEW_POLICY_VERSION = "graduation-rules-complete-v1"
PENDING_REVIEW_SUFFIX = "_official_table_review_pending"
BLOCKING_REASON_MESSAGES = {
    "course_table_unavailable": "官方該入學年度查詢頁沒有提供可解析的系所專業課程表。",
    "empty_course_table": "目前沒有任何正式系所課程列，不能從當學期開課資料推測。",
    "missing_minimum_graduation_credits": "官方來源未提供可確認的最低畢業學分。",
    "course_credit_unknown": "至少一門課在官方文件中沒有可唯一判定的學分。",
    "course_row_requires_review": "至少一門課有零學分、多學期配置或同名列差異。",
    "course_group_requires_review": "課群宣告門數、表列數量或學生軌道仍需確認。",
    "parser_warnings": "官方表格宣告與安全解析結果不一致。",
    "source_hash_missing": "官方來源缺少可鎖定的SHA-256。",
    "non_generated_source_requires_review": "此規則來自獨立PDF人工建模，尚未納入HTML逐列重現稽核。",
}


class GraduationAIReviewAuditError(RuntimeError):
    """Raised when pinned graduation-rule review evidence is stale or incomplete."""


def _source_hashes(rule: dict) -> dict[str, str]:
    return {
        source["source_id"]: source["sha256"]
        for source in rule.get("sources", [])
        if isinstance(source.get("source_id"), str)
        and isinstance(source.get("sha256"), str)
    }


def graduation_rule_disqualifiers(rule: dict) -> list[str]:
    """Return fail-closed reasons a department rule cannot be AI approved."""
    reasons: list[str] = []
    credits = rule.get("credit_requirements", {})
    courses = rule.get("courses", [])
    groups = rule.get("course_groups", [])
    manual_rules = rule.get("manual_review_rules", [])

    if credits.get("minimum_graduation_credits") is None:
        reasons.append("missing_minimum_graduation_credits")
    if not courses:
        reasons.append("empty_course_table")
    if any(course.get("credits") is None for course in courses):
        reasons.append("course_credit_unknown")
    if any(course.get("manual_review_required") for course in courses):
        reasons.append("course_row_requires_review")
    if any(group.get("manual_review_required") for group in groups):
        reasons.append("course_group_requires_review")
    if not rule.get("sources") or len(_source_hashes(rule)) != len(rule.get("sources", [])):
        reasons.append("source_hash_missing")
    if any(
        not str(source.get("source_id") or "").startswith(
            f"official-required-subjects-{rule.get('entry_year')}-"
        )
        for source in rule.get("sources", [])
    ):
        reasons.append("non_generated_source_requires_review")
    for item in manual_rules:
        rule_id = str(item.get("rule_id") or "")
        if rule_id.endswith("_course_table_unavailable"):
            reasons.append("course_table_unavailable")
        if rule_id.endswith("_parser_warnings"):
            reasons.append("parser_warnings")
    return sorted(set(reasons))


def graduation_ruleset_sha256(rules: list[dict]) -> str:
    """Pin every review-relevant source and parsed rule field."""
    pinned = []
    for original in sorted(rules, key=lambda value: value["department_code"]):
        rule = deepcopy(original)
        rule.pop("ai_review", None)
        rule["review_status"] = "manual_review_required"
        rule["coverage"] = "partial"
        pinned.append(rule)
    payload = json.dumps(
        pinned,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def apply_graduation_ai_review_audit(
    root: Path,
    entry_year: str,
    rules: list[dict],
) -> int:
    """Apply an exact, complete AI audit without erasing manual evaluation rules."""
    path = root / "data" / "graduation-ai-review" / f"{entry_year}.json"
    audit = load_json(path, None)
    if audit is None:
        return 0
    if audit.get("entry_year") != entry_year:
        raise GraduationAIReviewAuditError(f"{path}: entry_year does not match")
    if audit.get("policy_version") != GRADUATION_AI_REVIEW_POLICY_VERSION:
        raise GraduationAIReviewAuditError(f"{path}: unsupported policy_version")
    if audit.get("audit_status") != "passed":
        raise GraduationAIReviewAuditError(f"{path}: audit has not passed")
    if audit.get("department_count") != len(rules):
        raise GraduationAIReviewAuditError(f"{path}: department count no longer matches")
    if audit.get("ruleset_sha256") != graduation_ruleset_sha256(rules):
        raise GraduationAIReviewAuditError(f"{path}: parsed graduation rules are stale")

    decisions = audit.get("departments", [])
    by_code = {rule["department_code"]: rule for rule in rules}
    decision_by_code = {item.get("department_code"): item for item in decisions}
    if len(decisions) != len(decision_by_code) or set(decision_by_code) != set(by_code):
        raise GraduationAIReviewAuditError(
            f"{path}: every department needs exactly one decision"
        )

    audit_path = str(path.relative_to(root)).replace("\\", "/")
    approved_count = 0
    for code, rule in by_code.items():
        decision = decision_by_code[code]
        reasons = graduation_rule_disqualifiers(rule)
        expected_hashes = _source_hashes(rule)
        if decision.get("source_hashes") != expected_hashes:
            raise GraduationAIReviewAuditError(f"{path}: {code} source hashes are stale")
        if decision.get("decision") == "ai_approved":
            if reasons:
                raise GraduationAIReviewAuditError(
                    f"{path}: {code} cannot be approved: {', '.join(reasons)}"
                )
            checks = decision.get("checks", {})
            if not checks or not all(value is True for value in checks.values()):
                raise GraduationAIReviewAuditError(
                    f"{path}: {code} does not have complete passed checks"
                )
            rule["manual_review_rules"] = [
                item
                for item in rule.get("manual_review_rules", [])
                if not str(item.get("rule_id") or "").endswith(PENDING_REVIEW_SUFFIX)
            ]
            rule["review_status"] = "ai_approved"
            rule["coverage"] = "complete"
            approved_count += 1
        elif decision.get("decision") == "manual_review_required":
            if decision.get("blocking_reasons") != reasons:
                raise GraduationAIReviewAuditError(
                    f"{path}: {code} blocking reasons no longer match"
                )
            rule["review_status"] = "manual_review_required"
            rule["coverage"] = "partial"
        else:
            raise GraduationAIReviewAuditError(f"{path}: {code} has invalid decision")

        rule["ai_review"] = {
            "policy_version": GRADUATION_AI_REVIEW_POLICY_VERSION,
            "reviewed_at": audit["reviewed_at"],
            "audit_path": audit_path,
            "decision": decision["decision"],
            "source_hashes": expected_hashes,
            "blocking_reasons": reasons,
            "blocking_reason_details": [
                {
                    "code": reason,
                    "message": BLOCKING_REASON_MESSAGES.get(reason, reason),
                }
                for reason in reasons
            ],
            "manual_evaluation_rule_ids": [
                item["rule_id"] for item in rule.get("manual_review_rules", [])
            ],
        }
    return approved_count
