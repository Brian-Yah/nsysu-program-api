#!/usr/bin/env python3
"""Local, source-pinned review dashboard for program requirements."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import sys
import webbrowser
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = ROOT / "review-dashboard"
PROGRAM_ID_PATTERN = re.compile(r"^prog_[0-9a-f]{16}$")
VERSION_PATTERN = re.compile(r"^[0-9]{3}-[12]$")
ENTRY_YEAR_PATTERN = re.compile(r"^[0-9]{3}$")
DEPARTMENT_CODE_PATTERN = re.compile(r"^B[0-9]{4}$")
DECISIONS = {"approved", "cautious_use", "needs_fix", "skipped"}
MAX_BODY_SIZE = 64 * 1024


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def validate_version(version: str) -> str:
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError("invalid academic version")
    return version


def program_path(version: str, program_id: str) -> Path:
    validate_version(version)
    if not PROGRAM_ID_PATTERN.fullmatch(program_id):
        raise ValueError("invalid program id")
    return ROOT / "data" / "published" / version / f"{program_id}.json"


def decision_path(version: str, program_id: str) -> Path:
    validate_version(version)
    if not PROGRAM_ID_PATTERN.fullmatch(program_id):
        raise ValueError("invalid program id")
    return ROOT / "data" / "review-decisions" / version / f"{program_id}.json"


def validate_entry_year(entry_year: str) -> str:
    if not ENTRY_YEAR_PATTERN.fullmatch(entry_year):
        raise ValueError("invalid entry year")
    return entry_year


def graduation_rule_path(entry_year: str, department_code: str) -> Path:
    validate_entry_year(entry_year)
    if not DEPARTMENT_CODE_PATTERN.fullmatch(department_code):
        raise ValueError("invalid department code")
    return (
        ROOT
        / "api"
        / "v1"
        / "graduation-rules"
        / entry_year
        / "bachelor"
        / f"{department_code}.json"
    )


def graduation_decision_path(entry_year: str, department_code: str) -> Path:
    validate_entry_year(entry_year)
    if not DEPARTMENT_CODE_PATTERN.fullmatch(department_code):
        raise ValueError("invalid department code")
    return (
        ROOT
        / "data"
        / "graduation-review-decisions"
        / entry_year
        / f"{department_code}.json"
    )


def graduation_source_snapshot(entry_year: str, rule: dict, audit: dict) -> dict:
    rule_bytes = json.dumps(
        rule, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return {
        "entry_year": entry_year,
        "policy_version": audit.get("policy_version"),
        "ruleset_sha256": audit.get("ruleset_sha256"),
        "department_rule_sha256": hashlib.sha256(rule_bytes).hexdigest(),
        "official_source_hashes": {
            source.get("source_id"): source.get("sha256")
            for source in rule.get("sources", [])
            if source.get("source_id")
        },
    }


def graduation_is_stale(decision: dict | None, entry_year: str, rule: dict, audit: dict) -> bool:
    return bool(
        decision
        and decision.get("based_on") != graduation_source_snapshot(entry_year, rule, audit)
    )


def graduation_queue_payload(entry_year: str) -> dict:
    validate_entry_year(entry_year)
    audit_path = ROOT / "reports" / f"graduation-ai-audit-{entry_year}.json"
    audit = load_json(audit_path)
    if not audit:
        raise FileNotFoundError(f"missing graduation audit report: {audit_path}")

    departments = []
    counts = {decision: 0 for decision in DECISIONS}
    counts["unreviewed"] = 0
    counts["stale"] = 0
    for audited in audit.get("departments", []):
        if audited.get("decision") != "manual_review_required":
            continue
        department_code = audited["department_code"]
        rule = load_json(graduation_rule_path(entry_year, department_code))
        if not rule:
            raise FileNotFoundError(department_code)
        decision = load_json(graduation_decision_path(entry_year, department_code))
        stale = graduation_is_stale(decision, entry_year, rule, audit)
        decision_name = decision.get("decision") if decision else None
        if stale:
            counts["stale"] += 1
        elif decision_name in DECISIONS:
            counts[decision_name] += 1
        else:
            counts["unreviewed"] += 1

        ai_review = rule.get("ai_review", {})
        reasons = ai_review.get("blocking_reasons", audited.get("blocking_reasons", []))
        sources = rule.get("sources", [])
        departments.append(
            {
                "department_code": department_code,
                "department_name_zh": rule.get("department_name_zh"),
                "decision": decision_name,
                "decision_note": decision.get("notes", "") if decision else "",
                "reviewer": decision.get("reviewer", "") if decision else "",
                "reviewed_at": decision.get("reviewed_at") if decision else None,
                "decision_stale": stale,
                "reasons": reasons,
                "reason_details": ai_review.get("blocking_reason_details", []),
                "course_count": len(rule.get("courses", [])),
                "review_course_count": sum(
                    bool(course.get("manual_review_required"))
                    for course in rule.get("courses", [])
                ),
                "course_group_count": len(rule.get("course_groups", [])),
                "review_course_group_count": sum(
                    bool(group.get("manual_review_required"))
                    for group in rule.get("course_groups", [])
                ),
                "manual_rule_count": len(rule.get("manual_review_rules", [])),
                "source_url": sources[0].get("url") if sources else None,
                "source_title": sources[0].get("title") if sources else None,
                "needs_official_evidence": any(
                    reason
                    in {
                        "course_table_unavailable",
                        "empty_course_table",
                        "missing_minimum_graduation_credits",
                    }
                    for reason in reasons
                ),
            }
        )
    return {
        "entry_year": entry_year,
        "generated_at": audit.get("reviewed_at"),
        "ruleset_sha256": audit.get("ruleset_sha256"),
        "total": len(departments),
        "counts": counts,
        "departments": departments,
    }


def graduation_department_payload(entry_year: str, department_code: str) -> dict:
    audit_path = ROOT / "reports" / f"graduation-ai-audit-{entry_year}.json"
    audit = load_json(audit_path)
    if not audit:
        raise FileNotFoundError(f"missing graduation audit report: {audit_path}")
    rule = load_json(graduation_rule_path(entry_year, department_code))
    if not rule:
        raise FileNotFoundError(department_code)
    decision = load_json(graduation_decision_path(entry_year, department_code))
    sources = rule.get("sources", [])
    return {
        "rule": rule,
        "decision": decision,
        "decision_stale": graduation_is_stale(decision, entry_year, rule, audit),
        "source_url": sources[0].get("url") if sources else None,
    }


def save_graduation_decision(entry_year: str, department_code: str, body: dict) -> dict:
    audit_path = ROOT / "reports" / f"graduation-ai-audit-{entry_year}.json"
    audit = load_json(audit_path)
    if not audit:
        raise FileNotFoundError(f"missing graduation audit report: {audit_path}")
    rule = load_json(graduation_rule_path(entry_year, department_code))
    if not rule:
        raise FileNotFoundError(department_code)

    decision = body.get("decision")
    if decision not in DECISIONS:
        raise ValueError("invalid decision")
    reviewer = str(body.get("reviewer") or "").strip()
    if not reviewer:
        raise ValueError("reviewer is required")
    notes = str(body.get("notes") or "").strip()
    if decision in {"approved", "cautious_use", "needs_fix"} and not notes:
        raise ValueError("notes are required for this decision")
    evidence_url = str(body.get("evidence_url") or "").strip()
    if evidence_url:
        parsed = urlparse(evidence_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("evidence URL must be an HTTP(S) URL")

    reasons = rule.get("ai_review", {}).get("blocking_reasons", [])
    source_missing = any(
        reason
        in {
            "course_table_unavailable",
            "empty_course_table",
            "missing_minimum_graduation_credits",
        }
        for reason in reasons
    )
    if decision == "approved" and source_missing and not evidence_url:
        raise ValueError("official evidence URL is required for missing source data")

    record = {
        "schema_version": 1,
        "entry_year": entry_year,
        "degree_level": "bachelor",
        "department_code": department_code,
        "department_name_zh": rule.get("department_name_zh"),
        "decision": decision,
        "reviewer": reviewer,
        "reviewed_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "notes": notes,
        "evidence_url": evidence_url or None,
        "based_on": graduation_source_snapshot(entry_year, rule, audit),
        "notice": (
            "Source-pinned first-pass evidence review. This record does not automatically "
            "change review_status or bypass schema and official-source verification."
        ),
    }
    write_json_atomic(graduation_decision_path(entry_year, department_code), record)
    return record


def current_source_snapshot(program: dict) -> dict:
    source = program.get("source", {})
    snapshot = {
        "pdf_binary_sha256": source.get("pdf_binary_sha256"),
        "normalized_text_sha256": source.get("normalized_text_sha256"),
        "selected_pdf_academic_version": program.get("selected_pdf_academic_version"),
        "parser_version": source.get("parser_version"),
    }
    if program.get("review", {}).get("override_path"):
        reviewed_output = json.dumps(
            {
                "course_catalog": program.get("course_catalog", []),
                "structured_requirements": program.get("structured_requirements", {}),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        snapshot["reviewed_output_sha256"] = hashlib.sha256(reviewed_output).hexdigest()
    return snapshot


def is_stale(decision: dict | None, program: dict) -> bool:
    return bool(decision and decision.get("based_on") != current_source_snapshot(program))


def queue_payload(version: str) -> dict:
    report_path = ROOT / "reports" / f"manual-review-{version}.json"
    report = load_json(report_path)
    if not report:
        raise FileNotFoundError(f"missing review report: {report_path}")

    programs = []
    counts = {decision: 0 for decision in DECISIONS}
    counts["unreviewed"] = 0
    counts["stale"] = 0
    for item in report.get("programs", []):
        pid = item["program_id"]
        program = load_json(program_path(version, pid), {})
        decision = load_json(decision_path(version, pid))
        stale = is_stale(decision, program)
        decision_name = decision.get("decision") if decision else None
        conflicts = program.get("structured_requirements", {}).get("source_conflicts", [])
        unresolved_conflicts = [
            conflict
            for conflict in conflicts
            if conflict.get("resolution_status", "unresolved") != "resolved"
        ]
        if stale:
            counts["stale"] += 1
        elif decision_name in DECISIONS:
            counts[decision_name] += 1
        else:
            counts["unreviewed"] += 1
        programs.append(
            {
                **item,
                "decision": decision_name,
                "decision_note": decision.get("notes", "") if decision else "",
                "reviewer": decision.get("reviewer", "") if decision else "",
                "reviewed_at": decision.get("reviewed_at") if decision else None,
                "decision_stale": stale,
                "conflict_count": len(unresolved_conflicts),
                "resolved_conflict_count": len(conflicts) - len(unresolved_conflicts),
                "course_count": len(program.get("course_catalog", [])),
            }
        )
    return {
        "academic_version": version,
        "generated_at": report.get("generated_at"),
        "total": len(programs),
        "counts": counts,
        "programs": programs,
    }


def export_review_results(version: str, output_path: Path) -> dict:
    """Export a source-pinned snapshot after every first-pass review is current."""
    queue = queue_payload(version)
    if queue["counts"]["unreviewed"] or queue["counts"]["stale"]:
        raise ValueError("cannot export incomplete or stale review decisions")

    decisions = []
    for item in sorted(queue["programs"], key=lambda value: value["program_id"]):
        decision = load_json(decision_path(version, item["program_id"]))
        if not decision:
            raise ValueError(f"missing decision for {item['program_id']}")
        decisions.append(
            {
                key: decision.get(key)
                for key in (
                    "program_id",
                    "program_name_zh",
                    "decision",
                    "reviewer",
                    "reviewed_at",
                    "notes",
                    "conflict_choices",
                    "based_on",
                )
            }
        )

    result = {
        "schema_version": 1,
        "academic_version": version,
        "review_scope": "first_pass",
        "formal_approval": False,
        "reviewed_at": max(item["reviewed_at"] for item in decisions),
        "total": queue["total"],
        "counts": queue["counts"],
        "notice": (
            "Source-pinned first-pass review results. These decisions do not replace "
            "the two-person formal approval required for review_status=approved."
        ),
        "decisions": decisions,
    }
    write_json_atomic(output_path, result)
    return result


def program_payload(version: str, program_id: str) -> dict:
    program = load_json(program_path(version, program_id))
    if not program:
        raise FileNotFoundError(program_id)
    decision = load_json(decision_path(version, program_id))
    pdf_hash = program.get("source", {}).get("pdf_binary_sha256")
    pdf_exists = bool(pdf_hash and (ROOT / "cache" / "pdf" / f"{pdf_hash}.pdf").is_file())
    return {
        "program": program,
        "decision": decision,
        "decision_stale": is_stale(decision, program),
        "pdf_local_url": f"/pdf/{pdf_hash}.pdf" if pdf_exists else None,
    }


def save_decision(version: str, program_id: str, body: dict) -> dict:
    program = load_json(program_path(version, program_id))
    if not program:
        raise FileNotFoundError(program_id)
    decision = body.get("decision")
    if decision not in DECISIONS:
        raise ValueError("invalid decision")
    reviewer = str(body.get("reviewer") or "").strip()
    if not reviewer:
        raise ValueError("reviewer is required")
    notes = str(body.get("notes") or "").strip()
    if decision in {"cautious_use", "needs_fix"} and not notes:
        raise ValueError("notes are required for this decision")

    conflicts = program.get("structured_requirements", {}).get("source_conflicts", [])
    valid_candidates = {
        conflict["conflict_id"]: {
            candidate["candidate_id"] for candidate in conflict.get("candidates", [])
        }
        for conflict in conflicts
    }
    conflict_choices = body.get("conflict_choices") or {}
    if not isinstance(conflict_choices, dict):
        raise ValueError("conflict_choices must be an object")
    for conflict_id, candidate_id in conflict_choices.items():
        if conflict_id not in valid_candidates or candidate_id not in valid_candidates[conflict_id]:
            raise ValueError("invalid conflict candidate")

    record = {
        "schema_version": 1,
        "academic_version": version,
        "program_id": program_id,
        "program_name_zh": program.get("name_zh"),
        "decision": decision,
        "reviewer": reviewer,
        "reviewed_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "notes": notes,
        "conflict_choices": conflict_choices,
        "based_on": current_source_snapshot(program),
        "notice": (
            "This is a first-pass review decision. It does not resolve source conflicts or "
            "constitute the two-person approved reviewed override."
        ),
    }
    write_json_atomic(decision_path(version, program_id), record)
    return record


class ReviewHandler(BaseHTTPRequestHandler):
    server_version = "NSYSUReview/1.0"

    def log_message(self, format: str, *args) -> None:
        sys.stderr.write("review-dashboard: " + format % args + "\n")

    def send_json(self, value, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, content_type: str | None = None) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        detected_type = mimetypes.guess_type(path.name)[0]
        response_type = content_type or detected_type or "application/octet-stream"
        self.send_header("Content-Type", response_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        version = query.get("version", ["115-1"])[0]
        try:
            if parsed.path == "/api/queue":
                self.send_json(queue_payload(version))
                return
            if parsed.path == "/api/graduation/queue":
                entry_year = query.get("entry_year", ["113"])[0]
                self.send_json(graduation_queue_payload(entry_year))
                return
            if parsed.path.startswith("/api/program/"):
                program_id = unquote(parsed.path.removeprefix("/api/program/"))
                self.send_json(program_payload(version, program_id))
                return
            if parsed.path.startswith("/api/graduation/department/"):
                department_code = unquote(
                    parsed.path.removeprefix("/api/graduation/department/")
                )
                entry_year = query.get("entry_year", ["113"])[0]
                self.send_json(graduation_department_payload(entry_year, department_code))
                return
            if parsed.path.startswith("/pdf/"):
                filename = unquote(parsed.path.removeprefix("/pdf/"))
                if not re.fullmatch(r"[0-9a-f]{64}\.pdf", filename):
                    raise ValueError("invalid PDF path")
                self.send_file(ROOT / "cache" / "pdf" / filename, "application/pdf")
                return
            if parsed.path in {"", "/"}:
                asset_name = (
                    "graduation.html"
                    if query.get("scope", [""])[0] == "graduation"
                    else "index.html"
                )
            else:
                asset_name = parsed.path.lstrip("/")
            if asset_name not in {
                "index.html",
                "app.js",
                "graduation.html",
                "graduation.js",
                "style.css",
            }:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_file(ASSET_ROOT / asset_name)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except FileNotFoundError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
        except Exception as exc:  # pragma: no cover - last-resort local UI guard
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        graduation_match = re.fullmatch(
            r"/api/graduation/decision/(B[0-9]{4})", parsed.path
        )
        if graduation_match:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BODY_SIZE:
                    raise ValueError("invalid request size")
                body = json.loads(self.rfile.read(length))
                entry_year = parse_qs(parsed.query).get("entry_year", ["113"])[0]
                self.send_json(
                    save_graduation_decision(
                        entry_year, graduation_match.group(1), body
                    )
                )
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except FileNotFoundError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            return
        match = re.fullmatch(r"/api/decision/(prog_[0-9a-f]{16})", parsed.path)
        if not match:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY_SIZE:
                raise ValueError("invalid request size")
            body = json.loads(self.rfile.read(length))
            version = parse_qs(parsed.query).get("version", ["115-1"])[0]
            self.send_json(save_decision(version, match.group(1), body))
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except FileNotFoundError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local NSYSU program review dashboard")
    parser.add_argument("--academic-version", default="115-1")
    parser.add_argument("--scope", choices=("program", "graduation"), default="program")
    parser.add_argument("--export-results", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    if args.export_results:
        version = validate_version(args.academic_version)
        output_path = ROOT / "reports" / f"manual-review-results-{version}.json"
        result = export_review_results(version, output_path)
        print(f"Exported {result['total']} decisions to {output_path}")
        return
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("review dashboard may only bind to localhost")
    server = ThreadingHTTPServer((args.host, args.port), ReviewHandler)
    suffix = "?scope=graduation" if args.scope == "graduation" else ""
    url = f"http://127.0.0.1:{server.server_port}/{suffix}"
    print(f"Review dashboard: {url}")
    print("Press Ctrl+C to stop.")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
