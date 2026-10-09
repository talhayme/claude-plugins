#!/usr/bin/env python3
"""Measure what a document pipeline does with hidden instructions.

Existing tools answer "does this PDF contain hidden text?". That is the easy
question. The one that matters for a pipeline that *translates* or summarises
a whole document is: of the instructions a human can never see, how many does
my extraction step pull into the text I send to the model?

This builds a document per hiding technique, runs each through the pipeline's
own extractor, and reports — per technique — whether the planted instruction
survived into the extracted text. That is the exposure. A detector tells you a
file is dangerous; this tells you whether *your* pipeline is exposed to it.

Two extractors are built in (PyMuPDF and pypdf for PDF, python-docx for DOCX,
stdlib for EPUB) so it runs out of the box. Point `--extractor` at your own
command to measure the real thing.

No network, no model calls. The generated documents are adversarial by design
and are written only under the output directory you name.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import carriers  # noqa: E402

MARKER_RE = re.compile(
    re.escape(carriers.MARKER_OPEN) + r"([a-z0-9-]+)\]\](.*?)" + re.escape(carriers.MARKER_CLOSE),
    re.S,
)


@dataclass
class Result:
    carrier_id: str
    fmt: str
    technique: str
    extractor: str
    survived: bool                 # the planted instruction reached extracted text
    extracted_chars: int
    note: str = ""


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    @property
    def exposed(self) -> list[Result]:
        return [r for r in self.results if r.survived]


# ---------------------------------------------------------------------------
# Built-in extractors. Each takes a file path and returns extracted text, or
# raises to signal "this extractor cannot read this format".
# ---------------------------------------------------------------------------

def extract_pymupdf(path: Path) -> str:
    import fitz

    doc = fitz.open(str(path))
    parts = [page.get_text() for page in doc]
    # Some pipelines fold metadata into the content; include it so the probe
    # reflects that choice rather than hiding a real exposure.
    meta = doc.metadata or {}
    parts.append(" ".join(str(v) for v in meta.values() if v))
    doc.close()
    return "\n".join(parts)


def extract_pypdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        import PyPDF2  # type: ignore
        reader = PyPDF2.PdfReader(str(path))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    reader = PdfReader(str(path))
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    md = reader.metadata or {}
    text += "\n" + " ".join(str(v) for v in md.values() if v)
    return text


def extract_docx(path: Path) -> str:
    import docx

    doc = docx.Document(str(path))
    # python-docx reads hidden and white runs the same as any other — which is
    # exactly the point. .text does not consult the vanish flag or colour.
    return "\n".join(p.text for p in doc.paragraphs)


def extract_epub_stdlib(path: Path) -> str:
    """Flatten EPUB XHTML the way a naive get_text() pipeline would —
    stripping tags without honouring display:none or colour."""
    import zipfile

    chunks: list[str] = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.endswith((".xhtml", ".html", ".htm")):
                raw = z.read(name).decode("utf-8", "replace")
                chunks.append(re.sub(r"<[^>]+>", " ", raw))
    return "\n".join(chunks)


BUILTIN = {
    "pdf": {"pymupdf": extract_pymupdf, "pypdf": extract_pypdf},
    "docx": {"python-docx": extract_docx},
    "epub": {"stdlib": extract_epub_stdlib},
}


def run_external(command: str, path: Path) -> str:
    """Run a user-supplied extractor. `{}` in the command is the file path;
    otherwise the path is appended. Pipeline's stdout is the extracted text."""
    args = command.split()
    args = [path.as_posix() if a == "{}" else a for a in args]
    if "{}" not in command:
        args.append(path.as_posix())
    proc = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"extractor exited {proc.returncode}: {proc.stderr[:200]}")
    return proc.stdout


def payload_survived(carrier_id: str, extracted: str) -> bool:
    """The instruction survived if its marked payload appears in extracted text.

    Match on the payload body, not the marker wrapper: a real extractor may
    drop the bracket characters while keeping the sentence, and the sentence is
    what reaches the model.
    """
    for found_id, body in MARKER_RE.findall(extracted):
        if found_id == carrier_id and body.strip():
            return True
    # Fall back to the payload sentence in case the markers were stripped but
    # the instruction text came through.
    needle = "payment within ninety"
    return needle in extracted.lower()


