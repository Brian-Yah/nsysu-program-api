from nsysu_program_api.requirements import (
    extract_completion_requirements,
    extract_manual_requirements,
    finalize_completion_summary,
    select_applicable_rule_version,
)


def summary(text: str, page: int = 1) -> dict:
    return {
        "source_page": page,
        "raw_text": text,
        "rule_text": text,
        "is_summary": True,
    }


def test_selects_latest_non_future_version_independent_of_order() -> None:
    versions = [
        {"pdf_academic_version": "114-1", "courses": [{}], "requirements": {}},
        {"pdf_academic_version": "112-2", "courses": [{}], "requirements": {}},
        {"pdf_academic_version": "114-2", "courses": [{}], "requirements": {}},
        {"pdf_academic_version": "116-1", "courses": [{}], "requirements": {}},
    ]
    selected, warnings = select_applicable_rule_version(versions, "115-1")
    assert selected["pdf_academic_version"] == "114-2"
    assert warnings == []


def test_unknown_version_is_only_used_when_no_dated_version_exists() -> None:
    selected, warnings = select_applicable_rule_version(
        [{"pdf_academic_version": None, "courses": [{}], "requirements": {}}],
        "115-1",
    )
    assert selected["courses"]
    assert warnings == ["academic_version_unknown"]


def test_empty_derived_summary_does_not_hide_older_complete_version() -> None:
    selected, warnings = select_applicable_rule_version(
        [
            {
                "pdf_academic_version": "114-2",
                "courses": [],
                "requirements": {"completion_summary": {"model_status": "partial"}},
            },
            {
                "pdf_academic_version": "114-1",
                "courses": [{"catalog_entry_id": "entry_a"}],
                "requirements": {},
            },
        ],
        "115-1",
    )
    assert selected["pdf_academic_version"] == "114-1"
    assert warnings == []


def test_stream_separates_declared_pool_from_minimum_core() -> None:
    requirements = extract_completion_requirements(
        [
            summary("核心課程〈專業模組課程〉學分數：15學分（至少修畢3學分）"),
            summary("總學分數：至少9學分"),
        ],
        [],
    )
    assert requirements["core_course_pool_credits"] == 15
    assert requirements["minimum_core_credits"] == 3
    assert requirements["core_credits_text_value"] == 3
    assert requirements["minimum_total_credits"] == 9


def test_course_pool_total_is_not_completion_minimum() -> None:
    requirements = extract_completion_requirements(
        [summary("總學分數：選修課程31學分，完成微學程條件至少必修加選修需達12學分")],
        [],
    )
    assert requirements["total_course_pool_credits"] == 31
    assert requirements["minimum_total_credits"] == 12


def test_page_text_fallback_recognizes_graduation_total_wording() -> None:
    requirements = extract_completion_requirements(
        [summary("核心課程學分數：9學分")],
        [(1, "本學程規定之結業學分總數至少 15 學分，其中專業模組課程須達9學分。")],
    )
    assert requirements["minimum_total_credits"] == 15


def test_supplemental_any_of_keeps_only_the_two_alternatives() -> None:
    page = (
        "課程規劃表與其他前文。總學分數：至少9學分，"
        "參加至少一場「海洋與島嶼環境變遷」共學群的微學分工作坊，"
        "或規劃與港市相關之個人化(U)學程。"
    )
    manual = extract_manual_requirements([(1, page)])
    requirement = next(item for item in manual if item.get("satisfaction") == "any_of")
    assert [option["description"] for option in requirement["options"]] == [
        "參加至少一場「海洋與島嶼環境變遷」共學群的微學分工作坊",
        "規劃與港市相關之個人化(U)學程",
    ]
    assert "課程規劃表" not in requirement["description"]


def test_multiline_supplemental_any_of_does_not_become_mandatory_workshop() -> None:
    manual = extract_manual_requirements(
        [
            (
                1,
                "參加至少一場海洋共學群的\n"
                "微學分工作坊，或規劃與港市相關之\n"
                "個人化(U)學程。",
            )
        ]
    )
    assert len(manual) == 1
    assert manual[0]["satisfaction"] == "any_of"


