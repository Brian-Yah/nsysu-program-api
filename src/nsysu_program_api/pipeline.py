from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .ai_review import (
    apply_ai_review_audit,
    review_reason_details,
    simple_logic_disqualifiers,
)
from .core import (
    CATALOG_URL,
    DATA_REVISION,
    PARSER_VERSION,
    SCHEMA_VERSION,
    Fetcher,
    extract_candidate,
    extract_pdf_tables,
    extract_pdf_text,
    extractor_versions,
    load_json,
    now_iso,
    parse_catalog,
    sha256,
    split_responsible,
    write_json,
)
from .graduation import build_graduation_api
from .graduation_rules import build_graduation_rules_api
from .institutional import (
    apply_institutional_policy,
    load_institutional_policy,
    publish_institutional_policy,
)
from .requirements import finalize_completion_summary, select_applicable_rule_version
from .reviewed import apply_reviewed_override


def fetch_catalog(root: Path, academic_version: str, user_agent: str) -> dict:
    retrieved_at = now_iso()
    fetcher = Fetcher(user_agent)
    response = fetcher.get(CATALOG_URL)
    registry_path = root / "data" / "program-id-registry.json"
    registry = load_json(registry_path, {})
    programs = parse_catalog(response.body, retrieved_at, registry)
    for program in programs:
        program["academic_version"] = academic_version
    write_json(registry_path, registry)
    catalog = {
        "schema_version": SCHEMA_VERSION,
        "academic_version": academic_version,
        "data_revision": DATA_REVISION,
        "retrieved_at": retrieved_at,
        "source": {
            "url": CATALOG_URL,
            "binary_sha256": sha256(response.body),
            "http": {
                "status": response.status,
                "etag": response.headers.get("ETag"),
                "last_modified": response.headers.get("Last-Modified"),
            },
            "parser_version": PARSER_VERSION,
        },
        "programs": programs,
    }
    write_json(root / "data" / "source" / academic_version / "catalog.json", catalog)
    return catalog


