#!/usr/bin/env python3
"""Find selected-version PDF rule phrases without corresponding structured evidence."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

HEADER = re.compile(r"【\s*(?P<year>\d{3})\s*學年度第\s*(?P<term>[12])\s*學期起適用\s*】")
MARKERS = {
    "selection": re.compile(
        r"(?:[一二三四五六七八九十兩\d]+\s*[擇選]\s*"
        r"[一二三四五六七八九十兩\d]+|任選.{0,12}?門|"
        r"至少(?:選修|修習|修畢).{0,12}?門|必修.{0,12}?門)"
    ),
    "credit_cap": re.compile(r"(?:至多|最多|上限|僅採計).{0,20}?(?:學分|門|科)"),
    "no_double_count": re.compile(
        r"(?:不得|不可|不再|勿再).{0,8}?重複.{0,12}?(?:採計|認列|抵免|計算|修習)|"
        r"至多採認一科|為相同課程.{0,20}?其中一門"
    ),
    "overflow": re.compile(
        r"(?:其餘|其他|超出|多餘|可多修).{0,35}?"
        r"(?:計入|列入|納入|採計).{0,15}?(?:選修|總學分)"
    ),
    "manual_condition": re.compile(
        r"(?:方得|須經|需經|先修|不採計|只限|僅限|申請.{0,20}?證書|"
        r"學分證明|認證)"
    ),
    "cohort_exception": re.compile(r"(?:入學前|已取得者|已核准修習|以前學生|學年度以前|學期以前)"),
}
COLLECTIONS = {
    "selection": (
        "entry_selection_constraints",
        "course_count_constraints",
        "program_course_selection_constraints",
        "named_group_selection_constraints",
    ),
    "credit_cap": (
        "credit_constraints",
        "entry_selection_constraints",
        "course_count_constraints",
        "program_course_selection_constraints",
    ),
    "no_double_count": (
        "credit_constraints",
        "course_count_constraints",
        "no_double_count_constraints",
    ),
    "overflow": ("credit_constraints", "entry_selection_constraints"),
    "manual_condition": ("manual_requirements",),
    "cohort_exception": ("manual_requirements",),
}


def compact(value: str) -> str:
    return re.sub(r"[\s，,。；;:：()（）「」『』]+", "", unicodedata.normalize("NFKC", value))


def selected_text(raw: str, selected_version: str | None) -> str:
    if not selected_version:
        return raw
    matches = list(HEADER.finditer(raw))
    for index, match in enumerate(matches):
        version = f"{match.group('year')}-{match.group('term')}"
        if version != selected_version:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        return raw[match.start() : end]
    return raw


def evidence_texts(requirements: dict, category: str) -> list[str]:
    values = []
    for collection in COLLECTIONS[category]:
        for item in requirements.get(collection, []):
            text = compact(item.get("source_text", ""))
            if text:
                values.append(text)
    return values


def is_covered(snippet: str, evidence: list[str]) -> bool:
    target = compact(snippet)
    for source in evidence:
        if source in target or target in source:
            return True
        # PDF table and plain-text extractors wrap cells differently. A stable
        # 12-character overlap around the marker is sufficient to link evidence.
        if len(source) >= 12 and any(source[i : i + 12] in target for i in range(len(source) - 11)):
            return True
        if len(target) >= 4 and target in source:
            return True
    return False


def snippets(text: str, pattern: re.Pattern[str]) -> list[str]:
    results = []
    normalized = compact(text)
    for match in pattern.finditer(normalized):
        value = match.group(0)
        if "認證核心科目" in normalized[match.start() : match.end() + 8]:
            continue
        results.append(value)
    return list(dict.fromkeys(results))


def audit(root: Path, version: str) -> dict:
    findings = []
    extracted_root = root / "data" / "extracted" / version
    published_root = root / "data" / "published" / version
    policy = json.loads(
        (root / "data" / "policies" / "program-requirements.json").read_text(encoding="utf-8")
    )
    administrative_evidence = [
        compact(item.get("description", "")) for item in policy.get("administrative_rules", [])
    ]
    for path in sorted(extracted_root.glob("prog_*.json")):
        extracted = json.loads(path.read_text(encoding="utf-8"))
        published = json.loads((published_root / path.name).read_text(encoding="utf-8"))
        text = selected_text(
            extracted.get("raw_text", ""), extracted.get("selected_pdf_academic_version")
        )
        requirements = published.get("structured_requirements", {})
        for category, pattern in MARKERS.items():
            evidence = evidence_texts(requirements, category)
            if category in {"manual_condition", "cohort_exception"}:
                evidence.extend(administrative_evidence)
            # This audit is a category coverage gate. Detailed option counts,
            # entry references, duplicate semantics and limits are validated by
            # audit_program_rules.py and scripts/validate.py.
            if evidence:
                continue
            for snippet in snippets(text, pattern):
                if is_covered(snippet, evidence):
                    continue
                findings.append(
                    {
                        "program_id": published["program_id"],
                        "name_zh": published["name_zh"],
                        "category": category,
                        "snippet": snippet,
                    }
                )
    # Nearby repeated course notes can produce the same uncovered phrase.
    unique = {(item["program_id"], item["category"], item["snippet"]): item for item in findings}
    findings = list(unique.values())
    return {
        "schema_version": 1,
        "academic_version": version,
        "program_count": len(list(published_root.glob("prog_*.json"))),
        "finding_count": len(findings),
        "counts": dict(sorted(Counter(item["category"] for item in findings).items())),
        "findings": findings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--academic-version", default="115-1")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = audit(args.root.resolve(), args.academic_version)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {key: report[key] for key in ("program_count", "finding_count", "counts")},
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.strict and report["finding_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
