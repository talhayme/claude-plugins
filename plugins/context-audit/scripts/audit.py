#!/usr/bin/env python3
"""Measure what every installed skill costs you in context, on every request.

A skill's name, description and when_to_use are injected into the system
prompt of every single request — whether or not the skill is ever used. Ten
plugins with eight skills each is a standing tax you pay per turn and never
see itemised.

This reads the on-disk plugin layout, estimates the per-request cost of each
skill, and flags the ones that are expensive, duplicated, or over the limit
where Claude Code truncates them.

No network, no API calls, read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Claude Code truncates name + description + when_to_use at this length.
# Anything past it is silently dropped, so the author's intent never reaches
# the model.
DESCRIPTION_LIMIT = 1536

# Rough but stable: English prose runs ~4 characters per token. Exact counts
# need the tokenizer; for ranking and budgeting this is close enough and
# needs no dependency.
CHARS_PER_TOKEN = 4

# Above this, a description is doing more than helping Claude choose.
VERBOSE_DESCRIPTION_TOKENS = 120


@dataclass
class Skill:
    name: str
    source: str            # plugin name, or "personal" / "project"
    path: Path
    description: str
    when_to_use: str
    body_lines: int
    always_on: bool        # loaded even when the model would not pick it

    @property
    def prompt_text(self) -> str:
        """Exactly what gets injected per request."""
        return f"{self.name}\n{self.description}\n{self.when_to_use}".strip()

    @property
    def chars(self) -> int:
        return len(self.prompt_text)

    @property
    def tokens(self) -> int:
        return max(1, round(self.chars / CHARS_PER_TOKEN))

    @property
    def truncated_by(self) -> int:
        """Characters lost to the 1,536-char cap, if any."""
        combined = len(self.description) + len(self.when_to_use)
        return max(0, combined - DESCRIPTION_LIMIT)


@dataclass
class Report:
    skills: list[Skill] = field(default_factory=list)
    unreadable: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return sum(s.tokens for s in self.skills)

    def by_source(self) -> dict[str, list[Skill]]:
        grouped: dict[str, list[Skill]] = {}
        for skill in self.skills:
            grouped.setdefault(skill.source, []).append(skill)
        return grouped


def parse_frontmatter(text: str) -> tuple[dict, int]:
    """Extract YAML frontmatter without requiring PyYAML.

    Only scalar string and boolean values matter here, so a small parser
    avoids a dependency that would stop this script running anywhere.
    Returns (fields, body_line_count).
    """
    if not text.startswith("---"):
        return {}, len(text.splitlines())

    lines = text.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, len(lines)

    fields: dict[str, str] = {}
    key = None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        # Continuation of a block scalar (description: | or >).
        if raw.startswith((" ", "\t")) and key:
            fields[key] = (fields[key] + " " + raw.strip()).strip()
            continue
        match = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", raw)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        # Strip block-scalar markers and surrounding quotes.
        if value in ("|", ">", "|-", ">-", "|+", ">+"):
            value = ""
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[key] = value

    return fields, len(lines) - end - 1


def is_truthy(value: str) -> bool:
    return str(value).strip().lower() in {"true", "yes", "on", "1"}


def read_skill(skill_md: Path, source: str) -> Skill | None:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    fields, body_lines = parse_frontmatter(text)
    name = fields.get("name") or skill_md.parent.name

    return Skill(
        name=name,
        source=source,
        path=skill_md,
        description=fields.get("description", ""),
        when_to_use=fields.get("when_to_use", ""),
        body_lines=body_lines,
        # A skill the model cannot auto-invoke still costs nothing to *list*
        # only if it is also hidden from the menu; otherwise it is loaded.
        always_on=not is_truthy(fields.get("disable-model-invocation", "")),
    )


def collect(claude_dir: Path, project_dir: Path | None) -> Report:
    report = Report()
    seen: set[Path] = set()

    def add_from(root: Path, source: str) -> None:
        if not root.is_dir():
            return
        for skill_md in sorted(root.rglob("SKILL.md")):
            resolved = skill_md.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            skill = read_skill(skill_md, source)
            if skill is None:
                report.unreadable.append((skill_md, "could not read or decode"))
            else:
                report.skills.append(skill)

    # Personal skills.
    add_from(claude_dir / "skills", "personal")

    # Plugin skills, grouped by the plugin that ships them.
    marketplaces = claude_dir / "plugins" / "marketplaces"
    if marketplaces.is_dir():
        for market in sorted(p for p in marketplaces.iterdir() if p.is_dir()):
            plugins_dir = market / "plugins"
            targets = (
                sorted(p for p in plugins_dir.iterdir() if p.is_dir())
                if plugins_dir.is_dir() else [market]
            )
            for plugin in targets:
                add_from(plugin / "skills", f"{plugin.name}@{market.name}")

    # Project-local skills.
    if project_dir:
        add_from(project_dir / ".claude" / "skills", "project")

    return report


def find_duplicates(skills: list[Skill]) -> list[tuple[str, list[Skill]]]:
    """Skills whose descriptions overlap enough that Claude must guess.

    Two skills competing for the same trigger is worse than either alone:
    the model picks one semi-randomly and the other is pure context cost.
    """
    def signature(skill: Skill) -> set[str]:
        words = re.findall(r"[a-z]{4,}", skill.description.lower())
        return set(words)

    groups: list[tuple[str, list[Skill]]] = []
    unmatched = list(skills)

    while unmatched:
        current = unmatched.pop(0)
        base = signature(current)
        if len(base) < 5:
            continue
        cluster = [current]
        for other in list(unmatched):
            other_sig = signature(other)
            if not other_sig:
                continue
            overlap = len(base & other_sig) / min(len(base), len(other_sig))
            if overlap >= 0.6:
                cluster.append(other)
                unmatched.remove(other)
        if len(cluster) > 1:
            label = ", ".join(sorted(w for w in base)[:4])
            groups.append((label, cluster))

    return groups


def render(report: Report, top: int, budget: int | None) -> int:
    skills = sorted(report.skills, key=lambda s: -s.tokens)
    total = report.total_tokens

    if not skills:
        print("No skills found. Nothing is costing you context.")
        return 0

    print()
    print(f"  {len(skills)} skills across {len(report.by_source())} sources")
    print(f"  ~{total:,} tokens added to every request")
    print()

    # Per-source totals: this is where a single plugin's weight shows up.
    print("  BY SOURCE")
    print(f"  {'-' * 62}")
    grouped = sorted(report.by_source().items(), key=lambda kv: -sum(s.tokens for s in kv[1]))
    for source, group in grouped:
        cost = sum(s.tokens for s in group)
        share = cost / total * 100 if total else 0
        bar = "█" * max(1, round(share / 4))
        print(f"  {source[:28]:28} {cost:>6,} tok  {share:>5.1f}%  {bar}")
    print()

    print(f"  HEAVIEST SKILLS (top {min(top, len(skills))})")
    print(f"  {'-' * 62}")
    for skill in skills[:top]:
        flags = []
        if skill.truncated_by:
            flags.append(f"TRUNCATED -{skill.truncated_by}ch")
        if skill.tokens > VERBOSE_DESCRIPTION_TOKENS:
            flags.append("verbose")
        if not skill.description:
            flags.append("NO DESCRIPTION")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(f"  {skill.tokens:>5,} tok  {skill.name[:30]:30} {skill.source[:18]:18}{suffix}")
    print()

    problems = 0

    truncated = [s for s in skills if s.truncated_by]
    if truncated:
        problems += len(truncated)
        print("  ⚠ TRUNCATED DESCRIPTIONS")
        print(f"  {'-' * 62}")
        print(f"  Claude Code cuts name + description + when_to_use at")
        print(f"  {DESCRIPTION_LIMIT:,} characters. Everything past that never reaches the model,")
        print("  so these skills are being chosen on incomplete information:")
        for skill in truncated:
            print(f"    {skill.name} ({skill.source}) — losing {skill.truncated_by} chars")
        print()

    missing = [s for s in skills if not s.description]
    if missing:
        problems += len(missing)
        print("  ⚠ NO DESCRIPTION")
        print(f"  {'-' * 62}")
        print("  Claude picks skills by description. Without one it can only")
        print("  match on the name, so these rarely fire when they should:")
        for skill in missing:
            print(f"    {skill.name} ({skill.source})")
        print()

    duplicates = find_duplicates(skills)
    if duplicates:
        problems += len(duplicates)
        print("  ⚠ OVERLAPPING SKILLS")
        print(f"  {'-' * 62}")
        print("  These describe similar work. When two skills compete for the")
        print("  same request Claude picks one semi-arbitrarily, and the other")
        print("  is pure overhead:")
        for label, cluster in duplicates:
            names = ", ".join(f"{s.name} ({s.source})" for s in cluster)
            print(f"    [{label}] {names}")
        print()

    if report.unreadable:
        print("  ⚠ UNREADABLE")
        print(f"  {'-' * 62}")
        for path, reason in report.unreadable:
            print(f"    {path}: {reason}")
        print()

    if budget is not None:
        over = total - budget
        verdict = f"OVER by {over:,}" if over > 0 else f"under by {-over:,}"
        print(f"  Budget: {budget:,} tokens — {verdict}")
        print()
        if over > 0:
            return 1

    if problems == 0:
        print("  No issues found.")
        print()

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure the per-request context cost of installed skills."
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--top", type=int, default=12, help="how many heaviest skills to list")
    parser.add_argument("--budget", type=int, help="fail with exit 1 if total exceeds this")
    parser.add_argument("--claude-dir", type=Path, default=Path.home() / ".claude")
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    args = parser.parse_args()

    report = collect(args.claude_dir, args.project_dir)

    if args.json:
        payload = {
            "total_tokens": report.total_tokens,
            "skill_count": len(report.skills),
            "by_source": {
                source: sum(s.tokens for s in group)
                for source, group in report.by_source().items()
            },
            "skills": [
                {
                    "name": s.name,
                    "source": s.source,
                    "tokens": s.tokens,
                    "chars": s.chars,
                    "body_lines": s.body_lines,
                    "truncated_by": s.truncated_by,
                    "has_description": bool(s.description),
                    "path": str(s.path),
                }
                for s in sorted(report.skills, key=lambda s: -s.tokens)
            ],
        }
        print(json.dumps(payload, indent=2))
        if args.budget is not None and report.total_tokens > args.budget:
            return 1
        return 0

    return render(report, args.top, args.budget)


if __name__ == "__main__":
    sys.exit(main())
