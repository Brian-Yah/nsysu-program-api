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
    names = split_course_names(
        "電路學(一)、或電路學", 1, "任選4門", "電機系、機電系"
    )
    assert names == ["電路學(一)", "電路學"]


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
