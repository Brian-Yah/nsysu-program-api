from __future__ import annotations

from typing import Any


def evaluate(rules: dict, student: dict) -> dict:
    """Reference evaluator for composable reviewed rules; conservative by design."""
    completed = student.get("completed_courses", [])
    in_progress = student.get("in_progress_courses", [])
    by_code = {c["course_code"]: (c, "completed") for c in completed}
    by_code.update({c["course_code"]: (c, "in_progress") for c in in_progress})
    used: set[str] = set()
    details: list[dict[str, Any]] = []

    def course_match(ref: dict) -> tuple[dict, str] | None:
        for code in [ref.get("course_code"), *ref.get("aliases", [])]:
            if code in by_code and by_code[code][0]["course_code"] not in used:
                return by_code[code]
        return None

    def visit(rule: dict) -> tuple[str, float]:
        kind = rule["kind"]
        if kind == "manual_review":
            details.append({"kind": kind, "status": "needs_review", "reason": rule.get("reason")})
            return "needs_review", 0
        if kind == "course_set":
            hits, credits, states = 0, 0.0, []
            for ref in rule.get("courses", []):
                hit = course_match(ref)
                if hit:
                    course, state = hit
                    used.add(course["course_code"])
                    hits += 1
                    credits += float(course["credits"])
                    states.append(state)
            minimum_courses = rule.get("min_courses", len(rule.get("courses", [])))
            minimum_credits = rule.get("min_credits", 0)
            status = (
                "completed"
                if hits >= minimum_courses
                and credits >= minimum_credits
                and "in_progress" not in states
                else (
                    "in_progress"
                    if hits >= minimum_courses and credits >= minimum_credits
                    else "missing"
                )
            )
            details.append(
                {
                    "kind": kind,
                    "status": status,
                    "courses": hits,
                    "credits": credits,
                    "missing_courses": max(0, minimum_courses - hits),
                    "missing_credits": max(0, minimum_credits - credits),
                }
            )
            return status, credits
        if kind in {"all_of", "any_of"}:
            results = [visit(child) for child in rule.get("rules", [])]
            statuses = [x[0] for x in results]
            if "needs_review" in statuses:
                status = "needs_review"
            elif kind == "all_of":
                status = (
                    "completed"
                    if all(x == "completed" for x in statuses)
                    else ("in_progress" if "in_progress" in statuses else "missing")
                )
            else:
                status = (
                    "completed"
                    if "completed" in statuses
                    else ("in_progress" if "in_progress" in statuses else "missing")
                )
            return status, sum(x[1] for x in results)
        raise ValueError(f"unsupported rule kind: {kind}")

    status, credits = visit(rules)
    return {
        "status": status,
        "completed_credits": sum(
            float(c["credits"]) for c in completed if c["course_code"] in used
        ),
        "in_progress_credits": sum(
            float(c["credits"]) for c in in_progress if c["course_code"] in used
        ),
        "counted_credits": credits,
        "details": details,
        "needs_review": [d for d in details if d["status"] == "needs_review"],
    }
