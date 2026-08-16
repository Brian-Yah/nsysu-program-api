from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLISHED = ROOT / "data" / "published" / "115-1"


def load(program_id: str) -> dict:
    return json.loads((PUBLISHED / f"{program_id}.json").read_text(encoding="utf-8"))


def maximum_constraints(program: dict) -> list[dict]:
    return [
        item
        for item in program["structured_requirements"].get("credit_constraints", [])
        if item["kind"] == "maximum_counted_credits"
    ]


def test_all_published_models_are_internally_referenced_and_current() -> None:
    paths = list(PUBLISHED.glob("*.json"))
    assert len(paths) == 142
    for path in paths:
        program = json.loads(path.read_text(encoding="utf-8"))
        assert program["source"]["parser_version"] == "0.3.0"
        assert not any(
            "No structured course rows extracted" in warning
            for warning in program.get("warnings", [])
        )
        requirements = program["structured_requirements"]
        summary = requirements["completion_summary"]
        total_conflict = any(
            item["resolution_status"] == "unresolved"
            and item["semantic_key"] == "program.minimum_total_credits"
            for item in requirements.get("source_conflicts", [])
        )
        assert summary["minimum_total_credits"] == (
            None if total_conflict else requirements.get("minimum_total_credits")
        )
        assert summary["minimum_core_credits"] == requirements.get(
            "minimum_core_credits"
        )
        entry_ids = {item["catalog_entry_id"] for item in program["course_catalog"]}
        for constraint in requirements.get("entry_selection_constraints", []):
            assert set(constraint["catalog_entry_ids"]) <= entry_ids
            assert constraint["min_entries"] <= len(constraint["catalog_entry_ids"])


def test_ai_approved_programs_are_simple_and_conflict_free() -> None:
    programs = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in PUBLISHED.glob("*.json")
    ]
    ai_approved = [
        program for program in programs if program["review_status"] == "ai_approved"
    ]
    assert len(ai_approved) == 79
    forbidden = (
        "entry_selection_constraints",
        "course_count_constraints",
        "program_course_selection_constraints",
        "named_group_selection_constraints",
        "no_double_count_constraints",
        "manual_requirements",
        "source_conflicts",
    )
    for program in ai_approved:
        requirements = program["structured_requirements"]
        assert requirements["completion_summary"]["model_status"] == "complete"
        assert not any(requirements.get(key) for key in forbidden)


def test_table_geometry_and_label_regressions_are_fixed() -> None:
    expected_counts = {
        "prog_8f479dc02d095c9a": 78,
        "prog_e7809c397f60506f": 31,
        "prog_1c3832a46db355e9": 32,
        "prog_e8035437705e553e": 39,
        "prog_fd2710ede2ba5cfa": 27,
        "prog_58764eb1bb735197": 37,
    }
    for program_id, minimum in expected_counts.items():
        assert len(load(program_id)["course_catalog"]) >= minimum

    for path in PUBLISHED.glob("*.json"):
        program = json.loads(path.read_text(encoding="utf-8"))
        labels = {
            item.get("requirement_label") or "" for item in program["course_catalog"]
        }
        assert not any(
            token in label
            for label in labels
            for token in ("植物分類", "魚類分類", "醫學科")
        )

    western = load("prog_201d87e139c750d0")
    assert sum(c["requirement_group"] == "core" for c in western["course_catalog"]) == 8
    assert (
        sum(c["requirement_group"] == "elective" for c in western["course_catalog"])
        == 58
    )

    english = load("prog_51f6d4cadcc65af9")
    service_ids = {
        item["catalog_entry_id"]
        for item in english["course_catalog"]
        if item.get("requirement_label") == "服務學習課程"
    }
    assert len(service_ids) == 5
    assert any(
        set(item["catalog_entry_ids"]) == service_ids and item["min_entries"] == 1
        for item in english["structured_requirements"]["entry_selection_constraints"]
    )