def test_workshop_count_is_not_duplicated_as_generic_activity() -> None:
    manual = extract_manual_requirements([(1, "參加至少一場學分工作坊。")])
    assert len(manual) == 1
    assert manual[0]["requirement_type"] == "workshop_attendance"
    assert manual[0]["minimum_count"] == 1


def test_total_summary_with_subgroup_minima_uses_overall_minimum() -> None:
    requirements = extract_completion_requirements(
        [summary("總學分數：C與D類課程共計至少3學分，A、B、C與D類課程共計至少9學分")],
        [],
    )
    assert requirements["minimum_total_credits"] == 9
    scopes = [item["scope"] for item in requirements["credit_constraints"]]
    assert {
        "kind": "requirement_labels",
        "requirement_labels": ["C類", "D類"],
        "aggregation": "union",
    } in scopes


def test_named_core_minima_bind_to_each_nearby_value() -> None:
    source = "核心課程〈專業模組課程〉學分數：至少6學分（核心一至少3學分，核心二至少3學分）"
    row = summary(source)
    row["requirement_label"] = "核心二"
    requirements = extract_completion_requirements([row], [])
    minima = {
        tuple(item["scope"].get("requirement_labels", [])): item["minimum_credits"]
        for item in requirements["credit_constraints"]
        if item["scope"]["kind"] == "requirement_labels"
    }
    assert minima == {("核心一",): 3, ("核心二",): 3}


def test_required_and_selective_core_sections_are_summed() -> None:
    required = summary("核心課程學分數：6學分")
    required["requirement_section"] = "core_required"
    selective = summary("核心課程學分數：18學分")
    selective["requirement_section"] = "core_selective"
    elective = summary("選修課程學分數：9學分")
    elective["requirement_section"] = "elective"
    requirements = extract_completion_requirements(
        [required, selective, elective, summary("總學分數：至少33學分")],
        [],
    )
    assert requirements["minimum_core_credits"] == 24
    assert requirements["core_credits_text_value"] == 24
    assert requirements["minimum_elective_credits"] == 9
    assert requirements["minimum_total_credits"] == 33
    section_constraints = [
        item
        for item in requirements["credit_constraints"]
        if item["scope"].get("requirement_sections")
    ]
    assert {
        (item["scope"]["requirement_sections"][0], item["minimum_credits"])
        for item in section_constraints
    } == {("core_required", 6), ("core_selective", 18)}


def test_taica_eligibility_is_not_reduced_to_opening_department() -> None:
    page = (
        "學生申請TAICA學分證明時，須至少有9學分不屬於其主修、輔系或其他學分學程之"
        "必修或必選課程。"
    )
    requirements = extract_completion_requirements([], [(1, page)])
    constraint = requirements["credit_constraints"][0]
    assert constraint["scope"] == {
        "kind": "course_eligibility",
        "excluded_affiliations": ["major", "minor", "other_program"],
        "excluded_course_roles": ["required", "required_elective"],
    }
    assert requirements["minimum_eligible_curriculum_credits"] == 9
    assert "minimum_outside_home_department_credits" not in requirements


def test_outside_scope_uses_the_page_that_contains_the_rule() -> None:
    requirements = extract_completion_requirements(
        [],
        [
            (1, "其中至少6學分須為非屬原主修、雙主修及輔系之課程。"),
            (2, "本頁無其他規則。"),
        ],
    )
    constraint = requirements["credit_constraints"][0]
    assert constraint["scope"] == {
        "kind": "course_eligibility",
        "excluded_affiliations": ["home_department", "double_major", "minor"],
        "excluded_course_roles": ["all"],
    }


def test_unrelated_later_taica_text_does_not_change_plain_outside_scope() -> None:
    requirements = extract_completion_requirements(
        [],
        [
            (1, "其中至少3學分須為非屬原主修之課程。"),
            (2, "TAICA學分證明之其他說明。"),
        ],
    )
    assert requirements["credit_constraints"][0]["scope"] == {
        "kind": "outside_home_department"
    }


def test_range_keeps_minimum_and_maximum_counted_core() -> None:
    requirements = extract_completion_requirements(
        [summary("核心課程〈專業模組課程〉學分數：3~9學分")],
        [],
    )
    assert requirements["minimum_core_credits"] == 3
    assert requirements["maximum_core_credits"] == 9
    assert requirements["core_credits_text_value"] == 3


