from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from .core import load_json


class StaleReviewedOverrideError(RuntimeError):
    """Raised when reviewed data no longer matches the official PDF evidence."""


def _has_executable_rules(rule: dict) -> bool:
    kind = rule.get("kind")
    if kind == "course_set":
        return bool(rule.get("courses"))
    if kind in {"all_of", "any_of"}:
        children = rule.get("rules", [])
        return bool(children) and all(_has_executable_rules(child) for child in children)
    return False


def course_catalog_sha256(course_catalog: list[dict]) -> str:
    canonical = json.dumps(
        course_catalog,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def apply_reviewed_override(root: Path, academic_version: str, program: dict) -> bool:
    """Apply a hash-pinned, full-replacement reviewed record when one exists."""
    path = root / "data" / "reviewed" / academic_version / f"{program['program_id']}.json"
    reviewed = load_json(path, None)
    if reviewed is None:
        return False
    if reviewed.get("program_id") != program.get("program_id"):
        raise StaleReviewedOverrideError(f"{path}: program_id does not match")
    if reviewed.get("academic_version") != academic_version:
        raise StaleReviewedOverrideError(f"{path}: academic_version does not match")

    based_on = reviewed.get("based_on", {})
    source = program.get("source", {})
    for field in ("pdf_binary_sha256", "normalized_text_sha256"):
        expected = based_on.get(field)
        actual = source.get(field)
        if not expected or expected != actual:
            raise StaleReviewedOverrideError(
                f"{path}: reviewed {field} {expected!r} does not match source {actual!r}"
            )
    expected_pdf_version = based_on.get("selected_pdf_academic_version")
    if expected_pdf_version != program.get("selected_pdf_academic_version"):
        raise StaleReviewedOverrideError(f"{path}: selected PDF academic version no longer matches")
    expected_parser_version = based_on.get("parser_version")
    actual_parser_version = source.get("parser_version")
    if not expected_parser_version or expected_parser_version != actual_parser_version:
        raise StaleReviewedOverrideError(
            f"{path}: parser_version {expected_parser_version!r} does not match "
            f"source {actual_parser_version!r}"
        )
    expected_catalog_hash = based_on.get("course_catalog_sha256")
    actual_catalog_hash = course_catalog_sha256(program.get("course_catalog", []))
    if not expected_catalog_hash or expected_catalog_hash != actual_catalog_hash:
        raise StaleReviewedOverrideError(
            f"{path}: course_catalog_sha256 no longer matches extracted catalog"
        )

    if "structured_requirements" not in reviewed:
        raise StaleReviewedOverrideError(
            f"{path}: structured_requirements full replacement is required"
        )
    program["structured_requirements"] = deepcopy(reviewed["structured_requirements"])
    if "course_catalog" in reviewed:
        program["course_catalog"] = deepcopy(reviewed["course_catalog"])
    if "rules" in reviewed:
        program["rules"] = deepcopy(reviewed["rules"])
    program["review_status"] = reviewed.get("review_status", "needs_review")
    program["review"] = deepcopy(reviewed.get("review", {}))
    program["review"]["override_path"] = str(path.relative_to(root)).replace("\\", "/")

    if program["review_status"] == "approved":
        reviewer = str(program["review"].get("reviewer") or "").strip()
        second_reviewer = str(program["review"].get("second_reviewer") or "").strip()
        if not reviewer or not second_reviewer:
            raise StaleReviewedOverrideError(
                f"{path}: approved reviewed data requires two named reviewers"
            )
        if reviewer.casefold() == second_reviewer.casefold():
            raise StaleReviewedOverrideError(
                f"{path}: reviewer and second_reviewer must be different people"
            )
        if "rules" not in reviewed or not _has_executable_rules(reviewed["rules"]):
            raise StaleReviewedOverrideError(
                f"{path}: approved reviewed data requires non-empty executable rules"
            )
        if reviewed["structured_requirements"].get("minimum_total_credits") is None:
            raise StaleReviewedOverrideError(
                f"{path}: approved reviewed data requires a minimum total credit threshold"
            )
    return True
