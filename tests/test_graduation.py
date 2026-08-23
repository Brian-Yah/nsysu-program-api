from pathlib import Path

from nsysu_program_api.core import write_json
from nsysu_program_api.graduation import (
    build_graduation_api,
    materialize_graduation_requirements_from_rules,
    parse_department_options,
    parse_entry_year_options,
    parse_graduation_requirement,
    requirement_url,
)


def test_department_options_filter_degree_level():
    html = """
    <select name="DPT1">
      <option value="B4020">B4020 資訊管理學系</option>
      <option value="B8070">B8070 護理學系</option>
      <option value="M4020">M4020 資訊管理學系碩士班</option>
    </select>
    """
    assert parse_department_options(html) == [
        {"department_code": "B4020", "department_name": "資訊管理學系"},
        {"department_code": "B8070", "department_name": "護理學系"},
    ]


def test_entry_year_options_are_discovered_from_official_selector():
    html = """
    <select name="YY1">
      <option value=115>115</option>
      <option value=114>114</option>
      <option value=113>113</option>
      <option value=112>112</option>
      <option value=bad>請選擇</option>
    </select>
    """
    assert parse_entry_year_options(html) == ["112", "113", "114", "115"]


def test_parse_minimum_graduation_credits():
    html = """
    <table>
      <tr>
        <td>最低畢<br>業學分數</td><td>135</td>
        <td>必修比重</td><td>67.41%</td>
      </tr>
    </table>
    """
    assert parse_graduation_requirement(html) == {
        "minimum_graduation_credits": 135,
        "required_course_ratio": 67.41,
    }


def test_zero_credit_table_is_not_an_active_requirement():
    html = "<table><tr><td>最低畢<br>業學分數</td><td>0</td></tr></table>"
    assert parse_graduation_requirement(html) is None


def test_requirement_url_is_stable():
    assert requirement_url("115", "B4020").endswith("action=3&YY1=115&DPT1=B4020")


def test_build_graduation_api_writes_collection_and_department(tmp_path: Path):
    requirement = {
        "department_code": "B4020",
        "department_name": "資訊管理學系",
        "degree_level": "bachelor",
        "entry_academic_year": "115",
        "minimum_graduation_credits": 135,
        "required_course_ratio": 67.41,
        "source": {
            "url": requirement_url("115", "B4020"),
            "retrieved_at": "2026-08-15T00:00:00Z",
            "binary_sha256": "a" * 64,
            "http_status": 200,
            "parser_version": "test",
        },
    }
    dataset = {
        "schema_version": "1.0",
        "entry_academic_year": "115",
        "degree_level": "bachelor",
        "retrieved_at": "2026-08-15T00:00:00Z",
        "department_count": 1,
        "requirements": [requirement],
        "unavailable_departments": [],
        "source": {
            "index_url": "https://example.test",
            "index_binary_sha256": "b" * 64,
            "parser_version": "test",
        },
    }
    write_json(tmp_path / "data/graduation-requirements/115/bachelor.json", dataset)
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    for name in ("graduation-requirement.schema.json", "graduation-requirements.schema.json"):
        (schemas / name).write_text("{}\n", encoding="utf-8")
    stale = tmp_path / "api/v1/graduation-requirements/115/bachelor/B0000.json"
    write_json(stale, {"stale": True})

    result = build_graduation_api(tmp_path, "115")

    assert result["department_count"] == 1
    assert (tmp_path / "api/v1/graduation-requirements/115/bachelor.json").exists()
    assert (tmp_path / "api/v1/graduation-requirements/115/bachelor/B4020.json").exists()
    assert (tmp_path / "api/v1/graduation-requirements/latest/bachelor/B4020.json").exists()
    assert not stale.exists()


def test_materialize_compact_requirement_from_pinned_department_rule(tmp_path: Path):
    rule = {
        "department_code": "B4020",
        "department_name_zh": "資訊管理學系",
        "credit_requirements": {"minimum_graduation_credits": 135},
        "sources": [
            {
                "url": requirement_url("112", "B4020"),
                "sha256": "a" * 64,
            }
        ],
    }
    write_json(
        tmp_path / "data/graduation-rules/112/bachelor/B4020.json", rule
    )
    dataset = materialize_graduation_requirements_from_rules(
        tmp_path, "112", "b" * 64
    )

    assert dataset["department_count"] == 1
    requirement = dataset["requirements"][0]
    assert requirement["minimum_graduation_credits"] == 135
    assert requirement["required_course_ratio"] is None
    assert requirement["source"]["binary_sha256"] == "a" * 64
