# Poke — AI PR Review Agent

Domain-aware PR review agent for [Host Based Inventory (HBI)](https://github.com/RedHatInsights/insights-host-inventory). Powered by GPT-4o + o3-mini, free via GitHub Models.

## Quick Start

```bash
./poke              # browse open PRs, pick one to review
./poke --pr 4128    # review a specific PR directly
```

**Prerequisites:** `gh` CLI logged in (`gh auth login`). That's it — no API keys, no config files, no subscriptions.

## What It Does

Poke reviews PRs through **5 specialized lenses** — Migration, Auth, Kafka, API, Test — each with domain-specific rules for HBI. It auto-discovers Kafka topics, partitioned tables, API endpoints, and auth patterns from the live codebase.

**94% accuracy** across 8 real PRs. Zero false positives on refactoring PRs.

## Architecture

```
./poke
  |
  v
Index codebase (ChromaDB, 1032 chunks)
  |
  v
Auto-discover facts (Kafka topics, tables, endpoints, auth, flags)
  |
  v
Parse PR diff + fetch full files at HEAD
  |
  v
Route files to lenses (Migration, Auth, Kafka, API, Test, Security)
  |
  v
GPT-4o review (diff + full file + ChromaDB context + PR description + rules)
  |
  v
o3-mini self-critique (filter false positives)
  |
  v
Concept dedup + per-lens cap
  |
  v
Rich CLI output + optional PR comment posting
```

## Key Features

- **Auto-Discovery** — scans the codebase for Kafka topics, partitioned tables, API endpoints, auth decorators, feature flags
- **ChromaDB Index** — 1032 code chunks indexed by embeddings for cross-file awareness
- **Two-Model Strategy** — GPT-4o generates findings, o3-mini (reasoning model) filters false positives
- **Confidence Scoring** — each finding rated 1-10, below 6 auto-dropped
- **Concept Dedup** — merges similar findings across files (14 org_id findings → 1)
- **Deterministic Pre-checks** — regex for secrets, SQL injection, hardcoded schemas (free, no LLM cost)
- **Cherry-pick Posting** — review findings in terminal, pick which ones to post to the PR

## File Structure

```
├── __main__.py            CLI entry, auth check
├── main.py                Pipeline orchestrator
├── config.py              Configuration + route patterns
├── models.py              Data structures (FileChange, ReviewFinding)
├── diff_parser.py         Diff parsing + full file fetching
├── change_router.py       File → lens routing
├── codebase_index.py      ChromaDB codebase indexing
├── llm_engine.py          GPT-4o + o3-mini LLM calls
├── deduplicator.py        Concept + Jaccard deduplication
├── cli_formatter.py       Rich terminal output
├── comment_builder.py     GitHub PR comment formatting
├── github_client.py       GitHub API integration
├── pr_selector.py         PR listing + selection
├── poke                   Bash shortcut
├── requirements.txt       Dependencies
├── knowledge/
│   ├── prompts.py         LLM prompt templates
│   ├── auto_discover.py   Runtime codebase scanning
│   └── rules.yaml         Team-curated review rules
├── lenses/
│   ├── base.py            Abstract ReviewLens interface
│   ├── migration.py       Partition, downgrade, schema checks
│   ├── auth.py            RBAC, Kessel, org_id isolation
│   ├── kafka.py           Event schema, topic validation
│   ├── api.py             OpenAPI contract analysis
│   ├── test.py            Test coverage, fixture patterns
│   └── security.py        Secrets, SQL injection (pre-checks only)
└── demo/
    └── index.html         Presentation (https://adarshdubey-star.github.io)
```

## Tech Stack

- **LLMs:** GPT-4o (review) + o3-mini (critique) via GitHub Models (free)
- **Vector DB:** ChromaDB with all-MiniLM-L6-v2 embeddings
- **CLI:** Rich (tables, progress, panels) + gh CLI (GitHub API)
- **Language:** Python 3.12

## License

MIT
