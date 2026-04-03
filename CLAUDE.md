# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`codex-manager-auth` — a Python 3.12+ authentication service for a manager application. Uses **uv** as the package manager.

## Commands

```bash
# Install dependencies (syncs .venv)
uv sync

# Add a dependency
uv add <package>

# Add a dev dependency
uv add --dev <package>

# Run the application
uv run python main.py

# Run a single test
uv run pytest tests/test_foo.py::test_bar

# Run all tests
uv run pytest

# Lint / type-check (if configured)
uv run ruff check .
uv run mypy .
```

## Architecture

Single-file entry point (`main.py`) at the project root. The project is in its initial scaffolding phase — no modules, tests, or framework integration yet.

- **Package manager**: uv (`pyproject.toml` defines the project)
- **Python version**: 3.12 (pinned in `.python-version`)
