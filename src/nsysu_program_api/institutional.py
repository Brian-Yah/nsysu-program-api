from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .core import load_json, write_json

POLICY_ID = "nsysu_program_policy_2026_08_16"
POLICY_RELATIVE_PATH = Path("data/policies/program-requirements.json")


def _stable_id(*parts: object) -> str:
    canonical = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"constraint_{hashlib.sha256(canonical).hexdigest()[:16]}"


def load_institutional_policy(root: Path) -> dict:
    policy = load_json(root / POLICY_RELATIVE_PATH, None)
    if not policy:
        raise RuntimeError(f"missing institutional policy: {POLICY_RELATIVE_PATH}")
    if policy.get("policy_id") != POLICY_ID:
        raise RuntimeError("institutional policy id does not match the supported policy")
    return policy


def _scope_signature(scope: dict) -> str:
    return json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _effective_minimum(requirements: dict, scope: dict) -> float:
    signature = _scope_signature(scope)
    values = [
        float(item["minimum_credits"])
        for item in requirements.get("credit_constraints", [])
        if item.get("kind") == "minimum_credits"
        and _scope_signature(item.get("scope", {})) == signature
        and item.get("minimum_credits") is not None
    ]
    return max(values, default=0.0)


def _effective_maximum(requirements: dict, scope: dict) -> float | None:
    signature = _scope_signature(scope)
    values = [
        float(item["maximum_counted_credits"])
        for item in requirements.get("credit_constraints", [])
        if item.get("kind") == "maximum_counted_credits"
        and _scope_signature(item.get("scope", {})) == signature
        and item.get("maximum_counted_credits") is not None
    ]
    return min(values) if values else None


def _constraint(rule: dict, policy: dict) -> dict:
    result = {
        "constraint_id": _stable_id(POLICY_ID, rule["rule_id"]),
        "kind": rule["kind"],
        "scope": rule["scope"],
        "requirement_context": "program_completion",
        "source_kind": "institutional_catalog",
        "source_url": policy["source"]["url"],
        "source_text": rule["source_text"],
        "validation_status": "source_text_match",
    }
    field = (
        "minimum_credits"
        if rule["kind"] == "minimum_credits"
        else "maximum_counted_credits"
    )
    result[field] = rule[field]
    return result


def apply_institutional_policy(program: dict, policy: dict) -> list[str]:
    """Merge stricter institutional defaults without duplicating stronger PDF rules."""
    program_type = program.get("type")
    requirements = program.setdefault("structured_requirements", {})
    constraints = requirements.setdefault("credit_constraints", [])
    applied: list[str] = []

    policy_ids = program.setdefault("institutional_policy_ids", [])
    if POLICY_ID not in policy_ids:
        policy_ids.append(POLICY_ID)

    if program_type == "department_professional_program":
        # Older PDFs repeat an abbreviated copy of the institution-wide rule.
        # Once the complete, versioned policy is linked, retaining those clipped
        # fragments as manual blockers is both duplicate and misleading.
        requirements["manual_requirements"] = [
            item
            for item in requirements.get("manual_requirements", [])
            if not (
                item.get("requirement_type") == "student_condition"
                and "專業模組課程至少"
                in str(item.get("source_text") or item.get("description") or "")
            )
        ]

    for rule in policy.get("program_type_rules", {}).get(program_type, []):
        scope = rule["scope"]
        if rule["kind"] == "minimum_credits":
            expected = float(rule["minimum_credits"])
            if scope == {"kind": "program"}:
                current_total = requirements.get("minimum_total_credits")
                if current_total is None or float(current_total) < expected:
                    requirements["minimum_total_credits"] = expected
            if _effective_minimum(requirements, scope) >= expected:
                continue
        else:
            expected = float(rule["maximum_counted_credits"])
            current = _effective_maximum(requirements, scope)
            if current is not None and current <= expected:
                continue
        constraints.append(_constraint(rule, policy))
        applied.append(rule["rule_id"])

    # A program PDF may repeat the same generated rule. Keep the first evidence
    # record for an identical semantic constraint; evidence remains available in
    # the central policy endpoint and in stronger program-specific constraints.
    unique: list[dict] = []
    seen: set[str] = set()
    for item in constraints:
        semantic = {
            key: value
            for key, value in item.items()
            if key
            not in {
                "constraint_id",
                "source_page",
                "source_url",
                "source_kind",
                "source_text",
                "validation_status",
            }
        }
        signature = json.dumps(
            semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(item)
    requirements["credit_constraints"] = unique
    program["institutional_policy_applied_rule_ids"] = applied
    return applied


def publish_institutional_policy(root: Path, api: Path, policy: dict) -> None:
    write_json(api / "policies" / "program-requirements.json", policy)
