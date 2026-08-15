from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .core import (
    CATALOG_URL,
    PARSER_VERSION,
    SCHEMA_VERSION,
    Fetcher,
    extract_candidate,
    extract_pdf_tables,
    extract_pdf_text,
    load_json,
    now_iso,
    parse_catalog,
    sha256,
    split_responsible,
    write_json,
)


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
        "data_revision": 1,
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
    cache = root / "cache" / "pdf"
    cache.mkdir(parents=True, exist_ok=True)
    stats = Counter()
    failures = []
    for program in catalog["programs"]:
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
            latest_structured = next(
                (version for version in rule_versions if version["courses"]),
                {"pdf_academic_version": None, "courses": [], "requirements": {}},
            )
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
    """Publish catalog metadata; rules remain explicit manual_review, never AI-approved."""
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
    for program in source_catalog["programs"]:
        extracted = load_json(
            root / "data" / "extracted" / version / f"{program['program_id']}.json",
            {},
        )
        program["selected_pdf_academic_version"] = extracted.get("selected_pdf_academic_version")
        program["course_catalog"] = extracted.get("structured_courses", [])
        program["structured_requirements"] = extracted.get("structured_requirements", {})
    programs = approve_source_only(root, source_catalog)
    errors = {p["program_id"]: e for p in programs if (e := validate_program(p))}
    if errors:
        raise RuntimeError(f"validation errors: {json.dumps(errors, ensure_ascii=False)}")
    api = root / "api" / "v1"
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
    schema_src = root / "schemas" / "program.schema.json"
    schema_dest = api / "schemas" / "program.schema.json"
    schema_dest.parent.mkdir(parents=True, exist_ok=True)
    schema_dest.write_text(schema_src.read_text(encoding="utf-8"), encoding="utf-8")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "latest_academic_version": version,
        "data_revision": source_catalog["data_revision"],
        "generated_at": now_iso(),
        "program_count": len(programs),
        "active_program_count": sum(p["status"] == "active" for p in programs),
        "paths": {
            "latest": "latest/programs.json",
            "schema": "schemas/program.schema.json",
            "semester": f"semesters/{version}/programs.json",
        },
    }
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
