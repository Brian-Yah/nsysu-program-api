from nsysu_program_api.selection import (
    build_selection_requirements,
    split_course_names,
)


def course(name: str, entry: str, subject: str | None = None) -> dict:
    return {
        "catalog_entry_id": entry,
        "course_name_snapshot": name,
        "requirement_group": "core",
        "requirement_section": "core_required",
        "program_course_name_snapshot": subject,
        "source_page": 1,
        "notes": "",
    }


def row(index: int, courses: list[dict], text: str = "") -> dict:
    return {
        "table_id": "1:0",
        "row_index": index,
        "source_page": 1,
        "requirement_group": "core",
        "requirement_section": "core_required",
        "rule_text": text,
        "notes": text,
        "raw_course_name": " ".join(c["course_name_snapshot"] for c in courses),
        "is_summary": False,
        "courses": courses,
    }


def test_split_wrapped_title_is_not_treated_as_two_courses() -> None:
    names = split_course_names("高可靠度系統之設計、測試與應\n用", 1, "任選1門", "電機碩")
    assert names == ["高可靠度系統之設計、測試與應用"]


def test_split_explicit_alternatives_in_one_catalog_entry() -> None:
    names = split_course_names("電路學(一)、或電路學", 1, "任選4門", "電機系、機電系")
    assert names == ["電路學(一)", "電路學"]


def test_split_course_names_supports_minimum_wording_without_crashing() -> None:
    names = split_course_names("甲課程\n乙課程", 1, "至少選修一門課程", "甲系\n乙系")
    assert names == ["甲課程", "乙課程"]


def test_split_numbered_alternatives_in_one_catalog_entry() -> None:
    names = split_course_names(
        "1. 戲劇構作實踐:古典\n2. 戲劇構作實踐:中古與近代\n3. 戲劇構作實踐:現當代",
        1,
        "戲劇構作系列課程擇一",
        "劇藝系",
    )
    assert names == [
        "戲劇構作實踐:古典",
        "戲劇構作實踐:中古與近代",
        "戲劇構作實踐:現當代",
    ]


def test_ratio_with_multiple_credit_entries_stops_at_declared_count() -> None:
    alternatives = [course("甲", "entry_a_1"), course("乙", "entry_a_2"), course("丙", "entry_a_3")]
    rows = [row(0, alternatives, "三擇一"), row(1, [course("丁", "entry_b")])]
    requirements = build_selection_requirements(alternatives + rows[1]["courses"], rows, [])
    constraint = requirements["entry_selection_constraints"][0]
    assert constraint["catalog_entry_ids"] == ["entry_a_1", "entry_a_2", "entry_a_3"]
    assert constraint["min_entries"] == constraint["max_entries"] == 1


def test_choose_one_repeated_note_drops_trivial_subset() -> None:
    first, second = course("甲", "entry_a"), course("乙", "entry_b")
    rows = [row(0, [first], "擇一修習"), row(1, [second], "擇一修習")]
    requirements = build_selection_requirements([first, second], rows, [])
    constraints = requirements["entry_selection_constraints"]
    assert len(constraints) == 1
    assert constraints[0]["catalog_entry_ids"] == ["entry_a", "entry_b"]
    assert constraints[0]["max_entries"] == 1


def test_at_least_choose_one_has_no_entry_maximum() -> None:
    first, second = course("甲", "entry_a"), course("乙", "entry_b")
    source_text = "A類課程(至少擇一修習)"
    rows = [row(0, [first], source_text), row(1, [second], source_text)]
    requirements = build_selection_requirements([first, second], rows, [])
    constraints = requirements["entry_selection_constraints"]
    assert len(constraints) == 1
    assert constraints[0]["catalog_entry_ids"] == ["entry_a", "entry_b"]
    assert constraints[0]["min_entries"] == 1
    assert constraints[0]["max_entries"] is None


