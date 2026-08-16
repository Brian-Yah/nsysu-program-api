#!/usr/bin/env python3
"""Materialize Brian's 115-1 review corrections as hash-pinned reviewed records."""

from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nsysu_program_api.reviewed import course_catalog_sha256  # noqa: E402

VERSION = "115-1"
NEEDS_FIX_IDS = {
    "prog_28d293ce79f652ec",
    "prog_5f523e6fcaf05e21",
    "prog_7189acd83f59530a",
    "prog_b078403838695b4c",
    "prog_b6ba18c54c2d55bc",
    "prog_d712dc5849f457ae",
    "prog_e0cebeb568e35264",
    "prog_e902d3dcf603529c",
    "prog_fd2710ede2ba5cfa",
}


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def stable_id(prefix: str, *parts: object) -> str:
    identity = "|".join(str(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(identity.encode()).hexdigest()[:16]}"


def course_by_name(catalog: list[dict], name: str) -> dict:
    matches = [item for item in catalog if item["course_name_snapshot"] == name]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one course named {name!r}, got {len(matches)}")
    return matches[0]


def add_course_equivalence(requirements: dict, courses: list[dict], source: str) -> None:
    names = list(dict.fromkeys(course["course_name_snapshot"] for course in courses))
    entry_ids = list(dict.fromkeys(course["catalog_entry_id"] for course in courses))
    constraint = {
            "constraint_id": stable_id("constraint", "course_equivalence", *names),
            "kind": "course_equivalence",
            "course_names": names,
            "min_courses": 1,
            "max_courses": 1,
            "requirement_group": courses[0]["requirement_group"],
            "requirement_section": courses[0]["requirement_section"],
            "source_page": min(course["source_page"] for course in courses),
            "source_text": source,
            "validation_status": "source_text_match",
    }
    if len(entry_ids) >= 2:
        constraint["catalog_entry_ids"] = entry_ids
    requirements.setdefault("course_count_constraints", []).append(constraint)


def add_entry_selection(
    requirements: dict,
    courses: list[dict],
    source: str,
    *,
    source_page: int | None = None,
) -> None:
    entry_ids = list(dict.fromkeys(course["catalog_entry_id"] for course in courses))
    names = [course["course_name_snapshot"] for course in courses]
    requirements.setdefault("entry_selection_constraints", []).append(
        {
            "constraint_id": stable_id("constraint", "select_entries", 1, *entry_ids),
            "kind": "select_entries",
            "catalog_entry_ids": entry_ids,
            "course_names": names,
            "min_entries": 1,
            "max_entries": 1,
            "declared_option_count": len(entry_ids),
            "option_count_matches": True,
            "requirement_group": courses[0]["requirement_group"],
            "requirement_section": courses[0]["requirement_section"],
            "source_page": source_page or min(course["source_page"] for course in courses),
            "source_text": source,
            "validation_status": "source_text_match",
        }
    )


def add_manual(
    requirements: dict,
    requirement_type: str,
    description: str,
    source_page: int,
    source_text: str,
) -> None:
    requirements.setdefault("manual_requirements", []).append(
        {
            "requirement_id": stable_id("requirement", requirement_type, source_text),
            "kind": "manual_verification",
            "requirement_type": requirement_type,
            "description": description,
            "verification_mode": "manual_review",
            "requirement_context": "program_completion",
            "source_page": source_page,
            "source_text": source_text,
            "validation_status": "human_verified",
        }
    )


def correct_financial_management(catalog: list[dict], requirements: dict) -> None:
    replacements = {
        "財務管理(一)/財務管理": ["財務管理(一)", "財務管理"],
        "財務管理(二)/財務管理理論": ["財務管理(二)", "財務管理理論"],
        "金融市場/金融市場理論與實務": ["金融市場", "金融市場理論與實務"],
    }
    expanded = []
    for course in catalog:
        names = replacements.get(course["course_name_snapshot"])
        if not names:
            expanded.append(course)
            continue
        alternatives = []
        for index, name in enumerate(names):
            item = deepcopy(course)
            item["catalog_entry_id"] = stable_id(
                "entry", course["catalog_entry_id"], name
            )
            item["course_name_snapshot"] = name
            if len(course["opening_units"]) == len(names):
                item["opening_units"] = [course["opening_units"][index]]
                item["opening_unit_snapshot"] = course["opening_units"][index]
            alternatives.append(item)
        expanded.extend(alternatives)
        add_course_equivalence(
            requirements,
            alternatives,
            course["course_name_snapshot"],
        )
    catalog[:] = expanded


def correct_smart_aging(requirements: dict) -> None:
    constraints = requirements.get("entry_selection_constraints", [])
    requirements["entry_selection_constraints"] = [
        item
        for item in constraints
        if set(item.get("course_names", [])) != {"智慧醫療與AI創新管理"}
    ]


def correct_biotechnology(catalog: list[dict], requirements: dict) -> None:
    biotechnology_units = {
        "生物技術概論": "生科系",
        "生物技術": "海資系",
    }
    for course in catalog:
        name = course["course_name_snapshot"]
        if name not in biotechnology_units:
            continue
        unit = biotechnology_units[name]
        course["catalog_entry_id"] = stable_id(
            "entry", course["catalog_entry_id"], unit, name
        )
        course["opening_units"] = [unit]
        course["opening_unit_snapshot"] = unit

    pair_names = list(biotechnology_units)
    requirements["course_count_constraints"] = [
        constraint
        for constraint in requirements.get("course_count_constraints", [])
        if set(constraint.get("course_names", [])) != set(pair_names)
    ]
    add_entry_selection(
        requirements,
        [course_by_name(catalog, name) for name in pair_names],
        "生科系 生物技術概論 3；海資系 生物技術 3，二擇一",
        source_page=1,
    )

    bioinformatics_names = ["生物資訊學概論(大學部)", "生物資訊學及其應用", "生物資訊學"]
    add_entry_selection(
        requirements,
        [course_by_name(catalog, name) for name in bioinformatics_names],
        "生物資訊學概論(大學部)、生物資訊學及其應用、生物資訊學，三擇一",
        source_page=2,
    )

    signaling_names = [
        "訊息傳遞與藥物開發概論",
        "細胞訊息傳遞學概論",
        "細胞訊息傳遞與醫藥應用",
    ]
    add_entry_selection(
        requirements,
        [course_by_name(catalog, name) for name in signaling_names],
        "訊息傳遞與藥物開發概論、細胞訊息傳遞學概論、"
        "細胞訊息傳遞與醫藥應用，三擇一",
        source_page=2,
    )
    english = course_by_name(catalog, "分子細胞生物學")
    add_manual(
        requirements,
        "course_eligibility",
        "分子細胞生物學僅限英語授課之課程得採計。",
        english["source_page"],
        "海科系(碩) 分子細胞生物學 3 (英語授課)",
    )


def correct_green_port(catalog: list[dict], requirements: dict) -> None:
    courses = [
        course_by_name(catalog, "綠色港市與智慧港埠"),
        course_by_name(catalog, "海洋與海岸管理"),
    ]
    add_entry_selection(
        requirements,
        courses,
        "人科學程 綠色港市與智慧港埠 3；海工系 海洋與海岸管理 3，二選一",
        source_page=1,
    )


def correct_financial_engineering(requirements: dict) -> None:
    requirements["manual_requirements"] = []
    add_manual(
        requirements,
        "prerequisite",
        "修畢微積分 3 學分且成績及格，方得申請修讀本學程。",
        6,
        "修畢微積分(3 學分)且成績及格者,方得申請修讀本學程。",
    )
    add_manual(
        requirements,
        "student_condition",
        "一般學生至少 9 學分須為非屬原主修系所之課程；"
        "雙主修或輔系學生則至少 6 學分不得屬於原主修、雙主修或輔系。",
        6,
        "其中至少 9 學分須為非屬學生原主修系所之課程;"
        "若為雙主修或輔系學生,則至少應修滿 6 學分之課程"
        "不得屬於其原主修系所、雙主修或輔系。",
    )


def correct_smart_finance(catalog: list[dict], requirements: dict) -> None:
    if not any(item["course_name_snapshot"] == "商管軟體設計" for item in catalog):
        template = course_by_name(catalog, "金融數據分析")
        missing = deepcopy(template)
        missing.update(
            {
                "catalog_entry_id": stable_id(
                    "entry", "prog_d712dc5849f457ae", "管理學院", "商管軟體設計", 3
                ),
                "course_name_snapshot": "商管軟體設計",
                "credits_snapshot": 3.0,
                "notes": "必修課",
                "source_page": 1,
                "evidence_match": True,
            }
        )
        insert_at = next(
            index
            for index, course in enumerate(catalog)
            if course["course_name_snapshot"] == "金融數據分析"
        ) + 1
        catalog.insert(insert_at, missing)
    conflicts = requirements.get("source_conflicts", [])
    for conflict in conflicts:
        if conflict["semantic_key"] != "program.minimum_total_credits":
            continue
        selected = next(
            candidate for candidate in conflict["candidates"] if candidate["value"] == 11
        )
        conflict["resolution_status"] = "resolved"
        conflict["selected_candidate_id"] = selected["candidate_id"]
        conflict["resolution_note"] = (
            "Human review selected 11 credits: the same page explicitly states that a "
            "micro-program "
            "curriculum requires at least 11 credits."
        )
    requirements["minimum_total_credits"] = 11.0
    requirements.setdefault("credit_constraints", []).append(
        {
            "constraint_id": stable_id("constraint", "minimum_credits", "program", 11),
            "kind": "minimum_credits",
            "scope": {"kind": "program"},
            "minimum_credits": 11.0,
            "requirement_context": "program_completion",
            "source_page": 1,
            "source_text": "※【微學程】課程規劃至少 11 學分。",
            "validation_status": "human_verified",
        }
    )


def correct_marine_bioresources(catalog: list[dict], requirements: dict) -> None:
    alternatives = {
        "分子生物學": ("生科系", "分子生物學"),
        "生物技術": ("生科系", "生物技術概論"),
        "訊息傳遞與藥物開發": ("生科系", "細胞訊息傳遞學概論"),
        "海洋生物生理學": ("生科系", "動物生理學"),
    }
    expanded = []
    for course in catalog:
        expanded.append(course)
        alternate = alternatives.get(course["course_name_snapshot"])
        if not alternate:
            continue
        other = deepcopy(course)
        other["catalog_entry_id"] = stable_id(
            "entry", course["catalog_entry_id"], alternate[0], alternate[1]
        )
        other["opening_units"] = [alternate[0]]
        other["opening_unit_snapshot"] = alternate[0]
        other["course_name_snapshot"] = alternate[1]
        other["notes"] = f"可替代 {course['course_name_snapshot']}"
        expanded.append(other)
        add_course_equivalence(
            requirements,
            [course, other],
            course.get("notes") or f"{alternate[0]}{alternate[1]}亦可",
        )
    catalog[:] = expanded


def correct_performing_arts(catalog: list[dict], requirements: dict) -> None:
    replacements = {
        "戲劇構 作實踐:古典": "戲劇構作實踐:古典",
        "戲劇構作實踐:中 古與近代": "戲劇構作實踐:中古與近代",
        "戲劇構作實踐:現 當代": "戲劇構作實踐:現當代",
    }
    for course in catalog:
        course["course_name_snapshot"] = replacements.get(
            course["course_name_snapshot"], course["course_name_snapshot"]
        )
    practice_names = {
        "戲劇構作實踐:古典",
        "戲劇構作實踐:中古與近代",
        "戲劇構作實踐:現當代",
    }
    requirements["entry_selection_constraints"] = [
        item
        for item in requirements.get("entry_selection_constraints", [])
        if set(item.get("course_names", [])) != practice_names
    ]
    requirements["course_count_constraints"] = [
        item
        for item in requirements.get("course_count_constraints", [])
        if set(item.get("course_names", [])) != practice_names
    ]


def correct_silicon_photonics(catalog: list[dict], requirements: dict) -> None:
    source = "各課程屬性僅採計3學分"
    for subject in ("矽光子原理", "矽光子元件", "矽光子量測"):
        courses = [item for item in catalog if item.get("program_course_name_snapshot") == subject]
        requirements.setdefault("course_count_constraints", []).append(
            {
                "constraint_id": stable_id(
                    "constraint", "program_course_equivalence", subject, *[
                        item["catalog_entry_id"] for item in courses
                    ]
                ),
                "kind": "program_course_equivalence",
                "catalog_entry_ids": list(
                    dict.fromkeys(item["catalog_entry_id"] for item in courses)
                ),
                "course_names": list(
                    dict.fromkeys(item["course_name_snapshot"] for item in courses)
                ),
                "min_courses": 1,
                "max_courses": 1,
                "requirement_group": "core",
                "requirement_section": "core",
                "program_course_name_snapshot": subject,
                "source_page": 1,
                "source_text": source,
                "validation_status": "source_text_match",
            }
        )


CORRECTIONS = {
    "prog_28d293ce79f652ec": lambda c, r: correct_financial_management(c, r),
    "prog_5f523e6fcaf05e21": lambda c, r: correct_smart_aging(r),
    "prog_7189acd83f59530a": correct_biotechnology,
    "prog_b078403838695b4c": correct_green_port,
    "prog_b6ba18c54c2d55bc": lambda c, r: correct_financial_engineering(r),
    "prog_d712dc5849f457ae": correct_smart_finance,
    "prog_e0cebeb568e35264": correct_marine_bioresources,
    "prog_e902d3dcf603529c": correct_performing_arts,
    "prog_fd2710ede2ba5cfa": correct_silicon_photonics,
}


def main() -> None:
    decision_root = ROOT / "data" / "review-decisions" / VERSION
    output_root = ROOT / "data" / "reviewed" / VERSION
    decisions = {path.stem: load_json(path) for path in decision_root.glob("*.json")}
    if set(CORRECTIONS) != NEEDS_FIX_IDS:
        raise RuntimeError("correction registry does not cover the needs-fix set")
    for program_id, correction in CORRECTIONS.items():
        decision = decisions.get(program_id)
        if not decision:
            raise RuntimeError(f"missing review decision for {program_id}")
        program = load_json(ROOT / "data" / "published" / VERSION / f"{program_id}.json")
        extracted = load_json(ROOT / "data" / "extracted" / VERSION / f"{program_id}.json")
        original_catalog = deepcopy(extracted["structured_courses"])
        catalog = deepcopy(original_catalog)
        requirements = deepcopy(extracted["structured_requirements"])
        correction(catalog, requirements)
        requirements.pop("completion_summary", None)
        override = {
            "schema_version": 1,
            "academic_version": VERSION,
            "program_id": program_id,
            "based_on": {
                "pdf_binary_sha256": program["source"]["pdf_binary_sha256"],
                "normalized_text_sha256": program["source"]["normalized_text_sha256"],
                "selected_pdf_academic_version": program["selected_pdf_academic_version"],
                "parser_version": program["source"]["parser_version"],
                "course_catalog_sha256": course_catalog_sha256(original_catalog),
            },
            "course_catalog": catalog,
            "structured_requirements": requirements,
            "review_status": "needs_review",
            "review": {
                "reviewer": decision["reviewer"],
                "reviewed_at": decision["reviewed_at"],
                "review_note": decision["notes"],
                "correction_status": (
                    "rechecked"
                    if decision["decision"] == "approved"
                    else f"rechecked_{decision['decision']}"
                ),
            },
        }
        write_json(output_root / f"{program_id}.json", override)
        print(f"materialized {program_id}")


if __name__ == "__main__":
    main()
