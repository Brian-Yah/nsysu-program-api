from nsysu_program_api.evaluator import evaluate


def ref(code, alias=None):
    return {"course_code": code, "aliases": [alias] if alias else []}


def test_completed_in_progress_missing_alias_and_no_double_count():
    rules = {
        "kind": "all_of",
        "rules": [
            {
                "kind": "course_set",
                "courses": [ref("A", "OLD-A")],
                "min_courses": 1,
                "min_credits": 3,
            },
            {"kind": "course_set", "courses": [ref("B")], "min_courses": 1, "min_credits": 3},
            {"kind": "course_set", "courses": [ref("C")], "min_courses": 1, "min_credits": 3},
        ],
    }
    student = {
        "department": "CSE",
        "completed_courses": [{"course_code": "OLD-A", "credits": 3, "semester": "114-1"}],
        "in_progress_courses": [{"course_code": "B", "credits": 3, "semester": "115-1"}],
    }
    result = evaluate(rules, student)
    assert result["status"] == "in_progress"
    assert {d["status"] for d in result["details"]} == {"completed", "in_progress", "missing"}


def test_manual_review_propagates():
    result = evaluate({"kind": "manual_review", "reason": "approval required"}, {})
    assert result["status"] == "needs_review"


def test_empty_composite_rule_never_passes_vacuously():
    result = evaluate({"kind": "all_of", "rules": []}, {})
    assert result["status"] == "needs_review"