def test_known_credit_caps_and_overflow_rules_are_published() -> None:
    expected_caps = {
        "prog_08e51873a84951de": ("course_attributes", 1),
        "prog_4795cead391b5d2c": ("course_attributes", 2),
        "prog_6cd8c9bf1c955907": ("course_attributes", 2),
        "prog_84014e6da4cd5bb5": ("course_attributes", 2),
        "prog_9c8e67bbb0db56d7": ("opening_units", 3),
        "prog_b0535aebb56b5535": ("course_attributes", 6),
    }
    for program_id, (scope_kind, maximum) in expected_caps.items():
        assert any(
            item["scope"]["kind"] == scope_kind
            and item["maximum_counted_credits"] == maximum
            for item in maximum_constraints(load(program_id))
        )

    silicon = maximum_constraints(load("prog_fd2710ede2ba5cfa"))
    assert len(
        [item for item in silicon if item["scope"]["kind"] == "program_course_names"]
    ) == 3

    japanese = maximum_constraints(load("prog_8f479dc02d095c9a"))
    assert any(
        item["scope"] == {"kind": "catalog_filter", "requirement_groups": ["core"]}
        and item["maximum_counted_credits"] == 12
        and item["excess_credit_destination"] == "elective"
        for item in japanese
    )
    assert len(
        [
            item
            for item in japanese
            if item["scope"]["kind"] == "catalog_entries"
            and item.get("excess_credit_destination") == "elective"
        ]
    ) == 2

    for program_id, destination, counted in (
        ("prog_58f7156f3746514c", "program_total", 1),
        ("prog_e2622058c0055c4b", "program_total", 1),
        ("prog_ae43d8508d255ad0", "program_total", 2),
    ):
        constraints = load(program_id)["structured_requirements"].get(
            "entry_selection_constraints", []
        )
        assert any(
            item.get("excess_credit_destination") == destination
            and item.get("max_entries_counted_for_requirement") == counted
            for item in constraints
        )


def test_complex_completion_models_and_official_conflicts_are_explicit() -> None:
    for program_id in (
        "prog_9da001957107569d",
        "prog_a6cb5946f4a35ace",
        "prog_f23e55dec5f55c18",
    ):
        requirements = load(program_id)["structured_requirements"]
        assert requirements["minimum_core_credits"] == 24
        assert requirements["minimum_elective_credits"] == 9
        assert requirements["minimum_total_credits"] == 33

    ocean = load("prog_4fc4ae1446385354")["structured_requirements"]
    assert (ocean["minimum_core_credits"], ocean["maximum_core_credits"]) == (7, 10)
    labels = {
        item.get("requirement_label"): item
        for item in ocean["entry_selection_constraints"]
    }
    assert {"核心課程I", "核心課程II", "核心課程III", "總結性課程"} <= labels.keys()
    assert labels["核心課程I"]["excess_credit_destination"] == "elective"
    assert labels["核心課程II"]["excess_credit_destination"] == "elective"

    biodiversity = load("prog_7adfffe5c6f6544b")["structured_requirements"]
    core_two = next(
        item
        for item in biodiversity["entry_selection_constraints"]
        if item.get("requirement_label") == "核心課程II"
    )
    assert core_two["minimum_credits_for_requirement"] == 2
    assert core_two["excess_credit_destination"] == "elective"

    conflict_keys = []
    for path in PUBLISHED.glob("*.json"):
        program = json.loads(path.read_text(encoding="utf-8"))
        conflict_keys.extend(
            item["semantic_key"]
            for item in program["structured_requirements"].get("source_conflicts", [])
        )
    assert len(conflict_keys) == 3
    assert conflict_keys.count("program.minimum_total_credits") == 1
    assert sum(key.endswith(".option_count") for key in conflict_keys) == 2


