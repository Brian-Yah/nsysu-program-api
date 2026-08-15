from nsysu_program_api.core import (
    Fetcher,
    _join_wrapped_text,
    _split_units,
    apply_course_count_constraints,
    extract_course_count_constraints,
    extractor_versions,
    normalize_text,
    parse_catalog,
    pdf_academic_version,
    split_responsible,
)


def test_normalize_text():
    assert normalize_text(" Ａ  \n\n B ") == "A\nB"


def test_catalog_count_is_dynamic():
    html = (
        b"<table><tr><th>Name</th></tr><tr><td>A</td><td>Desc</td>"
        b"<td>Unit Person</td><td><a href='https://x'>web</a></td>"
        b"<td><a href='/a.pdf'>pdf</a></td></tr></table>"
    )
    result = parse_catalog(html, "2026-01-01T00:00:00Z", {})
    assert len(result) == 1
    assert result[0]["source_pdf"] == "https://ctdr.nsysu.edu.tw/a.pdf"


def test_pdf_academic_version():
    assert pdf_academic_version("【114 學年度第 2 學期起適用】") == "114-2"
    assert pdf_academic_version("no version") is None


def test_pdf_visual_line_wrapping_is_joined():
    assert _join_wrapped_text("機器學習系統設計實務與應\n用") == "機器學習系統設計實務與應用"
    assert _join_wrapped_text("MACHINE\nLEARNING") == "MACHINE LEARNING"


def test_wrapped_unit_is_not_split_but_real_units_are():
    assert _split_units("臺灣大專院校人\n工智慧學程聯盟") == ["臺灣大專院校人工智慧學程聯盟"]
    assert _split_units("應數系\n光電系\n電機系\n跨院選修(工)") == [
        "應數系",
        "光電系",
        "電機系",
        "跨院選修(工)",
    ]


def test_responsible_unit_and_coordinator_lines():
    assert split_responsible("物理系\n黃信銘副教授") == ("物理系", "黃信銘副教授")


def test_fetcher_network_settings_from_environment(monkeypatch):
    monkeypatch.setenv("NSYSU_API_TIMEOUT", "75")
    monkeypatch.setenv("NSYSU_API_ATTEMPTS", "5")
    fetcher = Fetcher("test-agent", delay=0)
    assert fetcher.timeout == 75
    assert fetcher.attempts == 5


def test_fetcher_network_settings_are_bounded(monkeypatch):
    monkeypatch.setenv("NSYSU_API_TIMEOUT", "999")
    monkeypatch.setenv("NSYSU_API_ATTEMPTS", "999")
    fetcher = Fetcher("test-agent", delay=0)
    assert fetcher.timeout == 180
    assert fetcher.attempts == 8


def test_extractor_versions_are_explicit():
    versions = extractor_versions()
    assert set(versions) == {"pypdf", "pdfplumber"}
    assert versions["pypdf"] is not None


def test_extracts_max_one_course_constraint_and_preserves_notes():
    courses = [
        {
            "course_name_snapshot": name,
            "requirement_group": "core",
            "source_page": 1,
            "notes": "",
        }
        for name in ("線性代數", "線性代數(一)", "線性代數(二)")
    ]
    page_text = """開課單位 課程名稱 學分數 備註
線性代數、線性代數 (一) 、線性代數 (二) 等
校內各系 線性代數 3
課程至多採認一科。
線性代數、線性代數 (一) 、線性代數 (二) 等
校內各系 線性代數(一) 3
課程至多採認一科。"""

    constraints = extract_course_count_constraints(page_text, courses, 1)
    assert len(constraints) == 1
    assert constraints[0]["kind"] == "max_courses"
    assert constraints[0]["course_names"] == [
        "線性代數",
        "線性代數(一)",
        "線性代數(二)",
    ]
    assert constraints[0]["max_courses"] == 1
    assert constraints[0]["validation_status"] == "source_text_match"

    requirements = {}
    apply_course_count_constraints(page_text, courses, requirements, 1)
    assert requirements["course_count_constraints"][0]["max_courses"] == 1
    assert all("至多採認一科" in course["notes"] for course in courses)


def test_course_count_constraint_requires_multiple_matching_courses():
    courses = [
        {
            "course_name_snapshot": "線性代數",
            "requirement_group": "core",
            "source_page": 1,
            "notes": "",
        }
    ]
    page_text = "校內各系 線性代數 3\n線性代數課程至多採認一科。"
    assert extract_course_count_constraints(page_text, courses, 1) == []


def test_course_count_constraint_can_reference_courses_across_pdf_pages():
    names = ("財務管理", "財務管理(一)", "財務管理(二)", "財務管理概論", "財務管理理論")
    courses = [
        {
            "course_name_snapshot": name,
            "requirement_group": "core",
            "source_page": 1 if index < 2 else 2,
            "notes": "",
        }
        for index, name in enumerate(names)
    ]
    page_text = """財務管理、財務管理(一)、財務管理(二)、
校內各系 財務管理 3 財務管理概論、財務管理理論等課程至多採認
一科。"""

    constraints = extract_course_count_constraints(page_text, courses, 1)
    assert constraints[0]["course_names"] == list(names)
    assert constraints[0]["max_courses"] == 1