def probe(fmts: list[str], extractor: str | None, workdir: Path) -> Report:
    report = Report()

    for carrier_id, (fmt, technique, _builder) in carriers.CARRIERS.items():
        if fmt not in fmts:
            continue

        suffix = {"pdf": ".pdf", "docx": ".docx", "epub": ".epub"}[fmt]
        doc_path = workdir / f"{carrier_id}{suffix}"
        try:
            carriers.build(carrier_id, doc_path)
        except Exception as exc:  # noqa: BLE001
            report.skipped.append((carrier_id, f"could not build: {exc}"))
            continue

        # Choose extractors: external command for every format, or the built-ins.
        if extractor:
            runs = [(extractor, lambda p, c=extractor: run_external(c, p))]
        else:
            runs = list(BUILTIN.get(fmt, {}).items())

        for ext_name, fn in runs:
            try:
                text = fn(doc_path)
            except Exception as exc:  # noqa: BLE001
                report.skipped.append((f"{carrier_id}/{ext_name}", str(exc)[:120]))
                continue
            report.results.append(
                Result(
                    carrier_id=carrier_id,
                    fmt=fmt,
                    technique=technique,
                    extractor=ext_name,
                    survived=payload_survived(carrier_id, text),
                    extracted_chars=len(text),
                )
            )

    return report


def render(report: Report) -> str:
    lines: list[str] = [""]
    results = report.results
    if not results:
        lines.append("  No documents were probed. Check --format and that a PDF/DOCX")
        lines.append("  library is available, or pass --extractor for your own.")
        lines.append("")
        return "\n".join(lines)

    exposed = report.exposed
    by_ext: dict[str, list[Result]] = {}
    for r in results:
        by_ext.setdefault(r.extractor, []).append(r)

    lines.append(f"  {len(results)} probes across {len(by_ext)} extractor(s)")
    lines.append(f"  {len(exposed)} hidden instruction(s) reached the extracted text")
    lines.append("")

    for ext_name, group in by_ext.items():
        got = sum(1 for r in group if r.survived)
        lines.append(f"  EXTRACTOR: {ext_name}   {got}/{len(group)} exposed")
        lines.append(f"  {'-' * 60}")
        for r in sorted(group, key=lambda x: (not x.survived, x.carrier_id)):
            mark = "EXPOSED " if r.survived else "   safe "
            lines.append(f"    {mark} {r.fmt:5} {r.technique}")
        lines.append("")

    if exposed:
        lines.append("  Every EXPOSED line is an instruction a human cannot see that")
        lines.append("  your extractor handed to the model as if it were source text.")
        lines.append("  Strip hidden content before translation, or the document can")
        lines.append("  rewrite its own output.")
    else:
        lines.append("  No hidden instruction survived extraction with these carriers.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure whether hidden document instructions survive your pipeline's extraction."
    )
    parser.add_argument("--format", default="pdf,docx,epub",
                        help="comma-separated: pdf,docx,epub (default: all)")
    parser.add_argument("--extractor",
                        help="your extraction command; '{}' is the file path, stdout is the text. "
                             "Omit to use the built-in extractors.")
    parser.add_argument("--keep", type=Path,
                        help="write the generated documents here instead of a temp dir")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--fail-on-exposure", action="store_true",
                        help="exit 1 if any hidden instruction survived (for CI)")
    args = parser.parse_args(argv)

    fmts = [f.strip() for f in args.format.split(",") if f.strip()]

    workdir = args.keep or Path(tempfile.mkdtemp(prefix="injection-probe-"))
    workdir.mkdir(parents=True, exist_ok=True)
    report = probe(fmts, args.extractor, workdir)

    if args.json:
        print(json.dumps({
            "probes": len(report.results),
            "exposed": len(report.exposed),
            "results": [
                {"carrier": r.carrier_id, "format": r.fmt, "technique": r.technique,
                 "extractor": r.extractor, "survived": r.survived,
                 "extracted_chars": r.extracted_chars}
                for r in report.results
            ],
            "skipped": [{"what": w, "why": y} for w, y in report.skipped],
        }, indent=2))
    else:
        print(render(report))
        if report.skipped:
            print("  Skipped:")
            for what, why in report.skipped:
                print(f"    {what}: {why}")
            print()

    return 1 if (args.fail_on_exposure and report.exposed) else 0


if __name__ == "__main__":
    sys.exit(main())
