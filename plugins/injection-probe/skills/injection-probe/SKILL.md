---
name: injection-probe
description: Test whether a document pipeline is exposed to hidden prompt injection. Existing scanners answer "does this file contain hidden text"; this answers the question that matters for a pipeline that translates or summarises a whole document — of the instructions a human cannot see, how many does your extraction step pull into the text sent to the model. Builds a document per hiding technique (white text, 1pt font, off-page, invisible render mode, PDF metadata, Word vanish flag, EPUB display:none) across PDF, DOCX and EPUB, runs each through your extractor, and reports per technique whether the planted instruction survived. Use when building or auditing a RAG, translation, summarisation or document-intake pipeline, when asked whether hidden instructions in a document can reach the model, or to add an injection check to CI.
argument-hint: "[--format pdf,docx,epub] [--extractor CMD] [--fail-on-exposure]"
allowed-tools: Bash(python3:*), Read
---

# Injection probe

A pipeline that sends a whole document to a model is only as safe as its text
extractor. If the extractor pulls in text a human can never see — white on
white, 1pt, off the page, behind `display:none` — then a document can carry an
instruction that rewrites its own translation, and nothing in the visible
content shows it.

This measures that exposure for a specific pipeline.

## Run it

Against the built-in extractors (PyMuPDF, pypdf, python-docx, stdlib EPUB):

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/probe.py" $ARGUMENTS
```

Against the pipeline's **own** extractor — this is the real test:

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/probe.py" \
  --extractor "python3 my_pipeline/extract.py {}"
```

`{}` is replaced with the path to each generated document; the command's
stdout is treated as the extracted text. If the planted instruction appears
in that stdout, the pipeline is exposed.

## Reading the result

Each line is a hiding technique against one extractor, marked `EXPOSED` or
`safe`. `EXPOSED` means the hidden instruction reached the extracted text —
the model would receive it as if it were part of the document.

Report the exposed count first, then which techniques and which extractor.
The difference between extractors is itself a finding: PyMuPDF and pypdf do
not expose the same set, so the vulnerability depends on which library the
pipeline uses.

Then give the fix, which is always the same shape: **strip non-visible
content before the text reaches the model.** Concretely — honour render mode,
font colour vs. background, font size, page bounds, the Word vanish flag and
CSS visibility at extraction time; do not feed raw metadata into the prompt.

## Honest scope

- The carriers model injection that survives ordinary extraction, not exotic
  steganography (acrostics, microglyphs). Those need a different detector and
  are out of scope on purpose — they rarely survive a translation pipeline
  intact anyway.
- "Exposed" means the instruction reached the extracted text. Whether the
  model then *obeys* it is a separate question this does not test — but text
  the model never receives cannot be obeyed, so closing extraction exposure
  is the first and cheapest line of defence.

## Flags

- `--format pdf,docx,epub` — limit to certain formats (default: all three).
- `--extractor CMD` — measure your own extractor instead of the built-ins.
- `--fail-on-exposure` — exit 1 if any instruction survived, for CI.
- `--json` — machine-readable output.
- `--keep DIR` — write the generated adversarial documents to DIR for
  inspection instead of a temp directory.