def test_enumerated_core_credit_range_is_normalized() -> None:
    requirements = extract_completion_requirements(
        [summary("核心課程學分數:學分:7或8或9或10學分")],
        [],
    )
    assert requirements["minimum_core_credits"] == 7
    assert requirements["maximum_core_credits"] == 10


def test_official_total_conflict_is_not_silently_resolved() -> None:
    page = (
        "總學分數：至少9學分。微學程架構分為各類課程，"
        "課程規劃至少11學分，獲得至少11學分後才有領取微學程證書之資格。"
    )
    requirements = extract_completion_requirements([summary("總學分數：至少9學分")], [(1, page)])
    finalize_completion_summary(requirements)
    assert "minimum_total_credits" not in requirements
    assert requirements["completion_summary"]["minimum_total_credits"] is None
    assert requirements["completion_summary"]["model_status"] == "conflicted"
    assert {
        candidate["value"] for candidate in requirements["source_conflicts"][0]["candidates"]
    } == {
        9,
        11,
    }


def test_non_total_conflict_does_not_erase_known_total() -> None:
    requirements = {
        "minimum_total_credits": 9,
        "source_conflicts": [
            {
                "semantic_key": "selection.constraint_deadbeefdeadbeef.option_count",
                "resolution_status": "unresolved",
            }
        ],
    }
    finalize_completion_summary(requirements)
    assert requirements["completion_summary"]["model_status"] == "conflicted"
    assert requirements["completion_summary"]["minimum_total_credits"] == 9


def test_approved_empty_requirements_are_never_marked_complete() -> None:
    requirements = {}
    finalize_completion_summary(requirements, approved=True)
    assert requirements["completion_summary"]["model_status"] == "partial"


def test_course_titles_and_version_boilerplate_are_not_manual_requirements() -> None:
    manual = extract_manual_requirements(
        [
            (
                1,
                "服務學習：人本交通環境促進\n"
                "永續報告書寫作\n"
                "修習學程適用之課程規劃表請依核准修習學年期版本為主。",
            )
        ]
    )
    assert manual == []


def test_course_table_fragments_are_not_service_or_report_requirements() -> None:
    manual = extract_manual_requirements(
        [
            (1, "至少2學分選修西灣學院來去台灣3服務學習：校園志工"),
            (2, "應用服務3故事創意與AI繪本創作3視覺文化與哲學反思"),
            (3, "跨院選修(工)資工系資料結構3跨院選修(工)應用數學"),
            (4, "碩士班課程數據科學實務與創新3人工智慧應用"),
            (5, "先修"),
        ]
    )
    assert manual == []


def test_certificate_statements_do_not_block_program_completion() -> None:
    manual = extract_manual_requirements(
        [(1, "修畢規定學分後可申請環境教育教學人員認證。")]
    )
    certificate = next(
        item for item in manual if item["requirement_type"] == "certificate"
    )
    assert certificate["requirement_context"] == "certificate"


def test_manual_activity_rules_support_chinese_numbers() -> None:
    manual = extract_manual_requirements(
        [(1, "應在人權領域持續工作至少十五小時。\n至少一場成果發表會。")]
    )
    hours = next(item for item in manual if item["requirement_type"] == "activity_hours")
    count = next(item for item in manual if item["requirement_type"] == "activity_count")
    assert hours["minimum_hours"] == 15
    assert count["minimum_count"] == 1


def test_manual_activity_numbers_are_bound_to_the_matching_rule() -> None:
    manual = extract_manual_requirements(
        [(1, "應服務至少十五小時並參加至少二場成果發表。")]
    )
    hours = next(item for item in manual if item["requirement_type"] == "activity_hours")
    count = next(item for item in manual if item["requirement_type"] == "activity_count")
    assert hours["minimum_hours"] == 15
    assert "minimum_count" not in hours
    assert count["minimum_count"] == 2
    assert "minimum_hours" not in count


