from copy import deepcopy

from nsysu_program_api.ai_review import (
    AI_REVIEW_POLICY_VERSION,
    apply_ai_review_audit,
    candidate_set_sha256,
    simple_logic_candidate_ids,
    simple_logic_disqualifiers,
)
from nsysu_program_api.core import write_json


def simple_program(index: int) -> dict:
    return {
        "program_id": f"prog_{index:016x}",
        "name_zh": f"普通學程 {index}",
        "type": "micro_program",
        "status": "active",
        "warnings": [],
        "review_status": "needs_review",
        "rules": {"kind": "manual_review", "reason": "pending"},
        "selected_pdf_academic_version": "114-1",
        "source": {
            "pdf_binary_sha256": f"{index:064x}",
            "normalized_text_sha256": f"{index + 10:064x}",
        },
        "course_catalog": [
            {
                "catalog_entry_id": f"entry_{index:016x}",
                "requirement_group": "core",
                "notes": "",
            }
        ],
        "structured_requirements": {
            "minimum_total_credits": 9,
            "minimum_core_credits": 3,
            "credit_constraints": [
                {
                    "kind": "minimum_credits",
                    "scope": {"kind": "program"},
                    "minimum_credits": 9,
                },
                {
                    "kind": "minimum_credits",
                    "scope": {
                        "kind": "catalog_filter",
                        "requirement_groups": ["core"],
                    },
                    "minimum_credits": 3,
                },
            ],
        },
    }


def test_special_rules_are_not_ai_approval_candidates() -> None:
    program = simple_program(1)
    assert simple_logic_disqualifiers(program) == []

    program["structured_requirements"]["entry_selection_constraints"] = [
        {"min_entries": 1}
    ]
    assert "entry_selection_constraints" in simple_logic_disqualifiers(program)

    program = simple_program(1)
    program["course_catalog"][0]["notes"] = "三選一，其餘計入總學分"
    assert "special_rule_text_in_course_note" in simple_logic_disqualifiers(program)


def test_pinned_three_sample_audit_marks_exact_candidate_set(tmp_path) -> None:
    programs = [simple_program(index) for index in (1, 2, 3)]
    candidate_ids = simple_logic_candidate_ids(programs)
    sample = []
    for program in programs:
        sample.append(
            {
                "program_id": program["program_id"],
                "pdf_binary_sha256": program["source"]["pdf_binary_sha256"],
                "normalized_text_sha256": program["source"][
                    "normalized_text_sha256"
                ],
                "selected_pdf_academic_version": "114-1",
                "result": "passed",
            }
        )
    write_json(
        tmp_path / "data" / "ai-review" / "115-1.json",
        {
            "academic_version": "115-1",
            "policy_version": AI_REVIEW_POLICY_VERSION,
            "audit_status": "passed",
            "candidate_count": 3,
            "candidate_set_sha256": candidate_set_sha256(candidate_ids),
            "sample": sample,
        },
    )

    assert apply_ai_review_audit(tmp_path, "115-1", programs) == 3
    assert {program["review_status"] for program in programs} == {"ai_approved"}
    assert all(program["review"]["sample_size"] == 3 for program in programs)


def test_human_override_is_never_replaced_by_ai_approval() -> None:
    program = simple_program(1)
    program["review"] = {"override_path": "data/reviewed/115-1/prog.json"}
    assert "human_review_override" in simple_logic_disqualifiers(program)

    approved = deepcopy(program)
    approved["review_status"] = "approved"
    assert simple_logic_candidate_ids([approved]) == []