def test_at_least_groups_remain_independent_constraints() -> None:
    courses = [
        course("A甲", "entry_a_1"),
        course("A乙", "entry_a_2"),
        course("B甲", "entry_b_1"),
        course("B乙", "entry_b_2"),
    ]
    rows = [
        row(0, [courses[0]], "A類(至少擇一修習)"),
        row(1, [courses[1]], "A類(至少擇一修習)"),
        row(2, [courses[2]], "B類(至少擇一修習)"),
        row(3, [courses[3]], "B類(至少擇一修習)"),
    ]
    requirements = build_selection_requirements(courses, rows, [])
    constraints = requirements["entry_selection_constraints"]
    assert len(constraints) == 2
    assert {tuple(item["catalog_entry_ids"]) for item in constraints} == {
        ("entry_a_1", "entry_a_2"),
        ("entry_b_1", "entry_b_2"),
    }
    assert all(item["min_entries"] == 1 for item in constraints)
    assert all(item["max_entries"] is None for item in constraints)


def test_program_subject_equivalence_and_subject_selection() -> None:
    courses = [
        course("甲一", "entry_a", "學程科目甲"),
        course("甲二", "entry_b", "學程科目甲"),
        course("乙", "entry_c", "學程科目乙"),
    ]
    rows = [row(0, courses[:1], "每一學程科目 僅採計一門課程學分。2學程科目應選1門")]
    requirements = build_selection_requirements(
        courses,
        rows,
        [(1, "不得重複計入學程核心課程學分。")],
    )
    equivalence = requirements["course_count_constraints"][0]
    assert equivalence["kind"] == "program_course_equivalence"
    assert equivalence["catalog_entry_ids"] == ["entry_a", "entry_b"]
    selection = requirements["program_course_selection_constraints"][0]
    assert selection["program_course_names"] == ["學程科目甲", "學程科目乙"]
    assert requirements["no_double_count_constraints"][0]["max_count_per_course"] == 1


def test_backward_summary_selects_preceding_entries() -> None:
    courses = [course("甲", "entry_a"), course("乙", "entry_b"), course("丙", "entry_c")]
    rows = [row(0, [courses[0]]), row(1, [courses[1]]), row(2, [courses[2]])]
    summary = row(3, [], "核心課程任選二門")
    summary["is_summary"] = True
    rows.append(summary)
    requirements = build_selection_requirements(courses, rows, [])
    constraint = requirements["entry_selection_constraints"][0]
    assert constraint["catalog_entry_ids"] == ["entry_a", "entry_b", "entry_c"]
    assert constraint["min_entries"] == constraint["max_entries"] == 2


def test_named_domain_selection_keeps_domain_groups() -> None:
    courses = [
        course("中國文學史", "entry_a"),
        course("中國思想史", "entry_b"),
        course("語言學概論", "entry_c"),
        course("文字學", "entry_d"),
    ]
    rows = [
        row(0, [courses[0]], "文學/思想/語言文字三領域擇一修習至少6學分"),
        row(1, [courses[1]]),
        row(2, [courses[2]]),
        row(3, [courses[3]]),
    ]
    rows[1]["notes"] = rows[2]["notes"] = rows[3]["notes"] = ""
    requirements = build_selection_requirements(courses, rows, [])
    constraint = requirements["named_group_selection_constraints"][0]
    assert [option["name"] for option in constraint["options"]] == [
        "文學",
        "思想",
        "語言文字",
    ]
    assert constraint["options"][2]["catalog_entry_ids"] == ["entry_c", "entry_d"]
    assert constraint["minimum_credits_per_selected_group"] == 6.0


def test_choose_character_variant_is_supported() -> None:
    first, second = course("甲", "entry_a"), course("乙", "entry_b")
    rows = [row(0, [first], "二選一"), row(1, [second])]
    constraint = build_selection_requirements([first, second], rows, [])[
        "entry_selection_constraints"
    ][0]
    assert constraint["min_entries"] == constraint["max_entries"] == 1
    assert constraint["catalog_entry_ids"] == ["entry_a", "entry_b"]


