#!/usr/bin/env python3
"""Validates every skill under skills/.

Checks:
  - SKILL.md exists and starts with YAML frontmatter.
  - The frontmatter has only `name` and `description`.
  - `name` matches the directory name and the Agent Skills naming rules.
  - `description` is not empty and has at most 1024 characters.
  - Every relative Markdown link points to a file that exists.

Exit code 0 means all skills are valid.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
ALLOWED_KEYS = {"name", "description"}


def parse_frontmatter(text: str):
    if not text.startswith("---\n"):
        return None, "SKILL.md does not start with '---'"
    end = text.find("\n---", 4)
    if end < 0:
        return None, "frontmatter has no closing '---'"
    fields = {}
    current = None
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if match and not line.startswith((" ", "\t")):
            current = match.group(1)
            fields[current] = match.group(2).strip()
        elif current:
            fields[current] = (fields[current] + " " + line.strip()).strip()
    for key, value in fields.items():
        if value[:1] in ("'", '"') and value[-1:] == value[:1]:
            fields[key] = value[1:-1]
    return fields, None


def check_links(md: Path, errors: list):
    text = md.read_text(encoding="utf-8")
    # Ignore links inside fenced code blocks.
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    for target in LINK_RE.findall(text):
        if re.match(r"^[a-z]+:", target) or target.startswith("#"):
            continue
        path = target.split("#", 1)[0]
        if path and not (md.parent / path).exists():
            errors.append(f"{md.relative_to(ROOT)}: broken link '{target}'")


def main() -> int:
    errors = []
    skills = sorted(p for p in SKILLS.iterdir() if p.is_dir())
    if not skills:
        errors.append("no skills found under skills/")

    for skill in skills:
        skill_md = skill / "SKILL.md"
        if not skill_md.is_file():
            errors.append(f"{skill.name}: SKILL.md is missing")
            continue

        fields, problem = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        if problem:
            errors.append(f"{skill.name}: {problem}")
            continue

        extra = set(fields) - ALLOWED_KEYS
        if extra:
            errors.append(f"{skill.name}: unexpected frontmatter keys {sorted(extra)}")

        name = fields.get("name", "")
        if name != skill.name:
            errors.append(f"{skill.name}: name '{name}' does not match the directory")
        if not NAME_RE.match(name) or len(name) > 64:
            errors.append(f"{skill.name}: name '{name}' breaks the naming rules")

        description = fields.get("description", "")
        if not description:
            errors.append(f"{skill.name}: description is empty")
        elif len(description) > 1024:
            errors.append(f"{skill.name}: description has {len(description)} chars (max 1024)")

        for md in skill.rglob("*.md"):
            check_links(md, errors)

    for error in errors:
        print(f"ERROR {error}")
    if not errors:
        print(f"OK {len(skills)} skills are valid")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
