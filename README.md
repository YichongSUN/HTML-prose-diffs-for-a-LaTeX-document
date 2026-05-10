# scripts/

Utility scripts for the LaTeX thesis project.

---

## `thesis_content_diff.py` — LaTeX Content Diff

Generates a **self-contained HTML report** that shows chapter-by-chapter prose differences between two Git revisions of a LaTeX document. The script normalises raw LaTeX into readable text blocks before diffing, so the report focuses on *what you actually wrote* rather than markup noise.

**Designed by Yichong SUN 2026**

### What gets normalised away

| Suppressed | Kept |
|---|---|
| `% comments` | Prose sentences and paragraphs |
| `\label`, `\ref`, `\autoref` | Section headings (`\chapter`, `\section`, …) |
| Citation keys (`\cite`, `\citep`, `\citet`) | Inline math (simplified, e.g. `$x^2$` → `x^2`) |
| Figure paths (`\includegraphics`) | Figure / table captions |
| Display equations (`equation`, `align`, …) | List items (`\item`) |
| `\begin`/`\end` of most environments | `\href` link text |
| Line-wrap differences | `\SI` values with units |

### Requirements

| Tool | Version |
|---|---|
| Python | ≥ 3.10 |
| Git | any recent version |
| [uv](https://github.com/astral-sh/uv) | ≥ 0.4 (recommended runner) |

> The script uses only Python standard library — no `pip install` required.

---

### Quick start

```zsh
# Using the convenience wrapper (macOS: auto-opens report in browser)
./scripts/diff.sh HEAD~1 HEAD

# Directly with uv
uv run scripts/thesis_content_diff.py HEAD~1 HEAD
```

The report is saved to:
```
diff_reports/thesis-content-diff_<old-sha>_to_<new-sha>.html
```
and the path is printed to stdout.

---

### CLI reference

```
uv run scripts/thesis_content_diff.py [options] <old_rev> <new_rev>
```

| Argument | Default | Description |
|---|---|---|
| `old_rev` | *(required)* | Older Git commit, tag, branch, or revision expression |
| `new_rev` | *(required)* | Newer Git commit, tag, branch, or revision expression |
| `-o`, `--output` | `diff_reports/thesis-content-diff_<old>_to_<new>.html` | Custom output path (relative paths are resolved from the repo root) |
| `--thesis` | `thesis.tex` | Entry `.tex` file that contains `\include` directives, relative to the repo root |
| `--context` | `2` | Number of unchanged text blocks to show around each change (like `diff -U`) |
| `--only-changed` | off | Omit chapters where no prose changes were detected |
| `--chapter-prefix` | `chapters/` | Filter `\include` paths by this prefix. Pass `""` to collect all `\include`'d files |
| `--title` | `LaTeX Content Diff` | Report title shown in the browser tab and sidebar |

#### Examples

```zsh
# Compare the last commit
uv run scripts/thesis_content_diff.py HEAD~1 HEAD

# Compare a feature branch to main
uv run scripts/thesis_content_diff.py main feature/chp5-rewrite

# Show only chapters that actually changed, with 4 context blocks
uv run scripts/thesis_content_diff.py HEAD~5 HEAD --only-changed --context 4

# Custom title and output path
uv run scripts/thesis_content_diff.py v1.0 HEAD \
  --title "Revision 1 → 2 Diff" \
  -o review/revision-diff.html

# Use with a non-thesis LaTeX project (different entry file and chapter directory)
uv run scripts/thesis_content_diff.py HEAD~1 HEAD \
  --thesis main.tex \
  --chapter-prefix sections/ \
  --title "Paper Draft Diff"
```

---

### `diff.sh` — convenience wrapper

A thin Zsh wrapper around `thesis_content_diff.py`. On macOS it automatically opens the generated HTML in your default browser.

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