def test_ratio_summary_collects_preceding_rows() -> None:
    courses = [course(name, f"entry_{name}") for name in ("甲", "乙", "丙", "丁")]
    rows = [row(index, [value]) for index, value in enumerate(courses)]
    footer = row(4, [], "核心課程學分數：6學分（4選2）")
    footer["is_summary"] = True
    rows.append(footer)
    constraint = build_selection_requirements(courses, rows, [])["entry_selection_constraints"][0]
    assert len(constraint["catalog_entry_ids"]) == 4
    assert constraint["min_entries"] == constraint["max_entries"] == 2


def test_group_heading_collects_following_rows_in_same_label() -> None:
    first, second = course("甲", "entry_a"), course("乙", "entry_b")
    heading = row(0, [], "核心一(2選1)")
    heading["requirement_label"] = "核心一"
    first_row, second_row = row(1, [first]), row(2, [second])
    first_row["requirement_label"] = second_row["requirement_label"] = "核心一"
    constraint = build_selection_requirements(
        [first, second], [heading, first_row, second_row], []
    )["entry_selection_constraints"][0]
    assert constraint["catalog_entry_ids"] == ["entry_a", "entry_b"]


def test_summary_mention_limits_selection_to_named_subgroup() -> None:
    ordinary = course("普通選修", "entry_regular")
    service_a = course("服務學習甲", "entry_service_a")
    service_b = course("服務學習乙", "entry_service_b")
    service_a["requirement_label"] = service_b["requirement_label"] = "服務學習課程"
    ordinary_row = row(0, [ordinary])
    service_rows = [row(1, [service_a]), row(2, [service_b])]
    for service_row in service_rows:
        service_row["requirement_label"] = "服務學習課程"
    summary = row(3, [], "必修一門全英語服務學習課程")
    summary["is_summary"] = True
    constraint = build_selection_requirements(
        [ordinary, service_a, service_b],
        [ordinary_row, *service_rows, summary],
        [],
    )["entry_selection_constraints"][0]
    assert constraint["catalog_entry_ids"] == ["entry_service_a", "entry_service_b"]
    assert constraint["requirement_label"] == "服務學習課程"


def test_excess_courses_are_not_rejected_but_count_only_toward_total() -> None:
    courses = [course(name, f"entry_{name}") for name in ("甲", "乙", "丙")]
    rows = [
        row(0, [courses[0]], "三選一，其餘計入總學分數"),
        row(1, [courses[1]]),
        row(2, [courses[2]]),
    ]
    constraint = build_selection_requirements(courses, rows, [])["entry_selection_constraints"][0]
    assert constraint["min_entries"] == 1
    assert constraint["max_entries"] is None
    assert constraint["max_entries_counted_for_requirement"] == 1
    assert constraint["excess_credit_destination"] == "program_total"


def test_one_additional_course_can_flow_to_elective_credit() -> None:
    courses = [course(name, f"entry_{name}") for name in ("甲", "乙", "丙")]
    rows = [
        row(0, [courses[0]], "必修一門，可多修1門，納入選修學分"),
        row(1, [courses[1]]),
        row(2, [courses[2]]),
    ]
    constraint = build_selection_requirements(courses, rows, [])["entry_selection_constraints"][
        0
    ]
    assert constraint["min_entries"] == 1
    assert constraint["max_entries"] == 2
    assert constraint["max_entries_counted_for_requirement"] == 1
    assert constraint["excess_credit_destination"] == "elective"


def test_other_selected_courses_can_flow_to_elective_without_a_limit() -> None:
    courses = [course(name, f"entry_{name}") for name in ("甲", "乙", "丙")]
    rows = [
        row(0, [courses[0]], "必修一門，其他門可採計選修"),
        row(1, [courses[1]]),
        row(2, [courses[2]]),
    ]
    constraint = build_selection_requirements(courses, rows, [])["entry_selection_constraints"][
        0
    ]
    assert constraint["max_entries"] is None
    assert constraint["max_entries_counted_for_requirement"] == 1
    assert constraint["excess_credit_destination"] == "elective"


