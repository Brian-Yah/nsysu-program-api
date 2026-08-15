from nsysu_program_api.core import (
    Fetcher,
    _join_wrapped_text,
    _split_units,
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
