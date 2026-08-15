from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .core import load_json, write_json
from .evaluator import evaluate
from .graduation import build_graduation_api, fetch_graduation_requirements
from .pipeline import build_api, fetch_catalog, process_pdfs, semantic_diff


def main() -> None:
    parser = argparse.ArgumentParser(prog="nsysu-program-api")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--academic-version", default="115-1")
    parser.add_argument("--entry-year", default="115")
    parser.add_argument(
        "--user-agent",
        default=os.getenv(
            "NSYSU_API_USER_AGENT",
            "nsysu-program-api/0.2.2 (+https://github.com/Brian-Yah/nsysu-program-api)",
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch")
    sub.add_parser("full")
    sub.add_parser("extract-cache")
    sub.add_parser("build")
    sub.add_parser("graduation-fetch")
    sub.add_parser("graduation-build")
    diff = sub.add_parser("diff")
    diff.add_argument("old", type=Path)
    diff.add_argument("new", type=Path)
    diff.add_argument("--output", type=Path)
    ev = sub.add_parser("evaluate")
    ev.add_argument("program", type=Path)
    ev.add_argument("student", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.command in {"fetch", "full"}:
        catalog = fetch_catalog(root, args.academic_version, args.user_agent)
        result = {"catalog_total": len(catalog["programs"])}
        if args.command == "full":
            result = process_pdfs(root, catalog, args.user_agent)
            result["manifest"] = build_api(root, args.academic_version)
    elif args.command == "extract-cache":
        catalog = load_json(root / "data" / "source" / args.academic_version / "catalog.json", {})
        result = process_pdfs(root, catalog, args.user_agent, reuse_cache=True)
        result["manifest"] = build_api(root, args.academic_version)
    elif args.command == "build":
        result = build_api(root, args.academic_version)
    elif args.command == "graduation-fetch":
        dataset = fetch_graduation_requirements(root, args.entry_year, args.user_agent)
        index = build_graduation_api(root, args.entry_year)
        result = {
            "entry_academic_year": args.entry_year,
            "department_count": dataset["department_count"],
            "unavailable_count": len(dataset["unavailable_departments"]),
            "api": index,
        }
    elif args.command == "graduation-build":
        result = build_graduation_api(root, args.entry_year)
    elif args.command == "diff":
        result = semantic_diff(load_json(args.old, {}), load_json(args.new, {}))
        if args.output:
            write_json(args.output, result)
    else:
        program, student = load_json(args.program, {}), load_json(args.student, {})
        result = evaluate(program["rules"], student)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
