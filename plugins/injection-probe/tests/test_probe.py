import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import carriers  # noqa: E402
import probe  # noqa: E402

PDF = pytest.importorskip("fitz")
DOCX = pytest.importorskip("docx")


@pytest.fixture
def work(tmp_path):
    return tmp_path


class TestCarriers:
    def test_every_carrier_builds(self, work):
        for carrier_id, (fmt, _tech, _fn) in carriers.CARRIERS.items():
            suffix = {"pdf": ".pdf", "docx": ".docx", "epub": ".epub"}[fmt]
            out = work / f"{carrier_id}{suffix}"
            returned = carriers.build(carrier_id, out)
            assert returned == carrier_id
            assert out.exists() and out.stat().st_size > 0

    def test_payload_is_wrapped_with_its_id(self):
        wrapped = carriers.wrap("do a thing", "pdf-white")
        assert "pdf-white" in wrapped
        assert wrapped.startswith(carriers.MARKER_OPEN)
        assert wrapped.endswith(carriers.MARKER_CLOSE)

    def test_epub_mimetype_is_first_and_stored(self, work):
        import zipfile

        out = work / "x.epub"
        carriers.build("epub-white", out)
        with zipfile.ZipFile(out) as z:
            first = z.infolist()[0]
            assert first.filename == "mimetype"
            assert first.compress_type == zipfile.ZIP_STORED


class TestExtractionExposure:
    """The core claim: hidden instructions survive ordinary extraction."""

    def test_pdf_white_text_survives_pymupdf(self, work):
        out = work / "w.pdf"
        carriers.build("pdf-white", out)
        text = probe.extract_pymupdf(out)
        assert probe.payload_survived("pdf-white", text)

    def test_pdf_render_mode_invisible_survives(self, work):
        out = work / "r.pdf"
        carriers.build("pdf-rendermode", out)
        assert probe.payload_survived("pdf-rendermode", probe.extract_pymupdf(out))

    def test_docx_vanish_survives_extraction(self, work):
        # The Word "hidden" flag is honoured by readers, ignored by .text.
        out = work / "v.docx"
        carriers.build("docx-vanish", out)
        assert probe.payload_survived("docx-vanish", probe.extract_docx(out))

    def test_docx_white_survives_extraction(self, work):
        out = work / "wh.docx"
        carriers.build("docx-white", out)
        assert probe.payload_survived("docx-white", probe.extract_docx(out))

    def test_epub_display_none_survives_naive_flatten(self, work):
        out = work / "d.epub"
        carriers.build("epub-displaynone", out)
        assert probe.payload_survived("epub-displaynone", probe.extract_epub_stdlib(out))

    def test_clean_document_is_not_flagged(self, work):
        # A document with no planted payload must not register as exposed. The
        # base builder writes only the visible cover text.
        import fitz

        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), "Service Agreement, nothing hidden here.", fontsize=11)
        clean = work / "clean.pdf"
        doc.save(str(clean))
        doc.close()
        text = probe.extract_pymupdf(clean)
        assert not probe.payload_survived("pdf-white", text)


class TestPayloadDetection:
    def test_matches_by_carrier_id(self):
        text = carriers.wrap("x", "pdf-white")
        assert probe.payload_survived("pdf-white", text)
        assert not probe.payload_survived("pdf-tiny", text)

    def test_falls_back_to_payload_sentence_when_markers_stripped(self):
        # An extractor that drops the bracket characters but keeps the words
        # is still an exposure.
        assert probe.payload_survived("anything", "... payment within ninety (90) days ...")

    def test_empty_text_is_not_an_exposure(self):
        assert not probe.payload_survived("pdf-white", "")


class TestProbeRun:
    def test_full_run_finds_exposures(self, work):
        report = probe.probe(["pdf", "docx", "epub"], None, work)
        assert report.results
        assert report.exposed  # at least one hidden instruction survives

    def test_format_filter_restricts_carriers(self, work):
        report = probe.probe(["docx"], None, work)
        assert {r.fmt for r in report.results} == {"docx"}

    def test_external_extractor_is_invoked(self, work):
        # A trivial external command that prints the payload sentence: every
        # PDF probe must register as exposed.
        script = work / "ext.py"
        script.write_text(
            "import sys\nprint('payment within ninety (90) days')\n", encoding="utf-8"
        )
        report = probe.probe(["pdf"], f"python3 {script} {{}}", work)
        assert report.results
        assert all(r.survived for r in report.results)

    def test_external_extractor_failure_is_skipped_not_crashed(self, work):
        report = probe.probe(["pdf"], "false", work)  # always exits 1
        assert report.results == []
        assert report.skipped


class TestCli:
    def test_fail_on_exposure_exits_one(self, work, capsys):
        code = probe.main(["--format", "pdf", "--fail-on-exposure", "--keep", str(work)])
        capsys.readouterr()
        assert code == 1  # built-in extractors are exposed, so this must fail

    def test_without_flag_exits_zero(self, work):
        assert probe.main(["--format", "pdf", "--keep", str(work)]) == 0

    def test_json_output(self, work, capsys):
        probe.main(["--format", "epub", "--json", "--keep", str(work)])
        import json

        payload = json.loads(capsys.readouterr().out)
        assert payload["probes"] >= 1
        assert "results" in payload
