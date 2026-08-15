from nsysu_program_api import pipeline
from nsysu_program_api.core import PARSER_VERSION, sha256


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
                "warnings": [],
            }
        ],
    }
    monkeypatch.setattr(pipeline, "extract_pdf_text", lambda _path: ("text", []))
    monkeypatch.setattr(pipeline, "extract_pdf_tables", lambda _path: ([], []))

    result = pipeline.process_pdfs(tmp_path, catalog, "test-agent", reuse_cache=True)

    assert result["cache_reused"] == 1
    assert catalog["programs"][0]["source"]["parser_version"] == PARSER_VERSION
