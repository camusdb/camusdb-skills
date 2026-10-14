# AGENTS.md

This repository holds Agent Skills for CamusDB. Claude Code, Codex, and Cursor
load these skills. Read this file before you add or change a skill.

## Layout

```text
skills/<skill-name>/SKILL.md        # the skill: frontmatter + instructions
skills/<skill-name>/references/     # long tables and details, loaded on demand
.claude-plugin/plugin.json          # Claude Code plugin manifest
.claude-plugin/marketplace.json     # Claude Code marketplace entry
install.sh                          # installs the skills for each agent
scripts/validate.py                 # checks frontmatter and relative links
```

## Rules for a skill

- The frontmatter has two keys only: `name` and `description`. Other keys are
  not portable across the three agents.
- `name` is the directory name. Use lowercase letters, digits, and hyphens.
  Start it with `camusdb-`.
- `description` says what the skill does and when to use it. Put the trigger
  words in it: statement names, error codes, package names. Keep it at 1024
  characters or fewer.
- Keep `SKILL.md` below about 350 lines. Move long tables to `references/`.
  Tell the agent when to read each reference file.
- Write plain, short sentences. Use the imperative for instructions.
- Use ```` ```sql ```` fences for CamusDB SQL.

## Source of truth

The skills summarize the public documentation. The documentation is the source
of truth:

- Docs: <https://camusdb.github.io/docs/intro>. The source is the
  `camusdb/camusdb.github.io` repository, in `docs/*.md`.
- Engine: the `camusdb/camusdb` repository.

Do not add a feature, a function, a limit, or an error code that the docs do
not describe. If the docs and the engine disagree, report the conflict. Do not
choose one side in silence.

When the docs change, update each skill that covers the changed page. Each
skill lists its doc pages in its "References" section.

## Validate

Run the validator before you commit:

```bash
python3 scripts/validate.py
```

CI runs the same check on each push and each pull request.