def test_conditional_and_certificate_rules_are_not_silently_dropped() -> None:
    practical = load("prog_58764eb1bb735197")
    pair_caps = [
        item
        for item in maximum_constraints(practical)
        if item["scope"]["kind"] == "catalog_entries"
    ]
    assert len(pair_caps) == 6
    assert all(item["maximum_counted_credits"] == 3 for item in pair_caps)

    for program_id in ("prog_51f6d4cadcc65af9", "prog_58764eb1bb735197"):
        manual = load(program_id)["structured_requirements"].get(
            "manual_requirements", []
        )
        assert any(item["requirement_type"] == "course_eligibility" for item in manual)

    human_rights = load("prog_14550fb9cc885968")["structured_requirements"][
        "manual_requirements"
    ]
    certificate = [
        item for item in human_rights if item["requirement_context"] == "certificate"
    ]
    assert any(item.get("minimum_count") == 5 for item in certificate)
    assert any(item.get("minimum_hours") == 15 for item in certificate)
    assert any(item["requirement_type"] == "report" for item in certificate)

    natural = load("prog_b5e32570efc25c18")["structured_requirements"][
        "manual_requirements"
    ]
    assert any("移除" in item["description"] for item in natural)
    assert any("選修學分" in item["description"] for item in natural)

    finance = load("prog_6563ee7be1805b86")["structured_requirements"][
        "manual_requirements"
    ]
    assert any(item["requirement_type"] == "credit_cap" for item in finance)
    assert any(item["requirement_type"] == "course_eligibility" for item in finance)


def test_taica_national_defense_and_csr_models_are_complete() -> None:
    taica_ids = (
        "prog_190e05feb0e25908",
        "prog_38fa876303d75d77",
        "prog_3d5388114d795ef4",
        "prog_6fe63fd4a2735877",
        "prog_b27037639ff15338",
    )
    for program_id in taica_ids:
        requirements = load(program_id)["structured_requirements"]
        selections = requirements["entry_selection_constraints"]
        assert len(selections) == 5
        assert all(
            item["min_entries"] == 1 and item.get("max_entries") is None
            for item in selections
        )
        constraints = requirements["credit_constraints"]
        expected = {
            ("program", "minimum_credits", 15),
            ("course_eligibility", "minimum_credits", 9),
            ("taica_courses", "minimum_credits", 8),
            ("recognized_similar_courses", "maximum_counted_credits", 3),
            ("cross_program_recognition", "maximum_counted_credits", 6),
        }
        actual = {
            (
                item["scope"]["kind"],
                item["kind"],
                item.get("minimum_credits", item.get("maximum_counted_credits")),
            )
            for item in constraints
        }
        assert expected <= actual

    defense = load("prog_4795cead391b5d2c")["structured_requirements"]
    assert defense["minimum_core_credits"] == 4
    assert defense["minimum_total_credits"] == 9
    selections = defense["entry_selection_constraints"]
    assert len(selections) == 2
    assert all(
        item["min_entries"] == 1 and item.get("max_entries") is None
        for item in selections
    )

    for program_id, total, core, outside in (
        ("prog_3b2f1e51c2055242", 15, 6, 6),
        ("prog_979e5a760ec15ab2", 9, 4, 3),
    ):
        requirements = load(program_id)["structured_requirements"]
        assert requirements["minimum_total_credits"] == total
        assert requirements["minimum_core_credits"] == core
        assert any(
            item["scope"]["kind"] == "course_eligibility"
            and item.get("minimum_credits") == outside
            for item in requirements["credit_constraints"]
        )


