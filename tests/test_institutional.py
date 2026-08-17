from copy import deepcopy

from nsysu_program_api.institutional import apply_institutional_policy

POLICY = {
    "policy_id": "nsysu_program_policy_2026_08_16",
    "source": {"url": "https://ctdr.nsysu.edu.tw/class2.php"},
    "program_type_rules": {
        "integrated_program": [
            {
                "rule_id": "total",
                "kind": "minimum_credits",
                "scope": {"kind": "program"},
                "minimum_credits": 15,
                "source_text": "整合學程十五學分。",
            },
            {
                "rule_id": "outside",
                "kind": "minimum_credits",
                "scope": {
                    "kind": "course_eligibility",
                    "excluded_affiliations": [
                        "home_department",
                        "double_major",
                        "minor",
                    ],
                    "excluded_course_roles": ["all"],
                },
                "minimum_credits": 6,
                "source_text": "系外六學分。",
            },
        ]
    },
}


def program(requirements: dict | None = None) -> dict:
    return {
        "type": "integrated_program",
        "structured_requirements": requirements or {},
    }


def test_policy_fills_missing_baselines_and_records_source() -> None:
    item = program()
    applied = apply_institutional_policy(item, deepcopy(POLICY))
    assert applied == ["total", "outside"]
    assert item["structured_requirements"]["minimum_total_credits"] == 15
    assert item["institutional_policy_ids"] == ["nsysu_program_policy_2026_08_16"]
    constraints = item["structured_requirements"]["credit_constraints"]
    assert len(constraints) == 2
    assert all(rule["source_kind"] == "institutional_catalog" for rule in constraints)
    assert all(rule["source_url"].startswith("https://") for rule in constraints)


def test_stronger_pdf_rule_is_not_duplicated() -> None:
    scope = POLICY["program_type_rules"]["integrated_program"][1]["scope"]
    item = program(
        {
            "minimum_total_credits": 18,
            "credit_constraints": [
                {
                    "constraint_id": "constraint_existing",
                    "kind": "minimum_credits",
                    "scope": {"kind": "program"},
                    "minimum_credits": 18,
                    "requirement_context": "program_completion",
                    "source_page": 1,
                    "source_text": "本學程至少十八學分。",
                    "validation_status": "source_text_match",
                },
                {
                    "constraint_id": "constraint_outside",
                    "kind": "minimum_credits",
                    "scope": scope,
                    "minimum_credits": 9,
                    "requirement_context": "program_completion",
                    "source_page": 1,
                    "source_text": "系外至少九學分。",
                    "validation_status": "source_text_match",
                },
            ],
        }
    )
    assert apply_institutional_policy(item, deepcopy(POLICY)) == []
    assert len(item["structured_requirements"]["credit_constraints"]) == 2
    assert item["structured_requirements"]["minimum_total_credits"] == 18


def test_policy_application_is_idempotent() -> None:
    item = program()
    apply_institutional_policy(item, deepcopy(POLICY))
    apply_institutional_policy(item, deepcopy(POLICY))
    assert len(item["structured_requirements"]["credit_constraints"]) == 2
    assert item["institutional_policy_ids"] == ["nsysu_program_policy_2026_08_16"]