def process_pdfs(root: Path, catalog: dict, user_agent: str, reuse_cache: bool = False) -> dict:
    fetcher = Fetcher(user_agent)
    version = catalog["academic_version"]
    catalog["schema_version"] = SCHEMA_VERSION
    catalog["data_revision"] = DATA_REVISION
    cache = root / "cache" / "pdf"
    cache.mkdir(parents=True, exist_ok=True)
    stats = Counter()
    failures = []
    runtime_extractors = extractor_versions()
    for program in catalog["programs"]:
        # Parser diagnostics describe only the current extraction pass. Keeping
        # prior-run values would make fixed PDFs continue to look broken.
        program["warnings"] = []
        if not program.get("coordinator") and "\n" in (program.get("responsible_unit") or ""):
            program["responsible_unit"], program["coordinator"] = split_responsible(
                program["responsible_unit"]
            )
        url = program["source_pdf"]
        if not url:
            stats["no_pdf"] += 1
            program["warnings"].append("No PDF URL in catalog")
            continue
        stats["pdf_urls"] += 1
        try:
            known_digest = program["source"].get("pdf_binary_sha256")
            known_path = cache / f"{known_digest}.pdf" if known_digest else None
            if reuse_cache and known_path and known_path.exists():
                response_body = known_path.read_bytes()
                response_status = program["source"].get("http", {}).get("status", 200)
                response_headers = program["source"].get("http", {})
                stats["cache_reused"] += 1
            else:
                response = fetcher.get(url)
                response_body = response.body
                response_status = response.status
                response_headers = response.headers
                stats["downloaded"] += 1
            digest = sha256(response_body)
            pdf_path = cache / f"{digest}.pdf"
            if not pdf_path.exists():
                pdf_path.write_bytes(response_body)
            text, warnings = extract_pdf_text(pdf_path)
            rule_versions, table_warnings = extract_pdf_tables(pdf_path)
            warnings.extend(table_warnings)
            source = program["source"]
            source["parser_version"] = PARSER_VERSION
            source["extractor_versions"] = runtime_extractors
            source["pdf_binary_sha256"] = digest
            source["http"] = {
                "status": response_status,
                "etag": response_headers.get("ETag") or response_headers.get("etag"),
                "last_modified": response_headers.get("Last-Modified")
                or response_headers.get("last_modified"),
                "content_length": len(response_body),
            }
            source["normalized_text_sha256"] = sha256(text.encode()) if text else None
            extracted = extract_candidate(text)
            latest_structured, version_warnings = select_applicable_rule_version(
                rule_versions, version
            )
            warnings.extend(version_warnings)
            program["selected_pdf_academic_version"] = latest_structured["pdf_academic_version"]
            program["course_catalog"] = latest_structured["courses"]
            program["structured_requirements"] = latest_structured["requirements"]
            stats["structured_course_rows"] += len(latest_structured["courses"])
            if latest_structured["courses"]:
                stats["structured_programs"] += 1
            audit = latest_structured.get("audit", {})
            stats["evidence_matched_rows"] += audit.get("evidence_matched_count", 0)
            stats["compound_rows_needing_review"] += len(
                audit.get("compound_rows_needing_review", [])
            )
            stats["duplicates_removed"] += audit.get("duplicates_removed", 0)
            extracted.update(
                {
                    "program_id": program["program_id"],
                    "academic_version": version,
                    "text_length": len(text),
                    "warnings": extracted["warnings"] + warnings,
                    "raw_text": text,
                    "rule_versions": rule_versions,
                    "selected_pdf_academic_version": latest_structured["pdf_academic_version"],
                    "structured_courses": latest_structured["courses"],
                    "structured_requirements": latest_structured["requirements"],
                }
            )
            write_json(
                root / "data" / "extracted" / version / f"{program['program_id']}.json", extracted
            )
            if text:
                stats["extracted"] += 1
                program["review_status"] = "needs_review"
                program["rules"] = {
                    "kind": "manual_review",
                    "reason": "Extracted candidate awaits approval",
                }
            else:
                stats["extract_failed"] += 1
            if warnings:
                stats["ocr_needed"] += 1
                program["warnings"].extend(warnings)
        except Exception as exc:
            stats["failed"] += 1
            failure = {
                "program_id": program["program_id"],
                "name_zh": program["name_zh"],
                "url": url,
                "reason": f"{type(exc).__name__}: {exc}",
            }
            failures.append(failure)
            program["warnings"].append(failure["reason"])
    write_json(root / "data" / "source" / version / "catalog.json", catalog)
    report = {
        "generated_at": now_iso(),
        "academic_version": version,
        "catalog_total": len(catalog["programs"]),
        "active_total": sum(p["status"] == "active" for p in catalog["programs"]),
        "discontinued_total": sum(p["status"] != "active" for p in catalog["programs"]),
        **stats,
        "published_approved": 0,
        "needs_review": sum(p["review_status"] == "needs_review" for p in catalog["programs"]),
        "failures": failures,
    }
    write_json(root / "reports" / f"initial-{version}.json", report)
    return report


def approve_source_only(root: Path, catalog: dict) -> list[dict]:
    """Write the already classified catalog to published storage."""
    version = catalog["academic_version"]
    published = []
    for source in catalog["programs"]:
        item = dict(source)
        if item["review_status"] == "source_only":
            item["rules"] = {"kind": "manual_review", "reason": "No extracted rule text"}
        published.append(item)
        write_json(root / "data" / "published" / version / f"{item['program_id']}.json", item)
    return published


def validate_program(program: dict) -> list[str]:
    errors = []
    required = [
        "program_id",
        "name_zh",
        "type",
        "status",
        "academic_version",
        "source",
        "review_status",
        "rules",
    ]
    errors.extend(f"missing {key}" for key in required if key not in program)
    if program.get("source_pdf") and not program.get("source", {}).get("pdf_binary_sha256"):
        errors.append("PDF source missing binary hash")
    if (
        program.get("review_status") == "approved"
        and program.get("rules", {}).get("kind") == "manual_review"
    ):
        errors.append("approved program cannot contain only manual_review")
    return errors


