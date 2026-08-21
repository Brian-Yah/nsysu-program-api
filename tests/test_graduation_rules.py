from __future__ import annotations

import json
import shutil
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from nsysu_program_api.graduation_rule_fetch import parse_official_department_rule
from nsysu_program_api.graduation_rules import (
    build_graduation_rules_api,
    validate_common_references,
    validate_department_references,
)

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_113_DEPARTMENT_CODES = {
    "B1010",
    "B1020",
    "B1030",
    "B1060",
    "B2010",
    "B2020",
    "B2030",
    "B2040",
    "B3010",
    "B3020",
    "B3040",
    "B3080",
    "B3090",
    "B3100",
    "B3240",
    "B4010",
    "B4020",
    "B4030",
    "B4610",
    "B5020",
    "B5040",
    "B5090",
    "B5610",
    "B6060",
    "B6090",
    "B7020",
    "B7610",
    "B7620",
    "B8010",
    "B8060",
    "B8070",
}


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


def test_official_table_parser_builds_courses_groups_and_manual_rules() -> None:
    def row(values: list[str]) -> str:
        return "<tr>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>"

    empty_semesters = [""] * 12
    html = "<table>" + "".join(
        [
            row(["國立中山大學必修科目表（113學年度入學新生適用）"]),
            row(["系所別：測試學系"]),
            row(
                ["專<br>業<br>必<br>修", "一般必修", "程式設計", "3", *([""] * 11), "", "", ""]
            ),
            row(["資料結構", "", "", "", "3", *([""] * 8), "", "", ""]),
            row(["分組必修", "【A】：計 2 科任選 1 科"]),
            row(["人工智慧", *empty_semesters[:6], "3", *empty_semesters[7:], "A", "2", "1"]),
            row(["資料探勘", *empty_semesters[:7], "3", *empty_semesters[8:], "A", "2", "1"]),
            row(["最低畢<br>業學分數", "128", "必修比重", "50%"]),
            row(
                [
                    "修課<br>規定",
                    "1.通識教育課程必修28學分。2.專業必修科目計30學分。"
                    "3.本系選修課程至少修習24學分。4.國外同級學校畢業年級相當"
                    "國內高中二年級者應增加12學分。5.本系選修課程至少修習24學分。",
                ]
            ),
            row(["備註", "特殊資格須由系所人工審核。"]),
        ]
    ) + "</table>"

    rule = parse_official_department_rule(
        html,
        entry_year="113",
        department_code="B9990",
        department_name="測試學系",
        source_url="https://example.test/B9990",
        source_hash="a" * 64,
        reviewed_at="2026-08-21",
    )

    assert rule["credit_requirements"]["minimum_graduation_credits"] == 128
    assert rule["credit_requirements"]["minimum_department_professional_credits"] == 30
    assert rule["credit_requirements"]["minimum_department_elective_credits"] == 24
    assert len(rule["courses"]) == 4
    assert rule["courses"][0]["recommendedYear"] == 1
    assert rule["courses"][0]["recommendedSemester"] == "fall"
    assert rule["courses"][1]["recommendedYear"] == 2
    assert rule["course_groups"][0]["minimum_courses"] == 1
    assert len(rule["course_groups"][0]["course_ids"]) == 2
    assert rule["additional_credit_rules"][0]["additional_credits"] == 12
    assert any("特殊資格" in item["description"] for item in rule["manual_review_rules"])
    descriptions = [item["description"] for item in rule["manual_review_rules"]]
    assert not any("通識教育課程必修28學分" in value for value in descriptions)
    assert not any("應增加12學分" in value for value in descriptions)
    assert sum("本系選修課程至少修習24學分" in value for value in descriptions) == 1
    assert schema_errors("graduation-department-rule.schema.json", rule) == []
    assert validate_department_references(rule) == []


def test_zero_credit_graduation_condition_is_not_published_as_course_credit() -> None:
    def row(values: list[str]) -> str:
        return "<tr>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>"

    html = "<table>" + "".join(
        [
            row(["專<br>業<br>必<br>修", "", "畢業條件", "0", *([""] * 11), "", "", ""]),
            row(["最低畢業學分數", "128"]),
        ]
    ) + "</table>"
    rule = parse_official_department_rule(
        html,
        entry_year="113",
        department_code="B9999",
        department_name="測試學系",
        source_url="https://example.test/B9999",
        source_hash="a" * 64,
        reviewed_at="2026-08-21",
    )

    course = rule["courses"][0]
    assert course["credits"] is None
    assert course["manual_review_required"] is True
    assert any("表列為0學分" in note for note in course["notes"])


def test_unknown_minimum_is_allowed_only_for_partial_manual_rule() -> None:
    rule = load(ROOT / "data/graduation-rules/113/bachelor/B2040.json")
    rule["credit_requirements"]["minimum_graduation_credits"] = None
    rule["courses"] = []
    rule["course_groups"] = []
    assert schema_errors("graduation-department-rule.schema.json", rule) == []
    assert validate_department_references(rule) == []

    rule["manual_review_rules"] = []
    errors = validate_department_references(rule)
    assert "unknown minimum_graduation_credits requires partial manual review rules" in errors
    assert "empty course rules require partial manual review rules" in errors


def test_113_department_fixtures_cover_all_official_bachelor_codes() -> None:
    paths = sorted((ROOT / "data/graduation-rules/113/bachelor").glob("*.json"))
    rules = [load(path) for path in paths]

    assert len(rules) == 31
    assert {rule["department_code"] for rule in rules} == EXPECTED_113_DEPARTMENT_CODES
    for rule in rules:
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

    expected_count = len(
        list((tmp_path / "data/graduation-rules").glob("[0-9][0-9][0-9]/bachelor/*.json"))
    )
    assert index["department_count"] == expected_count
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
