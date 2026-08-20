from __future__ import annotations

import json
import shutil
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from nsysu_program_api.graduation_rules import (
    build_graduation_rules_api,
    validate_common_references,
    validate_department_references,
)

ROOT = Path(__file__).resolve().parents[1]


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def schema_errors(schema_name: str, instance: dict) -> list[str]:
    schema = load(ROOT / "schemas" / schema_name)
    return [
        error.message
        for error in Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
            instance
        )
    ]


def test_common_113_plus_fixture() -> None:
    common = load(ROOT / "data/graduation-rules/common/113-plus.json")
    assert schema_errors("graduation-common-rule.schema.json", common) == []
    assert validate_common_references(common) == []

    requirements = common["requirements"]
    assert requirements["language_literacy"]["chinese"]["credits"] == 3
    assert requirements["language_literacy"]["english"]["credits"] == 3
    assert requirements["language_literacy"]["eap_esp"]["minimum_courses"] == 1
    assert requirements["language_literacy"]["eap_esp"]["minimum_credits"] == 3
    assert requirements["cross_college"]["minimum_credits"] == 8
    assert requirements["cross_college"]["outside_home_college_required"] is True
    assert (
        requirements["cross_college"]["local_language_certification_maximum_exemption_credits"] == 2
    )
    assert requirements["liberal_arts"]["minimum_credits"] == 13
    assert requirements["liberal_arts"]["minimum_dimensions"] == 4
    assert requirements["liberal_arts"]["maximum_credits_counted_toward_minimum_graduation"] == 19
    assert requirements["experiential_courses"]["service_learning"]["required_credits"] == 1
    assert requirements["experiential_courses"]["university_way"]["required_events"] == 6
    assert requirements["sport_and_health"]["required_credits"] == 4
    assert sum(item["credits"] for item in requirements["sport_and_health"]["composition"]) == 4
    assert (
        requirements["sport_and_health"]["special_admission_compositions"][0]["required_credits"]
        == 8
    )
    assert requirements["english_proficiency_certification"]["required"] is True
    assert requirements["international_or_interdisciplinary_learning"]["required"] is True
    assert {item["category"] for item in requirements["credit_exclusions"]} == {
        "education_program",
        "sport",
        "military",
    }


def test_applied_math_113_fixture() -> None:
    rule = load(ROOT / "data/graduation-rules/113/bachelor/B2040.json")
    assert schema_errors("graduation-department-rule.schema.json", rule) == []
    assert validate_department_references(rule) == []
    assert rule["credit_requirements"]["minimum_graduation_credits"] == 128
    assert rule["credit_requirements"]["minimum_department_elective_credits"] is None
    choice = next(
        group for group in rule["course_groups"] if group["rule_kind"] == "choose_n_from_m"
    )
    assert choice["minimum_courses"] == 2
    assert len(choice["course_ids"]) == 3
    assert choice["maximum_courses"] is None
    assert rule["additional_credit_rules"][0]["additional_credits"] == 12
    unverified = [course for course in rule["courses"] if course["credits"] is None]
    assert unverified
    assert all(course["manual_review_required"] for course in unverified)


def test_ibba_113_fixture() -> None:
    rule = load(ROOT / "data/graduation-rules/113/bachelor/B4610.json")
    assert schema_errors("graduation-department-rule.schema.json", rule) == []
    assert validate_department_references(rule) == []
    assert rule["credit_requirements"] == {
        "minimum_graduation_credits": 128,
        "minimum_required_credits": 66,
        "minimum_elective_credits": 62,
        "minimum_department_elective_credits": None,
        "minimum_department_professional_credits": 38,
    }
    assert all(course["canonical_name_en"] for course in rule["courses"])
    choice = rule["course_groups"][0]
    assert (choice["minimum_courses"], len(choice["course_ids"])) == (3, 5)
    assert choice["minimum_credits"] == 9
    assert (
        next(
            group
            for group in rule["course_groups"]
            if group["group_id"] == "ibba_emi_liberal_arts_minimum"
        )["minimum_credits"]
        == 6
    )
    assert (
        next(
            group
            for group in rule["course_groups"]
            if group["group_id"] == "ibba_chinese_taught_elective_cap"
        )["maximum_counted_credits"]
        == 30
    )
    assert any("交換" in item["description"] for item in rule["manual_review_rules"])


def test_department_schema_supports_requested_relationships() -> None:
    rule = load(ROOT / "data/graduation-rules/113/bachelor/B4610.json")
    rule["courses"][0]["known_aliases"] = [{"name": "Calculus I", "language": "en"}]
    rule["courses"][0]["alternatives"] = [
        {
            "canonical_name_zh": "微積分甲",
            "canonical_name_en": "Calculus A",
            "known_aliases": [],
            "credits": 3,
            "source_document": "ibba-113",
            "approval_required": True,
            "notes": ["Schema capability fixture only."],
        }
    ]
    rule["course_groups"].append(
        {
            "group_id": "cross_category_fixture",
            "name_zh": "跨類別範例",
            "name_en": "Cross-category fixture",
            "rule_kind": "cross_category",
            "course_ids": ["ibba_calculus", "ibba_economics_1"],
            "minimum_courses": 2,
            "maximum_courses": None,
            "minimum_credits": 6,
            "category_requirements": [
                {"category": "quantitative", "minimum_courses": 1, "minimum_credits": 3},
                {"category": "economics", "minimum_courses": 1, "minimum_credits": 3},
            ],
            "counts_toward": "required",
            "source_document": "ibba-113",
            "manual_review_required": True,
            "notes": ["Schema capability fixture only."],
        }
    )
    rule["prerequisites"] = [
        {
            "course_id": "ibba_economics_2",
            "prerequisite_course_ids": ["ibba_economics_1"],
            "requirement": "required",
            "source_document": "ibba-113",
            "notes": [],
        }
    ]
    rule["non_duplicated_counting_groups"] = [
        {
            "group_id": "calculus_no_double_count",
            "course_ids": ["ibba_calculus", "ibba_economics_1"],
            "maximum_counted_courses": 1,
            "source_document": "ibba-113",
            "notes": ["Schema capability fixture only."],
        }
    ]
    assert schema_errors("graduation-department-rule.schema.json", rule) == []
    assert validate_department_references(rule) == []


def test_builder_publishes_static_paths_and_removes_stale_file(tmp_path: Path) -> None:
    for name in (
        "graduation-common-rule.schema.json",
        "graduation-department-rule.schema.json",
    ):
        destination = tmp_path / "schemas" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "schemas" / name, destination)
    shutil.copytree(ROOT / "data/graduation-rules", tmp_path / "data/graduation-rules")
    stale = tmp_path / "api/v1/graduation-rules/113/bachelor/STALE.json"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("{}", encoding="utf-8")

    index = build_graduation_rules_api(tmp_path)

    assert index["department_count"] == 2
    assert (tmp_path / "api/v1/graduation-rules/common/113-plus.json").exists()
    assert (tmp_path / "api/v1/graduation-rules/113/bachelor/B2040.json").exists()
    assert (tmp_path / "api/v1/graduation-rules/113/bachelor/B4610.json").exists()
    assert (tmp_path / "api/v1/graduation-rules/index.json").exists()
    assert not stale.exists()
    department_path = tmp_path / "api/v1/graduation-rules/113/bachelor/B2040.json"
    department = load(department_path)
    assert (department_path.parent / department["common_rule_ref"]).resolve() == (
        tmp_path / "api/v1/graduation-rules/common/113-plus.json"
    ).resolve()