def test_explicit_credit_caps_are_executable_constraints() -> None:
    requirements = extract_completion_requirements(
        [],
        [
            (1, "得採計全英授課、主題與科技或環境相關之微學分課程，至多1學分。"),
            (2, "博雅向度六:自然環境、生態及其永續至多3學分。"),
            (3, "經認可與本學程相關之線上課程，最多可折抵6學分。"),
        ],
    )
    maxima = {
        tuple(item["scope"].get("course_attributes", item["scope"].get("opening_units", []))):
        item["maximum_counted_credits"]
        for item in requirements["credit_constraints"]
    }
    assert maxima[("microcredit",)] == 1
    assert maxima[("博雅向度六",)] == 3
    assert maxima[("online",)] == 6


def test_core_overflow_is_counted_as_elective_instead_of_rejected() -> None:
    requirements = extract_completion_requirements(
        [],
        [(1, "學生若修習核心課程超過12學分，多餘的核心課程學分可以納入選修學分計算。")],
    )
    cap = requirements["credit_constraints"][0]
    assert cap["scope"] == {
        "kind": "catalog_filter",
        "requirement_groups": ["core"],
    }
    assert cap["maximum_counted_credits"] == 12
    assert cap["excess_credit_destination"] == "elective"


def test_cohort_specific_core_overflow_is_not_applied_to_every_student() -> None:
    source = (
        "110學年度以前申請修讀之學生，核心課程超過12學分，"
        "多餘的核心課程學分可以納入選修學分計算。"
    )
    requirements = extract_completion_requirements([], [(1, source)])
    assert requirements.get("credit_constraints", []) == []
    assert any(
        item["requirement_type"] in {"student_cohort", "curriculum_exception"}
        for item in requirements["manual_requirements"]
    )


def test_specific_spectrum_courses_have_a_shared_credit_cap() -> None:
    rows = [
        {
            "courses": [
                {
                    "catalog_entry_id": "entry_1111111111111111",
                    "course_name_snapshot": "有機光譜概論",
                },
                {
                    "catalog_entry_id": "entry_2222222222222222",
                    "course_name_snapshot": "有機光譜學",
                },
            ]
        }
    ]
    requirements = extract_completion_requirements(
        rows,
        [(1, "「有機光譜概論」和「有機光譜學」之學分不得重複認列。")],
    )
    cap = requirements["credit_constraints"][0]
    assert cap["scope"]["kind"] == "catalog_entries"
    assert cap["maximum_counted_credits"] == 3


def test_delivery_language_rule_is_preserved_for_manual_verification() -> None:
    manual = extract_manual_requirements(
        [(5, "當學期課程開設為英語授課者，方得認列本學程學分；相同課名以中文授課者，不採計。")]
    )
    assert {item["requirement_type"] for item in manual} == {"course_eligibility"}
    assert manual[0]["source_page"] == 5


def test_certificate_only_manual_rules_are_marked_with_certificate_context() -> None:
    manual = extract_manual_requirements(
        [
            (
                1,
                "欲取得 NSYSU + OSUN Human Rights Certificate，"
                "須完成至少五項活動、至少十五小時，"
                "並提交最終批判性反思論文。",
            )
        ]
    )
    assert manual
    assert {item["requirement_context"] for item in manual} == {"certificate"}
    assert any(item.get("minimum_count") == 5 for item in manual)
    assert any(item.get("minimum_hours") == 15 for item in manual)
    assert any(item["requirement_type"] == "report" for item in manual)


def test_cohort_and_removed_course_exceptions_are_preserved() -> None:
    manual = extract_manual_requirements(
        [
            (
                2,
                "自114學年度第2學期起，將從本學程課程架構中移除。"
                "凡於114學年度第1學期(含)前已取得該課程學分者，"
                "可將其計入選修學分。",
            )
        ]
    )
    exceptions = [
        item for item in manual if item["requirement_type"] == "curriculum_exception"
    ]
    assert len(exceptions) == 2
    assert any("選修學分" in item["description"] for item in exceptions)


def test_prior_cohort_course_lists_are_preserved() -> None:
    manual = extract_manual_requirements(
        [(3, "111學年度以前申請修習本學程的學生，可將法文一(一)及法文一(二)列入核心學分。")]
    )
    assert len(manual) == 1
    assert manual[0]["requirement_type"] == "curriculum_exception"
    assert "核心學分" in manual[0]["description"]
