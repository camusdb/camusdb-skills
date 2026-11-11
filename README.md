# CamusDB skills

Agent Skills that help coding agents use [CamusDB](https://camusdb.github.io)
well. The skills work in **Claude Code**, **Codex**, and **Cursor**.

A skill is a folder with a `SKILL.md` file. The agent reads the name and the
description of each skill. It loads the full instructions only when a task
matches. The skills summarize the
[CamusDB documentation](https://camusdb.github.io/docs/intro) and the
[best practices](https://camusdb.github.io/docs/best-practices).

## Skills

| Skill | Use it to |
| --- | --- |
| [`camusdb-schema-design`](skills/camusdb-schema-design/SKILL.md) | Design or review tables, primary keys, indexes, data types, foreign keys, sequences, and TTL. Avoid hotspots under key-range sharding. |
| [`camusdb-sql`](skills/camusdb-sql/SKILL.md) | Write correct CamusDB SQL, including correlated subqueries and JSONB. Port SQL from PostgreSQL or MySQL. Look up scalar functions. |
| [`camusdb-transactions`](skills/camusdb-transactions/SKILL.md) | Write transactional code: serializable isolation, client retries, transaction limits, priority, and historical reads. |
| [`camusdb-query-performance`](skills/camusdb-query-performance/SKILL.md) | Make a slow query fast with `EXPLAIN`, statistics, covering indexes, and the result cache. |
| [`camusdb-troubleshooting`](skills/camusdb-troubleshooting/SKILL.md) | Explain a `CADBxxxx` error and fix it. Diagnose a slow or stuck workload. |
| [`camusdb-dotnet`](skills/camusdb-dotnet/SKILL.md) | Use the .NET driver, Dapper, and EF Core with CamusDB. |
| [`camusdb-typescript`](skills/camusdb-typescript/SKILL.md) | Use the TypeScript connector. Call the HTTP API from any language. |
| [`camusdb-vector-search`](skills/camusdb-vector-search/SKILL.md) | Store embeddings and run nearest-neighbor queries. |
| [`camusdb-migrations-and-recovery`](skills/camusdb-migrations-and-recovery/SKILL.md) | Change a schema safely on a branch. Recover dropped or overwritten data. Back up, dump, and restore. |
| [`camusdb-csharp-extensions`](skills/camusdb-csharp-extensions/SKILL.md) | Write inline C# functions and triggers in SQL, or build and install C# extension DLLs. |
| [`camusdb-local-dev`](skills/camusdb-local-dev/SKILL.md) | Install and run CamusDB for development (NuGet, Homebrew, Docker, Aspire), use `camus-cli`, and connect an agent through the MCP server. |

## Install

### Claude Code

Install the skills as a plugin from this repository:

```text
/plugin marketplace add camusdb/camusdb-skills
/plugin install camusdb@camusdb
```

As an alternative, link the skills into `~/.claude/skills`:

```bash
git clone https://github.com/camusdb/camusdb-skills.git
cd camusdb-skills
./install.sh --agent claude
```

### Codex and Cursor

Codex and Cursor both read skills from `~/.agents/skills` and from
`.agents/skills` in a project:

```bash
./install.sh --agent agents
```

### All agents at once

```bash
./install.sh                 # links into ~/.claude/skills and ~/.agents/skills
```

### One project only

Copy the skills into a project, so the team gets them from the repository:

```bash
./install.sh --project ~/code/my-app --copy
```

That command writes `.claude/skills/` and `.agents/skills/` in the project.
Use `--copy` for a project. A symlink points to your local clone, so it does not
work on the machine of another developer.

### Update and uninstall

A symlink install follows the clone. Run `git pull` to update. Run the command
again after a copy install. Remove the skills with `--uninstall`:

```bash
./install.sh --uninstall
```

## Use

You do not need to call a skill by name. Ask the agent a CamusDB question, and
it loads the skill that matches. Some examples:

- "Review `migrations/002_orders.sql` against the CamusDB best practices."
- "This query is slow on CamusDB. Find out why."
- "Our app gets `CADB0502` under load. Fix the retry logic."
- "Port this PostgreSQL schema to CamusDB."
- "Try this migration on a branch of the `prod` database first."

You can also call a skill by name: `/camusdb-schema-design` in Claude Code and
Cursor, or `$camusdb-schema-design` in Codex.

## Contribute

See [AGENTS.md](AGENTS.md) for the rules of a skill. Run the validator before
you open a pull request:

```bash
python3 scripts/validate.py
```

## License

[MIT](LICENSE.txt)
