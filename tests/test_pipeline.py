from nsysu_program_api import pipeline
from nsysu_program_api.core import PARSER_VERSION, sha256, write_json
from nsysu_program_api.reviewed import (
    StaleReviewedOverrideError,
    apply_reviewed_override,
    course_catalog_sha256,
)


def test_cache_rebuild_refreshes_parser_version(tmp_path, monkeypatch):
    pdf = b"cached pdf"
    digest = sha256(pdf)
    cache = tmp_path / "cache" / "pdf"
    cache.mkdir(parents=True)
    (cache / f"{digest}.pdf").write_bytes(pdf)
    catalog = {
        "academic_version": "115-1",
        "programs": [
            {
                "program_id": "prog_test",
                "name_zh": "測試學程",
                "status": "active",
                "responsible_unit": "測試單位",
                "coordinator": "測試人員",
                "source_pdf": "https://example.test/program.pdf",
                "source": {
                    "pdf_binary_sha256": digest,
                    "parser_version": "0.0.0",
                    "http": {"status": 200},
                },
                "review_status": "source_only",
                "rules": {"kind": "manual_review"},
                "warnings": ["No structured course rows extracted from PDF tables"],
            }
        ],
    }
    monkeypatch.setattr(pipeline, "extract_pdf_text", lambda _path: ("text", []))
    monkeypatch.setattr(
        pipeline,
        "extract_pdf_tables",
        lambda _path: (
            [
                {
                    "pdf_academic_version": "114-1",
                    "courses": [{"catalog_entry_id": "entry_test"}],
                    "requirements": {},
                    "audit": {},
                }
            ],
            [],
        ),
    )

    result = pipeline.process_pdfs(tmp_path, catalog, "test-agent", reuse_cache=True)

    assert result["cache_reused"] == 1
    assert catalog["programs"][0]["source"]["parser_version"] == PARSER_VERSION
    assert catalog["programs"][0]["warnings"] == []


def test_reviewed_requirements_survive_rebuild_layer(tmp_path):
    catalog = [{"catalog_entry_id": "entry_a"}]
    program = {
        "program_id": "prog_test",
        "selected_pdf_academic_version": "114-1",
        "source": {
            "pdf_binary_sha256": "a" * 64,
            "normalized_text_sha256": "b" * 64,
            "parser_version": "0.3.0",
        },
        "course_catalog": catalog,
        "structured_requirements": {"minimum_total_credits": 99},
        "review_status": "needs_review",
    }
    write_json(
        tmp_path / "data" / "reviewed" / "115-1" / "prog_test.json",
        {
            "program_id": "prog_test",
            "academic_version": "115-1",
            "based_on": {
                "pdf_binary_sha256": "a" * 64,
                "normalized_text_sha256": "b" * 64,
                "selected_pdf_academic_version": "114-1",
                "parser_version": "0.3.0",
                "course_catalog_sha256": course_catalog_sha256(catalog),
            },
            "structured_requirements": {"minimum_total_credits": 9},
            "review_status": "needs_review",
            "review": {"reviewer": "test", "second_reviewer": None},
        },
    )
    assert apply_reviewed_override(tmp_path, "115-1", program)
    assert program["structured_requirements"]["minimum_total_credits"] == 9


def test_stale_reviewed_hash_fails_closed(tmp_path):
    catalog = [{"catalog_entry_id": "entry_a"}]
    program = {
        "program_id": "prog_test",
        "selected_pdf_academic_version": "114-1",
        "source": {
            "pdf_binary_sha256": "a" * 64,
            "normalized_text_sha256": "b" * 64,
            "parser_version": "0.3.0",
        },
        "course_catalog": catalog,
    }
    write_json(
        tmp_path / "data" / "reviewed" / "115-1" / "prog_test.json",
        {
            "program_id": "prog_test",
            "academic_version": "115-1",
            "based_on": {
                "pdf_binary_sha256": "c" * 64,
                "normalized_text_sha256": "b" * 64,
                "selected_pdf_academic_version": "114-1",
                "parser_version": "0.3.0",
                "course_catalog_sha256": course_catalog_sha256(catalog),
            },
            "structured_requirements": {},
        },
    )
    try:
        apply_reviewed_override(tmp_path, "115-1", program)
    except StaleReviewedOverrideError:
        pass
    else:
        raise AssertionError("stale reviewed data must fail closed")
