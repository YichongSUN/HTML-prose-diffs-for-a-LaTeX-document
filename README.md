# Thesis Utility Scripts

> Precision tooling for LaTeX thesis revision workflows.
> Designed by [Yichong SUN](https://github.com/ycsun) · 2026

---

## Overview

This directory contains utility scripts that augment a LaTeX thesis project with Git-aware diffing capabilities. Rather than wading through raw markup deltas, these tools surface the prose that matters — stripping away LaTeX noise and presenting changes in a clean, navigable HTML report.

| Script | Role |
|---|---|
| [`thesis_content_diff.py`](#thesis_content_diffpy) | Core diff engine — produces a self-contained HTML report |
| [`diff.sh`](#diffsh) | Convenience wrapper — one command, browser auto-opens on macOS |

---

## `thesis_content_diff.py`

A pure-stdlib Python script that compares two Git revisions of a LaTeX document chapter by chapter. It normalises raw `.tex` source into readable prose blocks before diffing, so the report reflects *what you actually wrote* — not how you wrote it.

### Normalisation philosophy

The diff engine deliberately suppresses markup that carries no semantic weight while preserving content that does.

| Suppressed (noise) | Preserved (signal) |
|---|---|
| `% comments` | Prose sentences and paragraphs |
| `\label`, `\ref`, `\autoref` | Section headings (`\chapter`, `\section`, …) |
| Citation keys (`\cite`, `\citep`, `\citet`) | Inline math (e.g. `$x^2$` → `x^2`) |
| Figure paths (`\includegraphics`) | Figure and table captions |
| Display math environments (`equation`, `align`, …) | List items (`\item`) |
| `\begin` / `\end` of most environments | Hyperlink text (`\href`) |
| Line-wrap differences | Physical quantities (`\SI` values with units) |

### Requirements

| Tool | Version |
|---|---|
| Python | ≥ 3.10 |
| Git | any recent version |
| [uv](https://github.com/astral-sh/uv) | ≥ 0.4 *(recommended runner)* |

> **Zero dependencies.** The script relies exclusively on the Python standard library — no virtual environment or `pip install` required.

---

### Quick start

```zsh
# One-liner via the convenience wrapper (auto-opens in browser on macOS)
./scripts/diff.sh HEAD~1 HEAD

# Or invoke the script directly with uv
uv run scripts/thesis_content_diff.py HEAD~1 HEAD
```

The report is written to:

```
diff_reports/thesis-content-diff_<old-sha>_to_<new-sha>.html
```

The resolved output path is echoed to stdout.

---

### CLI reference

```
uv run scripts/thesis_content_diff.py [options] <old_rev> <new_rev>
```

| Argument / Flag | Default | Description |
|---|---|---|
| `old_rev` | *(required)* | Older Git revision — commit hash, tag, branch, or expression |
| `new_rev` | *(required)* | Newer Git revision — commit hash, tag, branch, or expression |
| `-o`, `--output` | `diff_reports/thesis-content-diff_<old>_to_<new>.html` | Output path; relative paths are resolved from the repository root |
| `--thesis` | `thesis.tex` | Entry `.tex` file containing `\include` directives, relative to repo root |
| `--context` | `2` | Unchanged blocks shown around each change (analogous to `diff -U`) |
| `--only-changed` | off | Suppress chapters with no detected prose changes |
| `--chapter-prefix` | `chapters/` | Include only `\include`'d paths matching this prefix; pass `""` to collect all |
| `--title` | `LaTeX Content Diff` | Report title displayed in the browser tab and sidebar |

#### Examples

```zsh
# Review the most recent commit
uv run scripts/thesis_content_diff.py HEAD~1 HEAD

# Diff a feature branch against main
uv run scripts/thesis_content_diff.py main feature/chp5-rewrite

# Focus on changed chapters only, with wider context
uv run scripts/thesis_content_diff.py HEAD~5 HEAD --only-changed --context 4

# Named report for a formal revision round
uv run scripts/thesis_content_diff.py v1.0 HEAD \
  --title "Revision 1 → 2 Diff" \
  -o review/revision-diff.html

# Adapt to a non-thesis LaTeX project
uv run scripts/thesis_content_diff.py HEAD~1 HEAD \
  --thesis main.tex \
  --chapter-prefix sections/ \
  --title "Paper Draft Diff"
```

---

## `diff.sh`

A minimal Zsh wrapper around `thesis_content_diff.py` that forwards all arguments verbatim and, on macOS, automatically opens the resulting report in your default browser.

```zsh
# Accepts the same arguments as thesis_content_diff.py
./scripts/diff.sh HEAD~1 HEAD --only-changed --context 4
```

```zsh
./scripts/diff.sh <old_rev> <new_rev> [options]
```

All options are forwarded verbatim to `thesis_content_diff.py`.

```zsh
./scripts/diff.sh HEAD~1 HEAD
./scripts/diff.sh HEAD~1 HEAD --only-changed
./scripts/diff.sh abc1234 HEAD --context 4 -o my-report.html
```

---

### Report features

| Feature | Description |
|---|---|
| Sidebar navigation | Sticky chapter list; chapters with changes are highlighted with a blue dot |
| Scroll spy | Active chapter highlighted automatically as you scroll |
| Collapse / Expand | Each chapter header is clickable; **Expand All** / **Collapse All** buttons in toolbar |
| Print / Save PDF | Browser print dialog via button; sidebar and controls are hidden in print layout |
| Word delta | Summary table and hero metrics show per-chapter and total word count changes |
| Inline diff | Modified blocks show word-level `del`/`ins` highlights within each cell |
| Colour coding | Red border = deleted, green border = inserted, amber border = modified |

---

### How it works

1. **Discover chapters** — reads `\include{…}` directives from the entry file at each revision via `git show`. Falls back to `git ls-tree` if the entry file is missing.
2. **Normalise LaTeX** — strips comments, replaces headings/math/refs/floats with readable text, collapses whitespace, splits into paragraph-level blocks.
3. **Diff blocks** — uses Python `difflib.SequenceMatcher` at the block level, then again at the token level for modified blocks (inline word diff).
4. **Render HTML** — produces a fully self-contained single-file HTML report with embedded CSS and JS (no external dependencies at view time, except the Google Fonts CDN for Inter/JetBrains Mono).

---

### Reusing in another project

The script is intentionally generic. The only thesis-specific defaults are:

| Default | Override with |
|---|---|
| Entry file `thesis.tex` | `--thesis your-main.tex` |
| Chapter prefix `chapters/` | `--chapter-prefix your-dir/` |
| Output dir `diff_reports/` | `-o path/to/output.html` |
| Title `LaTeX Content Diff` | `--title "Your Title"` |

Copy `thesis_content_diff.py` to any Git-tracked LaTeX project and run it with the appropriate flags.
