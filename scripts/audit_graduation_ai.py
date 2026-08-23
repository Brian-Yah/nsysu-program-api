from __future__ import annotations

import argparse
import json
from pathlib import Path

from nsysu_program_api.core import Fetcher, load_json, now_iso, sha256, write_json
from nsysu_program_api.graduation_ai_review import (
    GRADUATION_AI_REVIEW_POLICY_VERSION,
    graduation_rule_disqualifiers,
    graduation_ruleset_sha256,
)
from nsysu_program_api.graduation_rule_fetch import parse_official_department_rule


def _roundtrip_matches(rule: dict, body: bytes) -> bool:
    source = rule["sources"][0]
    if not source["source_id"].startswith(
        f"official-required-subjects-{rule['entry_year']}-"
    ):
        return False
    reparsed = parse_official_department_rule(
        body.decode("utf-8", errors="strict"),
        entry_year=rule["entry_year"],
        department_code=rule["department_code"],
        department_name=rule["department_name_zh"],
        source_url=source["url"],
        source_hash=source["sha256"],
        reviewed_at=rule["reviewed_at"],
    )
    fields = (
        "credit_requirements",
        "courses",
        "course_groups",
        "prerequisites",
        "non_duplicated_counting_groups",
        "manual_review_rules",
        "additional_credit_rules",
    )
    return all(reparsed.get(field) == rule.get(field) for field in fields)


def build_online_audit(root: Path, entry_year: str, user_agent: str) -> dict:
    paths = sorted(
        (root / "data" / "graduation-rules" / entry_year / "bachelor").glob(
            "*.json"
        )
    )
    rules = [load_json(path, {}) for path in paths]
    fetcher = Fetcher(user_agent)
    decisions = []
    online_errors = []
    for rule in rules:
        code = rule["department_code"]
        hashes_match = True
        roundtrip_match = None
        for source in rule.get("sources", []):
            try:
                response = fetcher.get(source["url"])
            except RuntimeError as error:
                hashes_match = False
                online_errors.append(f"{code}: {error}")
                continue
            actual_hash = sha256(response.body)
            if actual_hash != source.get("sha256"):
                hashes_match = False
                online_errors.append(
                    f"{code}: source hash changed ({source.get('sha256')} -> {actual_hash})"
                )
            if source["source_id"].startswith(
                f"official-required-subjects-{entry_year}-"
            ):
                try:
                    roundtrip_match = _roundtrip_matches(rule, response.body)
                except (UnicodeDecodeError, ValueError) as error:
                    roundtrip_match = False
                    online_errors.append(f"{code}: roundtrip failed: {error}")
                if not roundtrip_match:
                    online_errors.append(f"{code}: parser roundtrip does not match pinned data")

        reasons = graduation_rule_disqualifiers(rule)
        approved = not reasons and hashes_match and roundtrip_match is True
        decisions.append(
            {
                "department_code": code,
                "department_name_zh": rule["department_name_zh"],
                "decision": "ai_approved" if approved else "manual_review_required",
                "source_hashes": {
                    source["source_id"]: source["sha256"]
                    for source in rule.get("sources", [])
                },
                "blocking_reasons": reasons,
                "checks": {
                    "official_source_hash_matched": hashes_match,
                    "parser_roundtrip_matched": roundtrip_match is True,
                    "course_rows_reviewed": approved,
                    "credit_requirements_reviewed": approved,
                    "course_groups_reviewed": approved,
                    "manual_rules_classified": approved,
                },
            }
        )
    return {
        "entry_year": entry_year,
        "policy_version": GRADUATION_AI_REVIEW_POLICY_VERSION,
        "audit_status": "passed" if not online_errors else "failed",
        "reviewed_at": now_iso()[:10],
        "department_count": len(rules),
        "ruleset_sha256": graduation_ruleset_sha256(rules),
        "online_errors": online_errors,
        "departments": decisions,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--entry-year", default="113")
    parser.add_argument("--user-agent", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    audit = build_online_audit(args.root, args.entry_year, args.user_agent)
    if args.output:
        write_json(args.output, audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0 if audit["audit_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
