# injection-probe

**Does a hidden instruction in a document reach your model?**

A pipeline that sends a whole document to an LLM — translation, summarisation,
RAG ingestion — is only as safe as its text extractor. If the extractor pulls
in text a human can never see (white on white, 1pt, off the page, behind
`display:none`, in the Word *hidden* flag), a document can carry an
instruction that rewrites its own output. The visible content shows nothing.

Existing scanners answer *"does this file contain hidden text?"*. This answers
the question that actually decides your risk: *of the instructions a human
cannot see, how many does **my** extraction step hand to the model?*

```
  15 probes across 4 extractor(s)
  13 hidden instruction(s) reached the extracted text

  EXTRACTOR: pymupdf   4/5 exposed
  ------------------------------------------------------------
    EXPOSED  pdf   payload in document metadata
    EXPOSED  pdf   invisible text render mode (mode 3)
    EXPOSED  pdf   sub-legible font size (1pt)
    EXPOSED  pdf   white text on white background
       safe  pdf   text placed outside the page box

  EXTRACTOR: pypdf   4/5 exposed
  ------------------------------------------------------------
    EXPOSED  pdf   text placed outside the page box
    ...
```

Note that PyMuPDF and pypdf do not expose the same set — the vulnerability
depends on which library you use. That is a finding you cannot get from a
scanner that looks at files instead of pipelines.

## Install

```bash
claude plugin marketplace add talhayme/claude-plugins
claude plugin install injection-probe@talhayme
```

## Use

Against your own extractor — the real test:

```bash
python3 scripts/probe.py --extractor "python3 my_pipeline/extract.py {}"
```

`{}` becomes each generated document's path; the command's stdout is the
extracted text. If a planted instruction appears there, you are exposed.

Or just ask — the skill fires on its own:

> Can a PDF sneak hidden instructions past my extraction into the model?

## Techniques covered

| Format | Technique |
|---|---|
| PDF | white text, 1pt font, off-page, invisible render mode (mode 3), metadata |
| DOCX | white run colour, Word hidden flag (`w:vanish`), 1pt font |
| EPUB | CSS `display:none`, white colour |

These are the carriers that survive ordinary extraction. Exotic steganography
(acrostics, microglyphs) is deliberately out of scope — it needs a different
detector and rarely survives a translation pipeline intact.

## The fix it points to

Always the same shape: **strip non-visible content before the text reaches the
model.** Honour render mode, colour against background, font size, page
bounds, the vanish flag, and CSS visibility at extraction time; never feed raw
metadata into the prompt. Re-run the probe against your hardened extractor and
watch the exposed count go to zero.

## In CI

```yaml
- run: python3 scripts/probe.py --extractor "python3 extract.py {}" --fail-on-exposure
```

Exit 1 on any surviving instruction, so a regression in extraction hardening
fails the build.

## What "exposed" means — and doesn't

Exposed means the hidden instruction reached the extracted text. Whether the
model then *obeys* it is a separate question this does not test. But text the
model never receives cannot be obeyed, so closing extraction exposure is the
first and cheapest line of defence — before any prompt-level mitigation.

## Requirements

Python 3.9+. `PyMuPDF` and `python-docx` for the built-in PDF/DOCX extractors;
EPUB uses the standard library. With `--extractor`, only your own command's
dependencies are needed.

## Tests

```bash
python3 -m pytest tests/ -q
# 19 passed
```

Covers every carrier building, each hidden technique surviving its extractor,
a clean document not being flagged, the external-extractor path, and all CLI
exit codes.

## License

MIT