def test_declared_group_credit_minimum_is_attached_to_selection() -> None:
    courses = [course(name, f"entry_{name}") for name in ("甲", "乙")]
    rows = [
        row(0, [courses[0]], "核心課程II(必修1門,2學分;其他門可採計為選修)"),
        row(1, [courses[1]]),
    ]
    constraint = build_selection_requirements(courses, rows, [])["entry_selection_constraints"][
        0
    ]
    assert constraint["minimum_credits_for_requirement"] == 2


def test_capstone_credit_heading_requires_one_matching_entry() -> None:
    first, second = course("總結甲", "entry_a"), course("總結乙", "entry_b")
    heading = row(0, [], "四、總結性課程(2學分)")
    heading["requirement_label"] = "總結性課程"
    following = [row(1, [first]), row(2, [second])]
    for item in following:
        item["requirement_label"] = "總結性課程"
    constraint = build_selection_requirements(
        [first, second], [heading, *following], []
    )["entry_selection_constraints"][0]
    assert constraint["catalog_entry_ids"] == ["entry_a", "entry_b"]
    assert constraint["minimum_credits_for_requirement"] == 2


def test_at_least_select_course_has_no_maximum() -> None:
    first, second = course("甲", "entry_a"), course("乙", "entry_b")
    rows = [row(0, [first], "至少選修一門課程"), row(1, [second])]
    constraint = build_selection_requirements([first, second], rows, [])[
        "entry_selection_constraints"
    ][0]
    assert constraint["min_entries"] == 1
    assert constraint["max_entries"] is None


def test_declared_option_count_mismatch_is_an_unresolved_source_conflict() -> None:
    courses = [course(name, f"entry_{name}") for name in ("甲", "乙", "丙")]
    rows = [row(0, courses, "二擇一")]
    requirements = build_selection_requirements(courses, rows, [])
    constraint = requirements["entry_selection_constraints"][0]
    assert constraint["option_count_matches"] is False
    conflict = requirements["source_conflicts"][0]
    assert conflict["semantic_key"] == f"selection.{constraint['constraint_id']}.option_count"
    assert {candidate["value"] for candidate in conflict["candidates"]} == {2, 3}
    assert conflict["resolution_status"] == "unresolved"


def test_credit_value_before_choose_one_is_not_an_option_count() -> None:
    first, second = course("甲", "entry_a"), course("乙", "entry_b")
    rows = [
        row(0, [first], "甲 2 擇一修習"),
        row(1, [second], "乙 3 擇一修習"),
    ]
    for item in rows:
        item["notes"] = "擇一修習"
    requirements = build_selection_requirements([first, second], rows, [])
    constraint = requirements["entry_selection_constraints"][0]
    assert constraint["catalog_entry_ids"] == ["entry_a", "entry_b"]
    assert constraint["declared_option_count"] is None
    assert "source_conflicts" not in requirements


def test_overflow_rule_in_course_note_keeps_extra_entries_in_program_total() -> None:
    courses = [course(name, f"entry_{name}") for name in ("甲", "乙", "丙")]
    courses[0]["notes"] = "三選一其餘計入總學分數"
    rows = [row(index, [item]) for index, item in enumerate(courses)]
    requirements = build_selection_requirements(courses, rows, [])
    constraint = next(
        item
        for item in requirements["entry_selection_constraints"]
        if item.get("excess_credit_destination") == "program_total"
    )
    assert constraint["catalog_entry_ids"] == [
        "entry_甲",
        "entry_乙",
        "entry_丙",
    ]
    assert constraint["min_entries"] == 1
    assert constraint["max_entries"] is None
    assert constraint["max_entries_counted_for_requirement"] == 1
