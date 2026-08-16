from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable

TERM_ORDER = {"1": 1, "2": 2, "S": 3}


def academic_version_key(value: str | None) -> tuple[int, int] | None:
    """Return a sortable ROC academic-version key, or None for unknown values."""
    match = re.fullmatch(r"(?P<year>\d{3})-(?P<term>1|2|S)", value or "")
    if not match:
        return None
    return int(match.group("year")), TERM_ORDER[match.group("term")]


def select_applicable_rule_version(
    rule_versions: list[dict], target_academic_version: str
) -> tuple[dict, list[str]]:
    """Select the latest non-future PDF rule version without relying on PDF page order."""
    target_key = academic_version_key(target_academic_version)
    if target_key is None:
        raise ValueError(f"invalid target academic version: {target_academic_version}")

    usable = []
    for version in rule_versions:
        requirements = version.get("requirements", {})
        has_substantive_requirements = any(
            key != "completion_summary" for key in requirements
        )
        if version.get("courses") or has_substantive_requirements:
            usable.append(version)
    dated = [
        (key, version)
        for version in usable
        if (key := academic_version_key(version.get("pdf_academic_version"))) is not None
        and key <= target_key
    ]
    if dated:
        return max(dated, key=lambda item: item[0])[1], []

    unknown = [version for version in usable if version.get("pdf_academic_version") is None]
    if len(unknown) == 1 and not any(
        academic_version_key(version.get("pdf_academic_version")) for version in usable
    ):
        return unknown[0], ["academic_version_unknown"]

    if any(
        (key := academic_version_key(version.get("pdf_academic_version"))) is not None
        and key > target_key
        for version in usable
    ):
        return {
            "pdf_academic_version": None,
            "courses": [],
            "requirements": {},
        }, ["no_non_future_pdf_academic_version"]

    return {
        "pdf_academic_version": None,
        "courses": [],
        "requirements": {},
    }, ["no_structured_rule_version"]


def _compact(value: object) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def _display_text(value: object) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip()