def test_brian_review_corrections_are_materialized() -> None:
    financial_management = load("prog_28d293ce79f652ec")
    financial_names = {
        item["course_name_snapshot"] for item in financial_management["course_catalog"]
    }
    assert {"財務管理(一)", "財務管理", "財務管理(二)", "財務管理理論"} <= financial_names
    assert all("/" not in name for name in financial_names)

    smart_aging = load("prog_5f523e6fcaf05e21")["structured_requirements"]
    assert [
        item["course_names"] for item in smart_aging["entry_selection_constraints"]
    ] == [["醫療與護理資訊", "智慧醫療與AI創新管理"]]

    biotechnology = load("prog_7189acd83f59530a")["structured_requirements"]
    expected_biotechnology_groups = [
        ["生物技術概論", "生物技術"],
        ["生物資訊學概論(大學部)", "生物資訊學及其應用", "生物資訊學"],
        ["訊息傳遞與藥物開發概論", "細胞訊息傳遞學概論", "細胞訊息傳遞與醫藥應用"],
    ]
    for expected_group in expected_biotechnology_groups:
        assert any(
            item["course_names"] == expected_group
            and item["min_entries"] == item["max_entries"] == 1
            and len(item["catalog_entry_ids"]) == len(expected_group)
            for item in biotechnology["entry_selection_constraints"]
        )
    assert any(
        item["requirement_type"] == "course_eligibility"
        and "英語授課" in item["description"]
        for item in biotechnology["manual_requirements"]
    )
    biotechnology_catalog = load("prog_7189acd83f59530a")["course_catalog"]
    biotechnology_courses = {
        item["course_name_snapshot"]: item
        for item in biotechnology_catalog
        if item["course_name_snapshot"] in {"生物技術概論", "生物技術"}
    }
    assert biotechnology_courses["生物技術概論"]["opening_units"] == ["生科系"]
    assert biotechnology_courses["生物技術"]["opening_units"] == ["海資系"]
    assert len(
        {item["catalog_entry_id"] for item in biotechnology_courses.values()}
    ) == 2

    green_port = load("prog_b078403838695b4c")["structured_requirements"]
    assert any(
        item["course_names"] == ["綠色港市與智慧港埠", "海洋與海岸管理"]
        for item in green_port["entry_selection_constraints"]
    )

    financial_engineering = load("prog_b6ba18c54c2d55bc")["structured_requirements"]
    manual_text = " ".join(
        item["description"] for item in financial_engineering["manual_requirements"]
    )
    assert "方得申請修讀本學程" in manual_text
    assert "雙主修或輔系學生" in manual_text and "至少 6 學分" in manual_text

    smart_finance = load("prog_d712dc5849f457ae")
    assert any(
        item["course_name_snapshot"] == "商管軟體設計"
        for item in smart_finance["course_catalog"]
    )
    smart_requirements = smart_finance["structured_requirements"]
    assert smart_requirements["minimum_total_credits"] == 11
    assert all(
        item["resolution_status"] == "resolved"
        for item in smart_requirements["source_conflicts"]
    )

    marine = load("prog_e0cebeb568e35264")
    marine_pairs = {
        (tuple(item["opening_units"]), item["course_name_snapshot"])
        for item in marine["course_catalog"]
    }
    assert (("生科系",), "分子生物學") in marine_pairs
    assert (("生科系",), "生物技術概論") in marine_pairs
    assert (("生科系",), "細胞訊息傳遞學概論") in marine_pairs
    assert (("生科系",), "動物生理學") in marine_pairs

    performing = load("prog_e902d3dcf603529c")["structured_requirements"]
    assert [
        item["course_names"] for item in performing["entry_selection_constraints"]
    ] == [[
        "戲劇構作導論",
        "戲劇構作實踐:古典",
        "戲劇構作實踐:中古與近代",
        "戲劇構作實踐:現當代",
    ]]

    silicon = load("prog_fd2710ede2ba5cfa")["structured_requirements"]
    equivalences = [
        item
        for item in silicon["course_count_constraints"]
        if item["kind"] == "program_course_equivalence"
    ]
    assert {item["program_course_name_snapshot"] for item in equivalences} == {
        "矽光子原理",
        "矽光子元件",
        "矽光子量測",
    }
    assert all(item["max_courses"] == 1 for item in equivalences)