def build_api(root: Path, version: str) -> dict:
    source_catalog = load_json(root / "data" / "source" / version / "catalog.json", None)
    if not source_catalog:
        raise RuntimeError(f"missing source catalog for {version}")
    reviewed_program_count = 0
    institutional_policy = load_institutional_policy(root)
    for program in source_catalog["programs"]:
        extracted = load_json(
            root / "data" / "extracted" / version / f"{program['program_id']}.json",
            {},
        )
        program["selected_pdf_academic_version"] = extracted.get("selected_pdf_academic_version")
        program["course_catalog"] = extracted.get("structured_courses", [])
        program["structured_requirements"] = extracted.get("structured_requirements", {})
        if apply_reviewed_override(root, version, program):
            reviewed_program_count += 1
        apply_institutional_policy(program, institutional_policy)
    ai_approved_program_count = apply_ai_review_audit(
        root, version, source_catalog["programs"]
    )
    for program in source_catalog["programs"]:
        finalize_completion_summary(
            program["structured_requirements"],
            approved=program.get("review_status") in {"approved", "ai_approved"},
        )
    programs = approve_source_only(root, source_catalog)
    errors = {p["program_id"]: e for p in programs if (e := validate_program(p))}
    if errors:
        raise RuntimeError(f"validation errors: {json.dumps(errors, ensure_ascii=False)}")
    api = root / "api" / "v1"
    publish_institutional_policy(root, api, institutional_policy)
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "academic_version": version,
        "data_revision": source_catalog["data_revision"],
        "retrieved_at": source_catalog["retrieved_at"],
        "programs": programs,
    }
    write_json(api / "semesters" / version / "programs.json", envelope)
    index = [
        {
            "program_id": p["program_id"],
            "name_zh": p["name_zh"],
            "type": p["type"],
            "status": p["status"],
            "review_status": p["review_status"],
        }
        for p in programs
    ]
    write_json(api / "semesters" / version / "program-index.json", index)
    write_json(api / "latest" / "programs.json", envelope)
    for program in programs:
        base = api / "programs" / program["program_id"]
        write_json(base / "versions" / f"{version}.json", program)
        write_json(
            base / "index.json",
            {
                "program_id": program["program_id"],
                "latest_academic_version": version,
                "versions": [version],
                "program": program,
            },
        )
    manual_review_programs = []
    for program in programs:
        if program.get("review_status") in {"approved", "ai_approved"}:
            continue
        reasons = simple_logic_disqualifiers(program)
        manual_review_programs.append(
            {
                "program_id": program["program_id"],
                "name_zh": program["name_zh"],
                "type": program["type"],
                "status": program["status"],
                "review_status": program["review_status"],
                "model_status": program.get("structured_requirements", {})
                .get("completion_summary", {})
                .get("model_status"),
                "reasons": reasons,
                "reason_details": review_reason_details(program, reasons),
            }
        )
    write_json(
        root / "reports" / f"manual-review-{version}.json",
        {
            "generated_at": now_iso(),
            "academic_version": version,
            "ai_approved_count": ai_approved_program_count,
            "manual_review_count": len(manual_review_programs),
            "programs": manual_review_programs,
        },
    )
    schema_src = root / "schemas" / "program.schema.json"
    schema_dest = api / "schemas" / "program.schema.json"
    schema_dest.parent.mkdir(parents=True, exist_ok=True)
    schema_dest.write_text(schema_src.read_text(encoding="utf-8"), encoding="utf-8")
    policy_schema_src = root / "schemas" / "institutional-policy.schema.json"
    (api / "schemas" / "institutional-policy.schema.json").write_text(
        policy_schema_src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    entry_year = version.split("-", 1)[0]
    graduation_source = root / "data" / "graduation-requirements" / entry_year / "bachelor.json"
    graduation_index = (
        build_graduation_api(root, entry_year) if graduation_source.exists() else None
    )
    graduation_rules_source = root / "data" / "graduation-rules" / "common" / "113-plus.json"
    graduation_rules_index = (
        build_graduation_rules_api(root) if graduation_rules_source.exists() else None
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "latest_academic_version": version,
        "data_revision": source_catalog["data_revision"],
        "generated_at": now_iso(),
        "program_count": len(programs),
        "active_program_count": sum(p["status"] == "active" for p in programs),
        "rule_model_version": "1.2",
        "reviewed_program_count": reviewed_program_count,
        "ai_approved_program_count": ai_approved_program_count,
        "manual_review_program_count": len(manual_review_programs),
        "unresolved_source_conflict_count": sum(
            sum(
                conflict.get("resolution_status") == "unresolved"
                for conflict in program.get("structured_requirements", {}).get(
                    "source_conflicts", []
                )
            )
            for program in programs
        ),
        "paths": {
            "latest": "latest/programs.json",
            "schema": "schemas/program.schema.json",
            "program_requirements_policy_schema": "schemas/institutional-policy.schema.json",
            "semester": f"semesters/{version}/programs.json",
            "graduation_requirements": (
                "graduation-requirements/index.json" if graduation_index else None
            ),
            "graduation_rules": "graduation-rules/index.json" if graduation_rules_index else None,
            "program_requirements_policy": "policies/program-requirements.json",
        },
    }
    if graduation_index:
        manifest["graduation_requirement_department_count"] = graduation_index["department_count"]
    if graduation_rules_index:
        manifest["graduation_rule_department_count"] = graduation_rules_index["department_count"]
        manifest["graduation_rule_ai_approved_department_count"] = graduation_rules_index[
            "ai_approved_department_count"
        ]
        manifest["graduation_rule_manual_review_required_department_count"] = (
            graduation_rules_index["manual_review_required_department_count"]
        )
    write_json(api / "manifest.json", manifest)
    return manifest


def semantic_diff(old: dict, new: dict) -> dict:
    old_by = {p["program_id"]: p for p in old.get("programs", [])}
    new_by = {p["program_id"]: p for p in new.get("programs", [])}
    added, removed, changed = (
        sorted(new_by.keys() - old_by.keys()),
        sorted(old_by.keys() - new_by.keys()),
        [],
    )
    fields = ["name_zh", "status", "responsible_unit", "coordinator", "source_pdf"]
    source_fields = ["pdf_binary_sha256", "normalized_text_sha256"]
    for pid in sorted(old_by.keys() & new_by.keys()):
        changes = []
        for field in fields:
            if old_by[pid].get(field) != new_by[pid].get(field):
                changes.append(
                    {"field": field, "old": old_by[pid].get(field), "new": new_by[pid].get(field)}
                )
        for field in source_fields:
            if old_by[pid].get("source", {}).get(field) != new_by[pid].get("source", {}).get(field):
                changes.append(
                    {
                        "field": f"source.{field}",
                        "old": old_by[pid].get("source", {}).get(field),
                        "new": new_by[pid].get("source", {}).get(field),
                    }
                )
        for field in ("structured_requirements", "rules", "course_catalog"):
            old_value = old_by[pid].get(field)
            new_value = new_by[pid].get(field)
            if old_value != new_value:
                old_digest = sha256(
                    json.dumps(old_value, ensure_ascii=False, sort_keys=True).encode()
                )
                new_digest = sha256(
                    json.dumps(new_value, ensure_ascii=False, sort_keys=True).encode()
                )
                changes.append(
                    {
                        "field": field,
                        "old_sha256": old_digest,
                        "new_sha256": new_digest,
                    }
                )
        if changes:
            changed.append(
                {"program_id": pid, "name_zh": new_by[pid]["name_zh"], "changes": changes}
            )
    return {
        "generated_at": now_iso(),
        "added": added,
        "removed": removed,
        "changed": changed,
        "summary": {"added": len(added), "removed": len(removed), "changed": len(changed)},
    }