def _stable_id(prefix: str, *parts: object) -> str:
    identity = "|".join(str(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(identity.encode()).hexdigest()[:16]}"


def _evidence(page: int, text: str) -> dict:
    return {
        "source_page": page,
        "source_text": _display_text(text),
        "validation_status": "source_text_match",
    }


def _credit_constraint(
    scope: dict,
    minimum: float,
    page: int,
    source_text: str,
    *,
    maximum_counted: float | None = None,
    context: str = "program_completion",
    minimum_qualifying_courses: int | None = None,
    minimum_credits_per_qualifying_course: float | None = None,
) -> dict:
    result = {
        "constraint_id": _stable_id(
            "constraint", "minimum_credits", scope, minimum, maximum_counted, context, source_text
        ),
        "kind": "minimum_credits",
        "scope": scope,
        "minimum_credits": float(minimum),
        "requirement_context": context,
        **_evidence(page, source_text),
    }
    if maximum_counted is not None:
        result["maximum_counted_credits"] = float(maximum_counted)
    if minimum_qualifying_courses is not None:
        result["minimum_qualifying_courses"] = minimum_qualifying_courses
    if minimum_credits_per_qualifying_course is not None:
        result["minimum_credits_per_qualifying_course"] = float(
            minimum_credits_per_qualifying_course
        )
    return result


def _maximum_credit_constraint(
    scope: dict,
    maximum: float,
    page: int,
    source_text: str,
    *,
    context: str = "program_completion",
) -> dict:
    return {
        "constraint_id": _stable_id(
            "constraint", "maximum_counted_credits", scope, maximum, context, source_text
        ),
        "kind": "maximum_counted_credits",
        "scope": scope,
        "maximum_counted_credits": float(maximum),
        "requirement_context": context,
        **_evidence(page, source_text),
    }


def _pool(
    pool_id: str,
    scope: dict,
    page: int,
    source_text: str,
    *,
    declared: float | None = None,
    minimum_declared: float | None = None,
    maximum_declared: float | None = None,
) -> dict:
    result = {
        "pool_id": pool_id,
        "scope": scope,
        "source_evidence": [_evidence(page, source_text)],
        "interpretation_status": "machine_extracted",
    }
    if declared is not None:
        result["declared_credits"] = float(declared)
    if minimum_declared is not None:
        result["minimum_declared_credits"] = float(minimum_declared)
    if maximum_declared is not None:
        result["maximum_declared_credits"] = float(maximum_declared)
    return result


def _source_row(row: dict) -> tuple[int, str, str]:
    source = row.get("raw_text") or row.get("rule_text") or ""
    return int(row.get("source_page", 1)), _display_text(source), _compact(source)


def _matched_sentence(text: str, start: int, end: int) -> str:
    """Return the source sentence around a match instead of an entire PDF page."""
    normalized = unicodedata.normalize("NFKC", text)
    left = max(
        normalized.rfind("。", 0, start),
        normalized.rfind("；", 0, start),
        normalized.rfind(";", 0, start),
    )
    right_candidates = [
        position
        for position in (
            normalized.find("。", end),
            normalized.find("；", end),
            normalized.find(";", end),
        )
        if position >= 0
    ]
    right = min(right_candidates) + 1 if right_candidates else min(len(normalized), end + 120)
    return _display_text(normalized[left + 1 : right])


def _first_number(pattern: str, text: str) -> float | None:
    match = re.search(pattern, text)
    return float(match.group(1)) if match else None


def _extract_scalar_requirements(table_rows: list[dict], pages: list[tuple[int, str]]) -> dict:
    result: dict = {}
    pools: list[dict] = []
    constraints: list[dict] = []
    core_evidence: tuple[int, str] | None = None
    total_evidence: tuple[int, str] | None = None
    section_core_minima: dict[str, tuple[float, int, str]] = {}

    for row in table_rows:
        page, source, compact = _source_row(row)
        if not compact:
            continue

        if "核心" in compact and "學分數" in compact:
            range_match = re.search(
                r"核心[^:：]{0,20}學分數[:：]?[^0-9]{0,8}(\d+(?:\.5)?)[~～\-至](\d+(?:\.5)?)學分",
                compact,
            )
            enumerated_range = re.search(
                r"核心[^:：]{0,20}學分數[:：]?(?:學分[:：]?)?"
                r"(?P<values>\d+(?:\.5)?(?:或\d+(?:\.5)?)+)學分",
                compact,
            )
            completion_match = re.search(
                r"核心[^:：]{0,24}學分數[:：]?(?:核心課程)?(\d+(?:\.5)?)學分"
                r".{0,45}?(?:至少(?:必)?修(?:習|畢)?(?:(?:核心)?課程)?(?:需)?達|至少修畢)"
                r"(\d+(?:\.5)?)學分",
                compact,
            )
            parenthetical_minimum = re.search(
                r"核心[^:：]{0,24}學分數[:：]?(\d+(?:\.5)?)學分"
                r".{0,20}?至少(?:修習|修畢)?(\d+(?:\.5)?)學分",
                compact,
            )
            at_least_course = re.search(r"核心[^:：]{0,24}學分數[:：]?至少(?:選修)?一門", compact)
            direct = re.search(
                r"核心[^:：]{0,24}學分數[:：]?[^0-9]{0,20}(\d+(?:\.5)?)學分",
                compact,
            )
            if range_match or enumerated_range:
                if range_match:
                    minimum, maximum = map(float, range_match.groups())
                else:
                    assert enumerated_range is not None
                    values = [
                        float(value)
                        for value in enumerated_range.group("values").split("或")
                    ]
                    minimum, maximum = min(values), max(values)
                result["minimum_core_credits"] = minimum
                result["maximum_core_credits"] = maximum
                result["core_credits_text_value"] = minimum
                pools.append(
                    _pool(
                        "pool_core",
                        {"kind": "catalog_filter", "requirement_groups": ["core"]},
                        page,
                        source,
                        minimum_declared=minimum,
                        maximum_declared=maximum,
                    )
                )
                core_evidence = (page, source)
            elif completion_match or parenthetical_minimum:
                match = completion_match or parenthetical_minimum
                assert match is not None
                declared, minimum = map(float, match.groups())
                result["core_course_pool_credits"] = declared
                result["minimum_core_credits"] = minimum
                result["core_credits_text_value"] = minimum
                pools.append(
                    _pool(
                        "pool_core",
                        {"kind": "catalog_filter", "requirement_groups": ["core"]},
                        page,
                        source,
                        declared=declared,
                    )
                )
                core_evidence = (page, source)
            elif at_least_course:
                result["minimum_core_courses"] = 1
                core_evidence = (page, source)
            elif direct:
                minimum = float(direct.group(1))
                section = row.get("requirement_section")
                if section in {"core_required", "core_selective"}:
                    section_core_minima[section] = (minimum, page, source)
                else:
                    result["minimum_core_credits"] = minimum
                    result["core_credits_text_value"] = minimum
                    core_evidence = (page, source)

        if "選修" in compact and "學分數" in compact:
            minimum = _first_number(
                r"選修(?:課程)?學分數[:：]?(?:至少)?(\d+(?:\.5)?)學分",
                compact,
            )
            if minimum is not None:
                result["minimum_elective_credits"] = minimum
                constraints.append(
                    _credit_constraint(
                        {"kind": "catalog_filter", "requirement_groups": ["elective"]},
                        minimum,
                        page,
                        source,
                    )
                )

        if "總學分數" in compact:
            completion = re.search(
                r"選修課程(\d+(?:\.5)?)學分.{0,45}?完成.{0,24}?"
                r"(?:需達|達|至少(?:必)?修(?:加選修)?(?:課程)?需達)"
                r"(\d+(?:\.5)?)學分",
                compact,
            )
            if completion:
                declared, minimum = map(float, completion.groups())
                result["total_course_pool_credits"] = declared
                result["minimum_total_credits"] = minimum
                pools.append(
                    _pool(
                        "pool_program",
                        {"kind": "program"},
                        page,
                        source,
                        declared=declared,
                    )
                )
                total_evidence = (page, source)
            else:
                total_tail = compact[compact.find("總學分數") :]
                candidates = [
                    float(value)
                    for value in re.findall(
                        r"至少(?:共|合計)?[^0-9]{0,4}(\d+(?:\.5)?)學分",
                        total_tail,
                    )
                ]
                if not candidates:
                    direct = re.search(r"總學分數[:：]?[^0-9]{0,12}(\d+(?:\.5)?)學分", compact)
                    if direct:
                        candidates.append(float(direct.group(1)))
                if candidates:
                    result["minimum_total_credits"] = max(candidates)
                    total_evidence = (page, source)

    if len(section_core_minima) >= 2:
        minimum_core = sum(value for value, _, _ in section_core_minima.values())
        result["minimum_core_credits"] = minimum_core
        result["core_credits_text_value"] = minimum_core
        core_evidence = None
        for section, (minimum, page, source) in sorted(section_core_minima.items()):
            constraints.append(
                _credit_constraint(
                    {
                        "kind": "catalog_filter",
                        "requirement_groups": ["core"],
                        "requirement_sections": [section],
                    },
                    minimum,
                    page,
                    source,
                )
            )
    elif section_core_minima and "minimum_core_credits" not in result:
        _, (minimum, page, source) = next(iter(section_core_minima.items()))
        result["minimum_core_credits"] = minimum
        result["core_credits_text_value"] = minimum
        core_evidence = (page, source)

    page_text = "\n".join(text for _, text in pages)
    page_compact = _compact(page_text)

    if "minimum_total_credits" not in result:
        total_fallback_pattern = (
            r"(?:總學習學分為|共計需修習|課程規劃至少|"
            r"總學分數[:：]?至少|結業學分總數至少(?:應達)?)"
            r"(\d+(?:\.5)?)學分"
        )
        total_candidates = [
            float(value)
            for value in re.findall(total_fallback_pattern, page_compact)
        ]
        if total_candidates:
            result["minimum_total_credits"] = max(total_candidates)
            for page, text in pages:
                match = re.search(
                    total_fallback_pattern,
                    _compact(text),
                )
                if match and float(match.group(1)) == result["minimum_total_credits"]:
                    total_evidence = (page, match.group(0))
                    break

    conflicts = []
    certificate_values = {
        float(value)
        for value in re.findall(
            r"(?:課程規劃至少|獲得至少)(\d+(?:\.5)?)學分.{0,40}?(?:證書|資格)",
            page_compact,
        )
    }
    canonical_total = result.get("minimum_total_credits")
    if canonical_total is not None and any(
        value != canonical_total for value in certificate_values
    ):
        values = sorted({float(canonical_total), *certificate_values})
        candidates = []
        for value in values:
            evidence = []
            for page, text in pages:
                compact = _compact(text)
                match = re.search(
                    rf"(?:總學分數|課程規劃至少|獲得至少).{{0,50}}?{value:g}學分",
                    compact,
                )
                if match:
                    evidence.append(_evidence(page, match.group(0)))
                    break
            candidates.append(
                {
                    "candidate_id": _stable_id("candidate", "program.minimum_total_credits", value),
                    "value": value,
                    "source_evidence": evidence,
                }
            )
        conflicts.append(
            {
                "conflict_id": _stable_id("conflict", "program.minimum_total_credits", *values),
                "semantic_key": "program.minimum_total_credits",
                "candidates": candidates,
                "resolution_status": "unresolved",
                "selected_candidate_id": None,
                "resolution_note": None,
            }
        )
        result.pop("minimum_total_credits", None)

    outside_matches: list[
        tuple[float, int, str, int | None, float | None, str]
    ] = []
    for page, text in pages:
        compact = _compact(text)
        patterns = (
            r"(?:至少應有|須至少有|至少應修滿)(?:一門\()?(\d+(?:\.5)?)學分\)?"
            r".{0,32}?不屬於(?:學生)?(?:本|其)(?:系所|主修)",
            r"其中至少(?:應有)?(?:一門\()?(\d+(?:\.5)?)學分\)?"
            r".{0,32}?不屬於(?:學生)?(?:本|其)(?:系所|主修)",
            r"其中至少(\d+(?:\.5)?)學分須為非屬(?:學生)?(?:原)?主修",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, compact):
                # Compact offsets cannot be mapped byte-for-byte to wrapped PDF
                # text, so retain the exact matched phrase as evidence.
                one_course = "一門(" in match.group(0)
                outside_matches.append(
                    (
                        float(match.group(1)),
                        page,
                        match.group(0),
                        1 if one_course else None,
                        float(match.group(1)) if one_course else None,
                        compact[max(0, match.start() - 120) : match.end() + 160],
                    )
                )
    if outside_matches:
        minimum, page, source, minimum_courses, minimum_per_course, matched_context = max(
            outside_matches, key=lambda item: item[0]
        )
        is_taica_certificate = "TAICA" in matched_context and "學分證明" in matched_context
        if is_taica_certificate:
            result["minimum_eligible_curriculum_credits"] = minimum
            scope = {
                "kind": "course_eligibility",
                "excluded_affiliations": ["major", "minor", "other_program"],
                "excluded_course_roles": ["required", "required_elective"],
            }
        elif "雙主修" in matched_context or "輔系" in matched_context:
            result["minimum_outside_home_department_credits"] = minimum
            scope = {
                "kind": "course_eligibility",
                "excluded_affiliations": [
                    "home_department",
                    "double_major",
                    "minor",
                ],
                "excluded_course_roles": ["all"],
            }
        else:
            result["minimum_outside_home_department_credits"] = minimum
            scope = {"kind": "outside_home_department"}
        constraints.append(
            _credit_constraint(
                scope,
                minimum,
                page,
                source,
                context="certificate" if is_taica_certificate else "program_completion",
                minimum_qualifying_courses=minimum_courses,
                minimum_credits_per_qualifying_course=minimum_per_course,
            )
        )

    for page, text in pages:
        compact = _compact(text)
        core_outside = re.search(
            r"核心課程中至少應有(\d+(?:\.5)?)學分.{0,32}?"
            r"不屬於(?:學生)?(?:本|其)(?:系所|主修)",
            compact,
        )
        if core_outside:
            minimum = float(core_outside.group(1))
            result["minimum_outside_home_department_core_credits"] = minimum
            constraints.append(
                _credit_constraint(
                    {
                        "kind": "outside_home_department",
                        "requirement_groups": ["core"],
                    },
                    minimum,
                    page,
                    core_outside.group(0),
                )
            )

    for page, text in pages:
        compact = _compact(text)
        taica_minimum = re.search(r"至少(\d+(?:\.5)?)學分TAICA(?:聯盟)?認定課程", compact)
        if taica_minimum:
            constraints.append(
                _credit_constraint(
                    {"kind": "taica_courses"},
                    float(taica_minimum.group(1)),
                    page,
                    taica_minimum.group(0),
                    context="certificate",
                )
            )
        similar_maximum = re.search(r"性質相近之課程認抵.{0,30}?以(\d+(?:\.5)?)學分為上限", compact)
        if similar_maximum:
            constraints.append(
                _maximum_credit_constraint(
                    {"kind": "recognized_similar_courses"},
                    float(similar_maximum.group(1)),
                    page,
                    similar_maximum.group(0),
                    context="certificate",
                )
            )
        cross_program_maximum = re.search(
            r"TAICA學程間之學分相互認抵以(\d+(?:\.5)?)學分為上限", compact
        )
        if cross_program_maximum:
            constraints.append(
                _maximum_credit_constraint(
                    {"kind": "cross_program_recognition"},
                    float(cross_program_maximum.group(1)),
                    page,
                    cross_program_maximum.group(0),
                    context="certificate",
                )
            )

    if core_evidence and "minimum_core_credits" in result:
        constraints.append(
            _credit_constraint(
                {"kind": "catalog_filter", "requirement_groups": ["core"]},
                result["minimum_core_credits"],
                core_evidence[0],
                core_evidence[1],
                maximum_counted=result.get("maximum_core_credits"),
            )
        )
    if total_evidence and "minimum_total_credits" in result:
        constraints.append(
            _credit_constraint(
                {"kind": "program"},
                result["minimum_total_credits"],
                total_evidence[0],
                total_evidence[1],
            )
        )

    if pools:
        by_id = {}
        for item in pools:
            by_id[item["pool_id"]] = item
        result["course_pools"] = list(by_id.values())
    if constraints:
        by_id = {item["constraint_id"]: item for item in constraints}
        result["credit_constraints"] = sorted(
            by_id.values(), key=lambda item: (item["source_page"], item["constraint_id"])
        )
    if conflicts:
        result["source_conflicts"] = conflicts
    return result


def _extract_labeled_credit_constraints(table_rows: list[dict]) -> list[dict]:
    constraints: list[dict] = []
    for row in table_rows:
        page, source, compact = _source_row(row)
        if not compact or "至少" not in compact or "學分" not in compact:
            continue

        named_core_matches = list(
            re.finditer(
                r"(?P<label>核心(?:課程)?[一二三四IVⅠⅡⅢⅣΙΠ]+)"
                r"至少(?:修畢)?(?P<credits>\d+(?:\.5)?)學分",
                compact,
            )
        )
        if named_core_matches:
            for match in named_core_matches:
                label = match.group("label").replace("Ι", "一").replace("Π", "二")
                constraints.append(
                    _credit_constraint(
                        {
                            "kind": "requirement_labels",
                            "requirement_labels": [label],
                            "aggregation": "union",
                        },
                        float(match.group("credits")),
                        page,
                        source,
                    )
                )
            continue

        matches = list(
            re.finditer(
                r"(?P<labels>[A-D](?:[^A-D0-9]{0,3}[A-D])+)(?:類課程?)?"
                r"(?P<between>[^0-9]{0,10})至少(?P<qualifier>共|合計|各)?"
                r"(?P<credits>\d+(?:\.5)?)學分",
                compact,
            )
        )
        if matches:
            for match in matches:
                labels = re.findall(r"[A-D]", match.group("labels"))
                is_each = "各" in match.group("between") or match.group("qualifier") == "各"
                labels = [f"{label}類" for label in labels]
                constraints.append(
                    _credit_constraint(
                        {
                            "kind": "requirement_labels",
                            "requirement_labels": labels,
                            "aggregation": "each" if is_each else "union",
                        },
                        float(match.group("credits")),
                        page,
                        source,
                    )
                )
            continue

        label = row.get("requirement_label")
        minimum = _first_number(r"至少(?:修畢)?(\d+(?:\.5)?)學分", compact)
        if label and _compact(label) in compact and minimum is not None:
            constraints.append(
                _credit_constraint(
                    {
                        "kind": "requirement_labels",
                        "requirement_labels": [label],
                        "aggregation": "union",
                    },
                    minimum,
                    page,
                    source,
                )
            )
    return constraints


MANUAL_NUMBER_TOKEN = r"(?:\d+(?:\.5)?|[一二三四五六七八九十兩]+)"
MANUAL_RULE_TYPES = (
    (
        "activity_hours",
        re.compile(
            rf"(?:至少|應|須|需).{{0,20}}?"
            rf"(?P<number>{MANUAL_NUMBER_TOKEN})\s*小時"
        ),
    ),
    (
        "activity_count",
        re.compile(
            rf"(?:至少|應|須|需).{{0,20}}?"
            rf"(?P<number>{MANUAL_NUMBER_TOKEN})\s*(?:場|次|項)"
        ),
    ),
    (
        "workshop_attendance",
        re.compile(r"(?:參加|完成|至少|應|須|需).{0,35}?(?:工作坊|workshop)", re.I),
    ),
    (
        "service_learning",
        re.compile(
            r"(?:(?:完成|修習|參與|應(?!用)|須|需(?!求)).{0,12}?服務學習|"
            rf"至少(?:修習)?{MANUAL_NUMBER_TOKEN}門.{{0,8}}?服務學習)"
        ),
    ),
    (
        "report",
        re.compile(
            r"(?:繳交|提交|撰寫|完成|應繳交|應提交|須繳交|須提交|需繳交|需提交)"
            r".{0,80}?(?:心得|反思|報告|論文)"
        ),
    ),
    (
        "certificate",
        re.compile(
            r"(?:(?:申請|取得|獲得|核發|領取).{0,30}?(?:認證|證書|學分證明)|"
            r"(?:認證|證書|學分證明).{0,30}?(?:申請|取得|獲得|核發|領取|資格))"
        ),
    ),
    (
        "prerequisite",
        re.compile(r"(?:方得申請|先修.{0,30}?(?:課程|應|須|需)|修畢.{0,30}?方得)"),
    ),
    (
        "approval",
        re.compile(
            r"(?:所修課程|學分認抵|抵免).{0,35}?"
            r"(?:學程負責人|學程委員會).{0,25}?"
            r"(?:同意|核准|認定)"
        ),
    ),
    (
        "recognition",
        re.compile(
            r"(?:(?:DELF|JLPT|SELPT).{0,250}?(?:抵免|認列)"
            r".{0,80}?\d+(?:\.5)?學分|"
            r"(?:入學前曾修習|同名或同質課程).{0,90}?"
            r"(?:抵免|認抵|認定)|"
            r"外語中心.{0,400}?可申請抵免.{0,200}?"
            r"(?:不可抵免畢業學分|"
            r"可申請抵免法文[（(]二[）)])|"
            r"(?:認抵|抵免|抵認|採計).{0,30}?(?:審查|通過))",
            re.I,
        ),
    ),
    (
        "student_condition",
        re.compile(
            r"(?:(?:跨院(?!選修)|同院跨系).{0,30}?(?:修讀|學生)|"
            r"(?:學士|碩士)班學生|雙主修|輔系)"
            r".{0,80}?(?:應(?!用)|須|至少|至多)"
        ),
    ),
    (
        "course_eligibility",
        re.compile(
            r"(?:(?:英語|全英)授課者.{0,50}?(?:方得|認列|採計)|"
            r"相同課名.{0,40}?中文授課者.{0,20}?不採計|"
            r"為相同課程.{0,35}?(?:不得重複修習|僅能認列其中一門)|"
            r"自\d{3}.{0,30}?起.{0,20}?不認列.{0,120}?課程)"
        ),
    ),
    (
        "credit_cap",
        re.compile(
            r"欄位中若有\*加註.{0,180}?學分數僅以"
            r"一門課程[（(]?3學分[）)]?計算"
        ),
    ),
    (
        "curriculum_exception",
        re.compile(
            r"(?:日本籍學生.{0,100}?(?:免修|補足)|"
            r"抵免.{0,220}?不列入.{0,40}?畢業學分|"
            r"同程度課程.{0,120}?(?:不得重複|其中一門)|"
            r"(?:\d{3}(?:-\d)?(?:學年度|學期).{0,20}?以前|"
            r"\d{3}學年度\(含\)以前).{0,500}?"
            r"(?:(?:列入|計入|納入).{0,60}?學分(?:中)?|適用)|"
            r"自\d{3}(?:-\d)?(?:學年度|學期).{0,120}?移除|"
            r"\d{3}(?:-\d)?(?:學年度|學期).{0,30}?前已取得"
            r".{0,100}?(?:列入|計入|採計).{0,60}?學分(?:中)?|"
            r"已取得者.{0,60}?仍可計入)"
        ),
    ),
)


def _manual_number(value: str) -> float | None:
    normalized = unicodedata.normalize("NFKC", value)
    try:
        return float(normalized)
    except ValueError:
        pass
    digits = {
        "一": 1,
        "二": 2,
        "兩": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if normalized == "十":
        return 10.0
    if "十" in normalized:
        left, right = normalized.split("十", 1)
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        return float(tens * 10 + ones)
    value_int = digits.get(normalized)
    return float(value_int) if value_int is not None else None


def _all_table_courses(table_rows: list[dict]) -> list[dict]:
    by_id: dict[str, dict] = {}
    for row in table_rows:
        for course in row.get("courses", []):
            by_id[course["catalog_entry_id"]] = course
    return list(by_id.values())


def _additional_credit_constraints(
    table_rows: list[dict], pages: list[tuple[int, str]]
) -> list[dict]:
    """Extract explicit caps and allocation rules outside scalar summaries."""
    constraints: list[dict] = []
    courses = _all_table_courses(table_rows)

    def add_maximum(
        scope: dict,
        maximum: float,
        page: int,
        source: str,
        *,
        destination: str | None = None,
    ) -> None:
        item = _maximum_credit_constraint(scope, maximum, page, source)
        if destination:
            item["excess_credit_destination"] = destination
        constraints.append(item)

    for row in table_rows:
        row_source = row.get("raw_text") or row.get("rule_text") or ""
        attribute_cap = re.search(
            rf"各課程屬性僅採計(?P<maximum>{MANUAL_NUMBER_TOKEN})學分",
            _compact(row_source),
        )
        if not attribute_cap:
            continue
        maximum = _manual_number(attribute_cap.group("maximum"))
        if maximum is None:
            continue
        program_course_names = list(
            dict.fromkeys(
                course.get("program_course_name_snapshot")
                for course in courses
                if course.get("program_course_name_snapshot")
            )
        )
        for name in program_course_names:
            add_maximum(
                {"kind": "program_course_names", "program_course_names": [name]},
                maximum,
                int(row.get("source_page", 1)),
                row_source,
            )

    def equivalence_base(name: str) -> str:
        chinese = re.split(
            r"[A-Z]{2,}", unicodedata.normalize("NFKC", name), maxsplit=1
        )[0]
        chinese = re.sub(r"\((?:中|高|進階|初|級|-)+\)", "", chinese)
        return _compact(chinese).rstrip(":：")

    for row in table_rows:
        notes = _compact(row.get("notes"))
        if "為相同課程" not in notes or "其中一門" not in notes:
            continue
        for source_course in row.get("courses", []):
            base = equivalence_base(source_course.get("course_name_snapshot", ""))
            matched = [
                course
                for course in courses
                if base
                and equivalence_base(course.get("course_name_snapshot", "")) == base
            ]
            entry_ids = list(
                dict.fromkeys(course["catalog_entry_id"] for course in matched)
            )
            if len(entry_ids) < 2:
                continue
            add_maximum(
                {"kind": "catalog_entries", "catalog_entry_ids": entry_ids},
                float(source_course["credits_snapshot"]),
                int(row.get("source_page", 1)),
                row.get("raw_text") or row.get("rule_text") or row.get("notes") or "",
            )

    for page, text in pages:
        compact = _compact(text)

        if "微學分課程" in compact and "ORCA共學群" not in compact:
            microcredit_matches = list(
                re.finditer(
                rf"(?:微學分課程.{{0,100}}?至多"
                rf"(?P<after>{MANUAL_NUMBER_TOKEN})學分|"
                rf"至多(?P<before>{MANUAL_NUMBER_TOKEN})學分"
                rf".{{0,100}}?微學分課程)",
                compact,
                )
            )
            if not microcredit_matches and "至多" in compact:
                placeholder = next(
                    (
                        course
                        for course in courses
                        if "微學分課程" in _compact(
                            course.get("course_name_snapshot")
                        )
                        and 0 < float(course.get("credits_snapshot") or 0) <= 3
                    ),
                    None,
                )
                if placeholder:
                    inferred = re.search(
                        r"至多.{0,100}?微學分課程",
                        compact,
                    )
                    if inferred:
                        microcredit_matches = [(inferred, placeholder["credits_snapshot"])]
            for candidate in microcredit_matches:
                if isinstance(candidate, tuple):
                    match, maximum = candidate
                else:
                    match = candidate
                    maximum = _manual_number(match.group("after") or match.group("before"))
                if maximum is None:
                    continue
                attributes = ["microcredit"]
                if "ORCA" in match.group(0):
                    attributes = ["orca_microcredit_workshop"]
                scope: dict = {
                    "kind": "course_attributes",
                    "course_attributes": attributes,
                }
                subject_areas = []
                if "全民國防相關微學分課程" in compact:
                    subject_areas.append("national_defense")
                if "海洋相關微學分課程" in compact:
                    subject_areas.append("marine")
                if "主題與科技或環境相關之微學分課程" in compact:
                    subject_areas.append("science_technology_environment")
                if subject_areas:
                    scope["subject_areas"] = subject_areas
                add_maximum(scope, maximum, page, match.group(0))

        dimension = re.search(
            rf"博雅向度六[:：]?自然環境、生態及其"
            rf".{{0,20}}?至多(?P<maximum>{MANUAL_NUMBER_TOKEN})學.{{0,30}}?分",
            compact,
        )
        if dimension and (maximum := _manual_number(dimension.group("maximum"))) is not None:
            add_maximum(
                {"kind": "opening_units", "opening_units": ["博雅向度六"]},
                maximum,
                page,
                dimension.group(0),
            )

        online = re.search(
            rf"(?:經認可.{{0,30}}?)?線上課程.{{0,60}}?"
            rf"最多可?折抵(?P<maximum>{MANUAL_NUMBER_TOKEN})學分",
            compact,
        )
        if online and (maximum := _manual_number(online.group("maximum"))) is not None:
            add_maximum(
                {
                    "kind": "course_attributes",
                    "course_attributes": ["online"],
                    "recognition_required": "經認可" in online.group(0),
                },
                maximum,
                page,
                online.group(0),
            )

        home_courses = re.search(
            rf"屬於就讀系所.{{0,40}}?課程"
            rf"至多以(?P<maximum>{MANUAL_NUMBER_TOKEN})學分計",
            compact,
        )
        if home_courses and (
            maximum := _manual_number(home_courses.group("maximum"))
        ) is not None:
            add_maximum(
                {
                    "kind": "course_eligibility",
                    "included_affiliations": [
                        "home_department",
                        "double_major",
                        "minor",
                    ],
                },
                maximum,
                page,
                home_courses.group(0),
            )

        orca = re.search(
            rf"ORCA.{{0,80}}?微學分.{{0,80}}?至多以?"
            rf"(?P<maximum>{MANUAL_NUMBER_TOKEN})學分計",
            compact,
            re.I,
        )
        if orca and (maximum := _manual_number(orca.group("maximum"))) is not None:
            add_maximum(
                {
                    "kind": "course_attributes",
                    "course_attributes": ["orca_microcredit_workshop"],
                },
                maximum,
                page,
                orca.group(0),
            )

        cross_college = re.search(
            rf"其它至多(?P<maximum>{MANUAL_NUMBER_TOKEN})學分"
            rf"得以通識跨院選修學分抵免",
            compact,
        )
        if cross_college and (
            maximum := _manual_number(cross_college.group("maximum"))
        ) is not None:
            add_maximum(
                {
                    "kind": "course_attributes",
                    "course_attributes": ["cross_college_general_education"],
                    "student_categories": ["cross_college_bachelor"],
                },
                maximum,
                page,
                cross_college.group(0),
            )

        core_overflow = re.search(
            rf"核心課程超過(?P<maximum>{MANUAL_NUMBER_TOKEN})學分"
            rf".{{0,80}}?多餘.{{0,30}}?納入選修學分",
            compact,
        )
        core_overflow_prefix = (
            compact[max(0, core_overflow.start() - 100) : core_overflow.start()]
            if core_overflow
            else ""
        )
        cohort_specific_overflow = bool(
            re.search(r"(?:學年度|學期).{0,50}?以前", core_overflow_prefix)
        )
        if not cohort_specific_overflow and core_overflow and (
            maximum := _manual_number(core_overflow.group("maximum"))
        ) is not None:
            add_maximum(
                {"kind": "catalog_filter", "requirement_groups": ["core"]},
                maximum,
                page,
                core_overflow.group(0),
                destination="elective",
            )

        attribute_cap = re.search(
            rf"各課程屬性.{0,20}?僅採計"
            rf"(?P<maximum>{MANUAL_NUMBER_TOKEN})學.{{0,30}}?分",
            compact,
        )
        if attribute_cap and (
            maximum := _manual_number(attribute_cap.group("maximum"))
        ) is not None:
            program_course_names = list(
                dict.fromkeys(
                    course.get("program_course_name_snapshot")
                    for course in courses
                    if course.get("program_course_name_snapshot")
                )
            )
            for name in program_course_names:
                add_maximum(
                    {"kind": "program_course_names", "program_course_names": [name]},
                    maximum,
                    page,
                    attribute_cap.group(0),
                )

        spectrum = re.search(
            r"有機光譜概論.{0,20}?和.{0,20}?有機光譜學"
            r".{0,30}?不得重複認列",
            compact,
        )
        if spectrum:
            names = {"有機光譜概論", "有機光譜學"}
            entries = [
                course["catalog_entry_id"]
                for course in courses
                if course.get("course_name_snapshot") in names
            ]
            if len(entries) == 2:
                add_maximum(
                    {"kind": "catalog_entries", "catalog_entry_ids": entries},
                    3,
                    page,
                    spectrum.group(0),
                )

        same_level = re.search(
            r"同程度課程不得重複計算為核心學分",
            compact,
        )
        if same_level:
            level_groups = (
                ("日文(一)", "工程日文(一)", "商用日文(一)", "法學日文(一)"),
                ("日文(二)", "工程日文(二)", "商用日文(二)", "法學日文(二)"),
            )
            for names in level_groups:
                entries = [
                    course["catalog_entry_id"]
                    for course in courses
                    if _compact(course.get("course_name_snapshot"))
                    in {_compact(name) for name in names}
                ]
                if len(entries) >= 2:
                    add_maximum(
                        {"kind": "catalog_entries", "catalog_entry_ids": entries},
                        3,
                        page,
                        same_level.group(0),
                        destination="elective",
                    )

    return list(
        {
            item["constraint_id"]: item
            for item in constraints
        }.values()
    )


def _sentence_fragments(text: str) -> Iterable[str]:
    normalized = unicodedata.normalize("NFKC", text)
    # A single newline in extracted PDF text is usually visual wrapping, not a
    # semantic sentence boundary. Preserve wrapped official rules intact.
    for fragment in re.split(
        r"(?<=[。；;])|\n{2,}|"
        r"\n(?=(?:※|\d+[.)、]|[一二三四五六七八九十]+、))",
        normalized,
    ):
        value = _display_text(fragment)
        if value:
            yield value[:800]


def extract_manual_requirements(pages: list[tuple[int, str]]) -> list[dict]:
    """Preserve explicit non-credit/conditional rules that cannot be safely auto-evaluated."""
    requirements: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for page, text in pages:
        page_display = _display_text(text)
        certificate_page = bool(
            re.search(r"欲取得.{0,80}HumanRightsCertificate", _compact(text), re.I)
        )
        alternatives = re.search(
            r"(?P<workshop>參加至少一場.{0,120}?工作坊)\s*[,，]?\s*或\s*"
            r"(?P<program>規劃.{0,100}?(?:個人化)?\(?U\)?學程)",
            page_display,
            re.I,
        )
        if alternatives:
            option_texts = [alternatives.group("workshop"), alternatives.group("program")]
            description = "，或".join(_display_text(option) for option in option_texts)
            identity = ("other", _compact(description))
            seen.add(identity)
            requirements.append(
                {
                    "requirement_id": _stable_id(
                        "requirement", "supplemental_any_of", description
                    ),
                    "kind": "manual_verification",
                    "requirement_type": "other",
                    "description": description,
                    "verification_mode": "manual_review",
                    "requirement_context": "program_completion",
                    "satisfaction": "any_of",
                    "options": [
                        {"description": _display_text(option)} for option in option_texts
                    ],
                    **_evidence(page, alternatives.group(0)),
                }
            )
        alternative_compact = _compact(alternatives.group(0)) if alternatives else ""
        for fragment in _sentence_fragments(text):
            compact = _compact(fragment)
            if alternative_compact and (
                compact in alternative_compact or alternative_compact in compact
            ):
                continue
            matches = [
                (requirement_type, match)
                for requirement_type, pattern in MANUAL_RULE_TYPES
                if (match := pattern.search(compact)) is not None
            ]
            workshop_count: float | None = None
            if any(item[0] == "workshop_attendance" for item in matches):
                count_match = next(
                    (
                        match
                        for requirement_type, match in matches
                        if requirement_type == "activity_count"
                    ),
                    None,
                )
                if count_match is not None:
                    workshop_count = _manual_number(count_match.group("number"))
                    matches = [item for item in matches if item[0] != "activity_count"]
            for requirement_type, match in matches:
                if requirement_type in {"service_learning", "report", "approval"} and len(
                    fragment
                ) > 500:
                    continue
                matched_description = _display_text(match.group(0))
                identity = (requirement_type, _compact(matched_description))
                if identity in seen:
                    continue
                seen.add(identity)
                requirement = {
                    "requirement_id": _stable_id(
                        "requirement", requirement_type, matched_description
                    ),
                    "kind": "manual_verification",
                    "requirement_type": requirement_type,
                    "description": matched_description,
                    "verification_mode": "manual_review",
                    "requirement_context": (
                        "certificate"
                        if certificate_page or requirement_type == "certificate"
                        else "program_completion"
                    ),
                    **_evidence(page, matched_description),
                }
                if requirement_type == "activity_hours":
                    value = _manual_number(match.group("number"))
                    if value is not None:
                        requirement["minimum_hours"] = value
                elif requirement_type == "activity_count":
                    value = _manual_number(match.group("number"))
                    if value is not None:
                        requirement["minimum_count"] = int(value)
                elif requirement_type == "workshop_attendance" and workshop_count is not None:
                    requirement["minimum_count"] = int(workshop_count)
                requirements.append(requirement)
    return sorted(requirements, key=lambda item: (item["source_page"], item["requirement_id"]))


def extract_completion_requirements(
    table_rows: list[dict], version_pages: list[tuple[int, str]]
) -> dict:
    """Extract completion thresholds separately from declared course-pool sizes."""
    result = _extract_scalar_requirements(table_rows, version_pages)
    labeled = _extract_labeled_credit_constraints(table_rows)
    additional = _additional_credit_constraints(table_rows, version_pages)
    if labeled or additional:
        existing = {item["constraint_id"]: item for item in result.get("credit_constraints", [])}
        existing.update({item["constraint_id"]: item for item in [*labeled, *additional]})
        result["credit_constraints"] = sorted(
            existing.values(), key=lambda item: (item["source_page"], item["constraint_id"])
        )
    manual = extract_manual_requirements(version_pages)
    if manual:
        result["manual_requirements"] = manual
    return result


def finalize_completion_summary(requirements: dict, *, approved: bool = False) -> None:
    conflicts = requirements.get("source_conflicts", [])
    manual = requirements.get("manual_requirements", [])
    completion_manual = [
        item
        for item in manual
        if item.get("requirement_context", "program_completion") == "program_completion"
    ]
    unresolved = any(item.get("resolution_status") == "unresolved" for item in conflicts)
    total_unresolved = any(
        item.get("resolution_status") == "unresolved"
        and item.get("semantic_key") == "program.minimum_total_credits"
        for item in conflicts
    )
    has_executable_model = requirements.get("minimum_total_credits") is not None and bool(
        requirements.get("credit_constraints")
        or requirements.get("entry_selection_constraints")
        or requirements.get("course_count_constraints")
    )
    requirements["completion_summary"] = {
        "model_status": (
            "conflicted"
            if unresolved
            else (
                "complete"
                if approved and not completion_manual and has_executable_model
                else "partial"
            )
        ),
        "minimum_total_credits": (
            None if total_unresolved else requirements.get("minimum_total_credits")
        ),
        "minimum_core_credits": requirements.get("minimum_core_credits"),
        "maximum_core_credits": requirements.get("maximum_core_credits"),
        "minimum_elective_credits": requirements.get("minimum_elective_credits"),
        "minimum_core_courses": requirements.get("minimum_core_courses"),
        "minimum_elective_courses": requirements.get("minimum_elective_courses"),
        "has_conditional_requirements": any(
            item.get("requirement_type") == "student_condition"
            for item in completion_manual
        ),
        "has_manual_requirements": bool(manual),
        "has_unresolved_conflicts": unresolved,
    }
