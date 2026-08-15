from nsysu_program_api.pipeline import semantic_diff


def test_semantic_diff_detects_url_and_hash():
    base = {
        "program_id": "p",
        "name_zh": "A",
        "status": "active",
        "responsible_unit": "U",
        "coordinator": "C",
        "source_pdf": "x",
        "source": {"pdf_binary_sha256": "1", "normalized_text_sha256": "a"},
    }
    new = {
        **base,
        "source_pdf": "y",
        "source": {"pdf_binary_sha256": "2", "normalized_text_sha256": "a"},
    }
    result = semantic_diff({"programs": [base]}, {"programs": [new]})
    assert result["summary"]["changed"] == 1
    assert {x["field"] for x in result["changed"][0]["changes"]} == {
        "source_pdf",
        "source.pdf_binary_sha256",
    }
