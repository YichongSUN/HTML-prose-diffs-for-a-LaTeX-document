#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Generate chapter-wise HTML prose diffs for a LaTeX document.

The script compares the chapters/sections included by a LaTeX entry file at two
Git revisions. It normalizes LaTeX into readable content blocks before diffing,
so line wrapping, labels, references, figure paths, and most formatting commands
do not dominate the report.

Designed by Yichong SUN 2026
"""

from __future__ import annotations

import argparse
import dataclasses
import difflib
import html
import re
import subprocess
import sys
from datetime import datetime
from itertools import zip_longest
from pathlib import Path


DEFAULT_CONTEXT = 2
DEFAULT_CHAPTER_PREFIX = "chapters/"
DEFAULT_TITLE = "LaTeX Content Diff"


@dataclasses.dataclass
class ChapterDiff:
    path: str
    old_title: str
    new_title: str
    old_blocks: list[str]
    new_blocks: list[str]
    rows: list[dict[str, str]]
    equal_blocks: int
    added_blocks: int
    deleted_blocks: int
    modified_blocks: int

    @property
    def title(self) -> str:
        return self.new_title or self.old_title or self.path

    @property
    def changed_blocks(self) -> int:
        return self.added_blocks + self.deleted_blocks + self.modified_blocks

    @property
    def old_word_count(self) -> int:
        return word_count(self.old_blocks)

    @property
    def new_word_count(self) -> int:
        return word_count(self.new_blocks)


def run_git(root: Path, args: list[str], *, allow_fail: bool = False) -> str | None:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode == 0:
        return proc.stdout
    if allow_fail:
        return None
    command = "git " + " ".join(args)
    raise SystemExit(f"{command} failed:\n{proc.stderr.strip()}")


def find_repo_root(start: Path) -> Path:
    output = run_git(start, ["rev-parse", "--show-toplevel"])
    if not output:
        raise SystemExit("Not inside a Git repository.")
    return Path(output.strip())


def short_rev(root: Path, rev: str) -> str:
    output = run_git(root, ["rev-parse", "--short", rev])
    return (output or rev).strip()


def commit_label(root: Path, rev: str) -> str:
    output = run_git(root, ["log", "-1", "--format=%h %cs %s", rev])
    return (output or rev).strip()


def git_show(root: Path, rev: str, path: str) -> str | None:
    return run_git(root, ["show", f"{rev}:{path}"], allow_fail=True)


def strip_tex_comments(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        cut_at: int | None = None
        for index, char in enumerate(line):
            if char != "%":
                continue
            slash_count = 0
            cursor = index - 1
            while cursor >= 0 and line[cursor] == "\\":
                slash_count += 1
                cursor -= 1
            if slash_count % 2 == 0:
                cut_at = index
                break
        lines.append(line[:cut_at] if cut_at is not None else line)
    return "\n".join(lines)


def read_balanced(text: str, start: int, open_char: str, close_char: str) -> tuple[str, int] | None:
    if start >= len(text) or text[start] != open_char:
        return None
    depth = 0
    cursor = start
    content_start = start + 1
    while cursor < len(text):
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return text[content_start:cursor], cursor + 1
        cursor += 1
    return None


def skip_spaces_and_options(text: str, cursor: int) -> int:
    while True:
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor < len(text) and text[cursor] == "[":
            parsed = read_balanced(text, cursor, "[", "]")
            if parsed is None:
                return cursor
            _, cursor = parsed
            continue
        return cursor


def replace_commands(
    text: str,
    specs: dict[str, tuple[int, object]],
) -> str:
    """Replace selected LaTeX commands with callback output.

    specs maps command name to (required_braced_arg_count, callback). The
    callback receives the command name and a list of raw braced arguments.
    """

    out: list[str] = []
    cursor = 0
    command_pattern = re.compile(r"\\([A-Za-z]+)\*?")
    while cursor < len(text):
        match = command_pattern.match(text, cursor)
        if not match:
            out.append(text[cursor])
            cursor += 1
            continue

        name = match.group(1)
        spec = specs.get(name)
        if spec is None:
            out.append(match.group(0))
            cursor = match.end()
            continue

        arity, callback = spec
        probe = skip_spaces_and_options(text, match.end())
        args: list[str] = []
        ok = True
        for _ in range(arity):
            probe = skip_spaces_and_options(text, probe)
            parsed = read_balanced(text, probe, "{", "}")
            if parsed is None:
                ok = False
                break
            arg, probe = parsed
            args.append(arg)
        if not ok:
            out.append(match.group(0))
            cursor = match.end()
            continue

        out.append(str(callback(name, args)))
        cursor = probe
    return "".join(out)


def extract_command_args(text: str, command: str) -> list[str]:
    args: list[str] = []
    pattern = re.compile(rf"\\{re.escape(command)}\*?")
    cursor = 0
    while True:
        match = pattern.search(text, cursor)
        if not match:
            return args
        probe = skip_spaces_and_options(text, match.end())
        parsed = read_balanced(text, probe, "{", "}")
        if parsed is None:
            cursor = match.end()
            continue
        arg, cursor = parsed
        args.append(arg)


def replace_float_environments(text: str) -> str:
    pattern = re.compile(r"\\begin\{(figure\*?|table\*?)\}(.+?)\\end\{\1\}", re.S)

    def repl(match: re.Match[str]) -> str:
        env_name = match.group(1)
        label = "Figure caption" if env_name.startswith("figure") else "Table caption"
        captions = extract_command_args(match.group(2), "caption")
        if not captions:
            return "\n"
        return "\n".join(f"[{label}] {caption}" for caption in captions) + "\n"

    return pattern.sub(repl, text)


def remove_environments(text: str, names: list[str]) -> str:
    for name in names:
        escaped = re.escape(name)
        text = re.sub(rf"\\begin\{{{escaped}\*?\}}.*?\\end\{{{escaped}\*?\}}", "\n", text, flags=re.S)
    return text


def normalize_inline_math(content: str) -> str:
    content = replace_commands(
        content,
        {
            "mathbf": (1, lambda _name, args: args[0]),
            "mathbb": (1, lambda _name, args: args[0]),
            "mathcal": (1, lambda _name, args: args[0]),
            "mathrm": (1, lambda _name, args: args[0]),
            "boldsymbol": (1, lambda _name, args: args[0]),
            "operatorname": (1, lambda _name, args: args[0]),
            "hat": (1, lambda _name, args: args[0]),
            "tilde": (1, lambda _name, args: args[0]),
            "bar": (1, lambda _name, args: args[0]),
        },
    )
    replacements = {
        r"\times": "x",
        r"\cdot": ".",
        r"\le": "<=",
        r"\ge": ">=",
        r"\in": "in",
        r"\subset": "subset",
        r"\Delta": "Delta",
        r"\Phi": "Phi",
        r"\Omega": "Omega",
        r"\mu": "mu",
        r"\lambda": "lambda",
        r"\sigma": "sigma",
        r"\tau": "tau",
        r"\nabla": "nabla",
        r"\top": "T",
    }
    for old, new in replacements.items():
        content = content.replace(old, new)
    content = re.sub(r"\\[A-Za-z]+", "", content)
    content = content.replace("{", "").replace("}", "")
    content = re.sub(r"\s+", " ", content)
    return content.strip()


def replace_inline_math(text: str) -> str:
    def dollar_repl(match: re.Match[str]) -> str:
        normalized = normalize_inline_math(match.group(1))
        return normalized if normalized else "[math]"

    text = re.sub(r"\$\$(.*?)\$\$", " ", text, flags=re.S)
    text = re.sub(r"\\\[(.*?)\\\]", " ", text, flags=re.S)
    text = re.sub(r"\\\((.*?)\\\)", lambda m: normalize_inline_math(m.group(1)), text, flags=re.S)
    text = re.sub(r"(?<!\\)\$(.*?)(?<!\\)\$", dollar_repl, text, flags=re.S)
    return text


def normalize_latex_to_blocks(tex: str) -> list[str]:
    text = strip_tex_comments(tex)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = replace_float_environments(text)
    text = remove_environments(
        text,
        [
            "equation",
            "align",
            "aligned",
            "subequations",
            "gather",
            "multline",
            "flalign",
            "eqnarray",
            "algorithm",
            "lstlisting",
            "verbatim",
            "tikzpicture",
        ],
    )

    heading_prefix = {
        "chapter": "#",
        "section": "##",
        "subsection": "###",
        "subsubsection": "####",
        "paragraph": "#####",
    }
    text = replace_commands(
        text,
        {
            name: (1, lambda command, args, prefixes=heading_prefix: f"\n{prefixes[command]} {args[0]}\n")
            for name in heading_prefix
        },
    )
    text = re.sub(r"\\item(?:\[[^\]]+\])?", "\n- ", text)
    text = replace_inline_math(text)

    text = replace_commands(
        text,
        {
            "cite": (1, lambda _name, _args: " [citation]"),
            "citep": (1, lambda _name, _args: " [citation]"),
            "citet": (1, lambda _name, _args: " [citation]"),
            "ref": (1, lambda _name, _args: "[reference]"),
            "autoref": (1, lambda _name, _args: "[reference]"),
            "eqref": (1, lambda _name, _args: "[reference]"),
            "label": (1, lambda _name, _args: ""),
            "includegraphics": (1, lambda _name, _args: ""),
            "url": (1, lambda _name, args: args[0]),
            "href": (2, lambda _name, args: args[1]),
            "SI": (2, lambda _name, args: f"{args[0]} {args[1]}"),
            "textcolor": (2, lambda _name, args: args[1]),
        },
    )

    unwrap_commands = [
        "textbf",
        "textit",
        "emph",
        "underline",
        "textrm",
        "textsf",
        "texttt",
        "mbox",
        "footnote",
        "footnotetext",
    ]
    for _ in range(4):
        new_text = replace_commands(
            text,
            {name: (1, lambda _name, args: args[0]) for name in unwrap_commands},
        )
        if new_text == text:
            break
        text = new_text

    text = text.replace("~", " ")
    text = text.replace(r"\%", "%").replace(r"\&", "&").replace(r"\_", "_")
    text = text.replace("--", "-")
    text = re.sub(r"\\begin\{[^}]+\}", "\n", text)
    text = re.sub(r"\\end\{[^}]+\}", "\n", text)
    text = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?", "", text)
    text = text.replace("{", "").replace("}", "")
    text = text.replace("\\", "")

    blocks: list[str] = []
    paragraph_parts: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph_parts:
            return
        block = " ".join(paragraph_parts)
        block = clean_block(block)
        paragraph_parts.clear()
        if keep_block(block):
            blocks.append(block)

    for raw_line in text.splitlines():
        line = clean_block(raw_line)
        if not line:
            flush_paragraph()
            continue
        if re.match(r"^(#{1,5}|\- |\[(Figure|Table) caption\])", line):
            flush_paragraph()
            if keep_block(line):
                blocks.append(line)
            continue
        paragraph_parts.append(line)
    flush_paragraph()
    return blocks


def clean_block(block: str) -> str:
    block = html.unescape(block)
    block = block.replace("``", '"').replace("''", '"')
    block = re.sub(r"\s+", " ", block)
    block = re.sub(r"\s+([,.;:!?])", r"\1", block)
    block = re.sub(r"\(\s+", "(", block)
    block = re.sub(r"\s+\)", ")", block)
    return block.strip()


def keep_block(block: str) -> bool:
    if not block:
        return False
    if block in {"-", "[citation]", "[reference]"}:
        return False
    return bool(re.search(r"[A-Za-z0-9]", block))


def chapter_title(blocks: list[str]) -> str:
    for block in blocks:
        if block.startswith("# "):
            return block[2:].strip()
    return ""


def word_count(blocks: list[str]) -> int:
    return sum(len(re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?", block)) for block in blocks)


def tokenize_for_inline(text: str) -> list[str]:
    return re.findall(r"\s+|[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?|[^\sA-Za-z0-9]", text)


def inline_diff(old: str, new: str) -> tuple[str, str]:
    old_tokens = tokenize_for_inline(old)
    new_tokens = tokenize_for_inline(new)
    matcher = difflib.SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
    old_out: list[str] = []
    new_out: list[str] = []
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        old_text = "".join(old_tokens[old_start:old_end])
        new_text = "".join(new_tokens[new_start:new_end])
        if tag == "equal":
            old_out.append(escape(old_text))
            new_out.append(escape(new_text))
        elif tag == "delete":
            old_out.append(f"<del>{escape(old_text)}</del>")
        elif tag == "insert":
            new_out.append(f"<ins>{escape(new_text)}</ins>")
        else:
            old_out.append(f"<del>{escape(old_text)}</del>")
            new_out.append(f"<ins>{escape(new_text)}</ins>")
    return "".join(old_out), "".join(new_out)


def escape(text: str) -> str:
    return html.escape(text, quote=False)


def build_rows(old_blocks: list[str], new_blocks: list[str], context: int) -> tuple[list[dict[str, str]], int, int, int, int]:
    matcher = difflib.SequenceMatcher(None, old_blocks, new_blocks, autojunk=False)
    grouped = matcher.get_grouped_opcodes(context)
    rows: list[dict[str, str]] = []
    equal_blocks = added_blocks = deleted_blocks = modified_blocks = 0

    for group_index, group in enumerate(grouped):
        if group_index:
            rows.append({"kind": "skip", "old": "", "new": ""})
        for tag, old_start, old_end, new_start, new_end in group:
            old_slice = old_blocks[old_start:old_end]
            new_slice = new_blocks[new_start:new_end]
            if tag == "equal":
                equal_blocks += len(old_slice)
                for old_text, new_text in zip(old_slice, new_slice):
                    rows.append({"kind": "equal", "old": escape(old_text), "new": escape(new_text)})
            elif tag == "delete":
                deleted_blocks += len(old_slice)
                for old_text in old_slice:
                    rows.append({"kind": "delete", "old": escape(old_text), "new": ""})
            elif tag == "insert":
                added_blocks += len(new_slice)
                for new_text in new_slice:
                    rows.append({"kind": "insert", "old": "", "new": escape(new_text)})
            else:
                modified_blocks += max(len(old_slice), len(new_slice))
                for old_text, new_text in zip_longest(old_slice, new_slice, fillvalue=""):
                    if old_text and new_text:
                        old_html, new_html = inline_diff(old_text, new_text)
                        rows.append({"kind": "replace", "old": old_html, "new": new_html})
                    elif old_text:
                        rows.append({"kind": "delete", "old": escape(old_text), "new": ""})
                    elif new_text:
                        rows.append({"kind": "insert", "old": "", "new": escape(new_text)})

    if not rows and old_blocks == new_blocks:
        equal_blocks = len(old_blocks)
    return rows, equal_blocks, added_blocks, deleted_blocks, modified_blocks


def parse_included_chapters(thesis_tex: str, chapter_prefix: str = DEFAULT_CHAPTER_PREFIX) -> list[str]:
    thesis_tex = strip_tex_comments(thesis_tex)
    chapters: list[str] = []
    for match in re.finditer(r"\\include\{([^}]+)\}", thesis_tex):
        path = match.group(1).strip()
        if chapter_prefix and not path.startswith(chapter_prefix):
            continue
        if not path.endswith(".tex"):
            path += ".tex"
        if path not in chapters:
            chapters.append(path)
    return chapters


def list_chapters_at_rev(root: Path, rev: str, thesis_path: str, chapter_prefix: str = DEFAULT_CHAPTER_PREFIX) -> list[str]:
    thesis = git_show(root, rev, thesis_path)
    if thesis:
        chapters = parse_included_chapters(thesis, chapter_prefix)
        if chapters:
            return chapters
    chapter_dir = chapter_prefix.rstrip("/") if chapter_prefix else ""
    if not chapter_dir:
        return []
    output = run_git(root, ["ls-tree", "-r", "--name-only", rev, chapter_dir], allow_fail=True)
    if not output:
        return []
    return [line for line in output.splitlines() if line.endswith(".tex")]


def ordered_union(primary: list[str], secondary: list[str]) -> list[str]:
    merged: list[str] = []
    for path in [*primary, *secondary]:
        if path not in merged:
            merged.append(path)
    return merged


def compare_chapter(root: Path, old_rev: str, new_rev: str, path: str, context: int) -> ChapterDiff:
    old_tex = git_show(root, old_rev, path) or ""
    new_tex = git_show(root, new_rev, path) or ""
    old_blocks = normalize_latex_to_blocks(old_tex)
    new_blocks = normalize_latex_to_blocks(new_tex)
    rows, equal_blocks, added_blocks, deleted_blocks, modified_blocks = build_rows(old_blocks, new_blocks, context)
    return ChapterDiff(
        path=path,
        old_title=chapter_title(old_blocks),
        new_title=chapter_title(new_blocks),
        old_blocks=old_blocks,
        new_blocks=new_blocks,
        rows=rows,
        equal_blocks=equal_blocks,
        added_blocks=added_blocks,
        deleted_blocks=deleted_blocks,
        modified_blocks=modified_blocks,
    )


def render_html(
    root: Path,
    old_rev: str,
    new_rev: str,
    old_label: str,
    new_label: str,
    chapter_diffs: list[ChapterDiff],
    context: int,
    title: str = DEFAULT_TITLE,
) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_changed = sum(diff.changed_blocks for diff in chapter_diffs)
    total_old_words = sum(diff.old_word_count for diff in chapter_diffs)
    total_new_words = sum(diff.new_word_count for diff in chapter_diffs)
    word_delta = total_new_words - total_old_words
    word_delta_str = (f"+{word_delta}" if word_delta > 0 else str(word_delta))

    nav_items = "\n".join(
        f'''<a href="#chapter-{index}" class="nav-item {'nav-changed' if diff.changed_blocks else ''}">
          <span class="nav-num">{escape(str(index))}</span>
          <span class="nav-label">{escape(diff.title)}</span>
          {'<span class="nav-dot"></span>' if diff.changed_blocks else ''}
        </a>'''
        for index, diff in enumerate(chapter_diffs, start=1)
    )
    summary_rows = "\n".join(render_summary_row(index, diff) for index, diff in enumerate(chapter_diffs, start=1))
    chapter_sections = "\n".join(render_chapter(index, diff) for index, diff in enumerate(chapter_diffs, start=1))

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}: {escape(short_rev(root, old_rev))} → {escape(short_rev(root, new_rev))}</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

:root {{
  --bg:           #f4f6f9;
  --panel:        #ffffff;
  --text:         #1a1d23;
  --text-secondary: #4a5568;
  --muted:        #718096;
  --border:       #e2e8f0;
  --border-light: #edf2f7;
  --old-bg:       #fff5f5;
  --old-border:   #fc8181;
  --new-bg:       #f0fff4;
  --new-border:   #68d391;
  --replace-bg:   #fffaf0;
  --replace-border:#f6ad55;
  --mark-old:     #fed7d7;
  --mark-old-text:#9b2c2c;
  --mark-new:     #c6f6d5;
  --mark-new-text:#22543d;
  --nav-bg:       #1a202c;
  --nav-border:   #2d3748;
  --nav-text:     #e2e8f0;
  --nav-muted:    #718096;
  --nav-hover:    #2d3748;
  --accent:       #4f7df3;
  --accent-light: #ebf4ff;
  --radius:       10px;
  --shadow:       0 1px 3px rgba(0,0,0,.08), 0 1px 2px rgba(0,0,0,.04);
  --shadow-md:    0 4px 12px rgba(0,0,0,.1), 0 2px 4px rgba(0,0,0,.06);
}}

*, *::before, *::after {{ box-sizing: border-box; }}

body {{
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  font-size: 14px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}}

/* ── Layout ────────────────────────────────── */
.layout {{
  display: grid;
  grid-template-columns: 268px minmax(0, 1fr);
  min-height: 100vh;
}}

/* ── Sidebar nav ───────────────────────────── */
nav {{
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
  background: var(--nav-bg);
  color: var(--nav-text);
  display: flex;
  flex-direction: column;
  scrollbar-width: thin;
  scrollbar-color: var(--nav-border) transparent;
}}
nav::-webkit-scrollbar {{ width: 4px; }}
nav::-webkit-scrollbar-thumb {{ background: var(--nav-border); border-radius: 4px; }}

.nav-header {{
  padding: 24px 20px 16px;
  border-bottom: 1px solid var(--nav-border);
}}
.nav-logo {{
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}}
.nav-logo svg {{ flex-shrink: 0; opacity: 0.9; }}
.nav-title {{
  font-size: 14px;
  font-weight: 600;
  line-height: 1.3;
  color: #fff;
}}
.nav-revs {{
  font-size: 11px;
  color: var(--nav-muted);
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  line-height: 1.8;
}}
.nav-revs .rev-arrow {{ color: var(--accent); margin: 0 2px; }}

.nav-actions {{
  padding: 12px 20px;
  border-bottom: 1px solid var(--nav-border);
  display: flex;
  flex-direction: column;
  gap: 6px;
}}
.nav-btn {{
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  background: transparent;
  border: 1px solid var(--nav-border);
  border-radius: 6px;
  color: var(--nav-text);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: background .15s, border-color .15s;
  text-align: left;
  font-family: inherit;
}}
.nav-btn:hover {{ background: var(--nav-hover); border-color: #4a5568; }}
.nav-btn svg {{ flex-shrink: 0; opacity: 0.7; }}

.nav-chapters {{
  padding: 12px 12px;
  flex: 1;
}}
.nav-section-label {{
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--nav-muted);
  padding: 4px 8px 8px;
}}
.nav-item {{
  display: grid;
  grid-template-columns: 22px 1fr auto;
  align-items: center;
  gap: 6px;
  padding: 7px 8px;
  color: #a0aec0;
  text-decoration: none;
  border-radius: 6px;
  font-size: 12.5px;
  transition: background .12s, color .12s;
  margin-bottom: 1px;
}}
.nav-item:hover {{ background: var(--nav-hover); color: #e2e8f0; }}
.nav-item.nav-changed {{ color: #e2e8f0; font-weight: 500; }}
.nav-num {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: var(--nav-muted);
  text-align: right;
}}
.nav-label {{ white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.nav-dot {{
  width: 6px; height: 6px;
  border-radius: 50%;
  background: var(--accent);
  flex-shrink: 0;
}}

/* ── Main content ──────────────────────────── */
.content {{
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}}

main {{
  padding: 28px 32px;
  flex: 1;
}}

/* ── Toolbar ───────────────────────────────── */
.toolbar {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
  gap: 12px;
}}
.toolbar-title {{
  font-size: 22px;
  font-weight: 700;
  color: var(--text);
  margin: 0;
}}
.toolbar-actions {{
  display: flex;
  gap: 8px;
}}
.btn {{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border-radius: 7px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all .15s;
  border: none;
  font-family: inherit;
  text-decoration: none;
}}
.btn-ghost {{
  background: var(--panel);
  color: var(--text-secondary);
  border: 1px solid var(--border);
  box-shadow: var(--shadow);
}}
.btn-ghost:hover {{ background: var(--border-light); color: var(--text); }}
.btn-primary {{
  background: var(--accent);
  color: #fff;
  box-shadow: 0 1px 3px rgba(79,125,243,.35);
}}
.btn-primary:hover {{ background: #3d6ee8; box-shadow: 0 2px 6px rgba(79,125,243,.45); }}

/* ── Hero stats ────────────────────────────── */
.hero {{
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px 24px;
  margin-bottom: 16px;
  box-shadow: var(--shadow);
}}
.hero-meta {{
  font-size: 12px;
  color: var(--muted);
  font-family: 'JetBrains Mono', monospace;
  margin-bottom: 16px;
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
}}
.meta-grid {{
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
}}
.metric {{
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px 16px;
  position: relative;
  overflow: hidden;
}}
.metric::before {{
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 3px;
  background: var(--accent);
  border-radius: 8px 8px 0 0;
}}
.metric b {{
  display: block;
  font-size: 24px;
  font-weight: 700;
  line-height: 1.2;
  color: var(--text);
}}
.metric span {{
  font-size: 11px;
  color: var(--muted);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: .04em;
}}
.metric.positive b {{ color: #276749; }}
.metric.positive::before {{ background: #48bb78; }}
.metric.negative b {{ color: #9b2c2c; }}
.metric.negative::before {{ background: #fc8181; }}

/* ── Summary table ─────────────────────────── */
.summary-card {{
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  margin-bottom: 16px;
  overflow: hidden;
  box-shadow: var(--shadow);
}}
.card-header {{
  padding: 14px 20px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: .05em;
}}
table {{
  width: 100%;
  border-collapse: collapse;
}}
th, td {{
  padding: 10px 16px;
  border-bottom: 1px solid var(--border-light);
  vertical-align: middle;
}}
th {{
  text-align: left;
  color: var(--muted);
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .05em;
  background: #fafbfc;
}}
tr:last-child td {{ border-bottom: none; }}
tr:hover td {{ background: var(--bg); }}
td.num {{
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12.5px;
}}
.path-mono {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 11.5px;
  color: var(--muted);
}}
.pill {{
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
}}
.pill-changed {{ background: #ebf4ff; color: #2b6cb0; }}
.pill-clean {{ background: #f0fff4; color: #276749; }}

/* ── Chapter cards ─────────────────────────── */
.chapter {{
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  margin-bottom: 16px;
  overflow: hidden;
  box-shadow: var(--shadow);
  scroll-margin-top: 20px;
}}
.chapter-header {{
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  cursor: pointer;
  user-select: none;
  transition: background .12s;
}}
.chapter-header:hover {{ background: #fafbfc; }}
.chapter-header-left {{ flex: 1; min-width: 0; }}
.chapter-title-row {{
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}}
.chapter-index {{
  width: 28px; height: 28px;
  background: var(--accent);
  color: #fff;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}}
.chapter-index.no-change {{
  background: #e2e8f0;
  color: var(--muted);
}}
.chapter-title {{
  font-size: 16px;
  font-weight: 600;
  color: var(--text);
  margin: 0;
}}
.chapter-path {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: var(--muted);
  margin-left: 38px;
}}
.chapter-badges {{
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
  margin-left: 38px;
}}
.badge {{
  padding: 3px 8px;
  border-radius: 5px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid transparent;
}}
.badge-changed  {{ background: #ebf4ff; color: #2b6cb0; border-color: #bee3f8; }}
.badge-modified {{ background: #fffaf0; color: #c05621; border-color: #fbd38d; }}
.badge-added    {{ background: #f0fff4; color: #276749; border-color: #9ae6b4; }}
.badge-deleted  {{ background: #fff5f5; color: #9b2c2c; border-color: #feb2b2; }}
.badge-words    {{ background: var(--bg);  color: var(--muted); border-color: var(--border); }}
.toggle-icon {{
  color: var(--muted);
  transition: transform .2s;
  flex-shrink: 0;
  margin-top: 4px;
}}
.chapter.collapsed .toggle-icon {{ transform: rotate(-90deg); }}
.chapter-body {{ }}
.chapter.collapsed .chapter-body {{ display: none; }}

/* ── Diff table ────────────────────────────── */
.diff-table {{
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}}
.diff-table th {{
  padding: 10px 16px;
  font-size: 11px;
  font-weight: 600;
  color: var(--muted);
  letter-spacing: .05em;
  background: #fafbfc;
  border-bottom: 1px solid var(--border);
  text-transform: uppercase;
}}
.diff-table th:first-child {{ border-right: 1px solid var(--border); }}
.diff-table td {{
  width: 50%;
  padding: 10px 16px;
  word-wrap: break-word;
  font-size: 13.5px;
  line-height: 1.65;
  vertical-align: top;
  border-bottom: 1px solid var(--border-light);
}}
.diff-table td:first-child {{ border-right: 1px solid var(--border); }}
.diff-table tr:last-child td {{ border-bottom: none; }}

.equal td {{ color: var(--text-secondary); background: #fafbfc; }}

.delete td.old {{
  background: var(--old-bg);
  border-left: 3px solid var(--old-border);
  padding-left: 13px;
}}
.delete td.new {{ background: #fff; }}

.insert td.old {{ background: #fff; }}
.insert td.new {{
  background: var(--new-bg);
  border-left: 3px solid var(--new-border);
  padding-left: 13px;
}}

.replace td {{
  background: var(--replace-bg);
  border-left: 3px solid var(--replace-border);
  padding-left: 13px;
}}

del {{
  background: var(--mark-old);
  color: var(--mark-old-text);
  text-decoration: none;
  border-radius: 3px;
  padding: 1px 3px;
  font-weight: 500;
}}
ins {{
  background: var(--mark-new);
  color: var(--mark-new-text);
  text-decoration: none;
  border-radius: 3px;
  padding: 1px 3px;
  font-weight: 500;
}}

.skip td {{
  text-align: center;
  color: var(--muted);
  background: #f7f9fc;
  font-size: 11px;
  padding: 7px;
  letter-spacing: .03em;
  border-bottom: 1px solid var(--border-light);
}}
.skip td::before {{ content: '···  '; opacity: .4; }}
.skip td::after  {{ content: '  ···'; opacity: .4; }}

.no-changes {{
  padding: 28px 20px;
  text-align: center;
  color: var(--muted);
  font-size: 13px;
}}
.no-changes svg {{ display: block; margin: 0 auto 8px; opacity: .4; }}

/* ── Footer ─────────────────────────────────── */
footer {{
  padding: 16px 32px;
  border-top: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11.5px;
  color: var(--muted);
  background: var(--panel);
}}
.footer-brand {{ font-weight: 600; color: var(--text-secondary); }}

/* ── Print ───────────────────────────────────── */
@media print {{
  nav, .toolbar-actions, .nav-actions, .chapter-header {{ cursor: default !important; }}
  nav, .toolbar-actions {{ display: none !important; }}
  .layout {{ grid-template-columns: 1fr; }}
  .content {{ min-height: unset; }}
  body {{ background: white; font-size: 12px; }}
  .chapter {{ break-inside: avoid; box-shadow: none; border: 1px solid #ccc; }}
  .chapter.collapsed .chapter-body {{ display: block !important; }}
  .hero, .summary-card {{ box-shadow: none; border: 1px solid #ccc; }}
  main {{ padding: 0; }}
  footer {{ border-top: 1px solid #ccc; }}
  .diff-table td {{ font-size: 11px; padding: 6px 10px; }}
}}

/* ── Responsive ──────────────────────────────── */
@media (max-width: 960px) {{
  .layout {{ grid-template-columns: 1fr; }}
  nav {{ position: static; height: auto; flex-direction: row; flex-wrap: wrap; }}
  .nav-header {{ flex: 1; }}
  .nav-chapters {{ display: none; }}
  main {{ padding: 16px; }}
  .meta-grid {{ grid-template-columns: repeat(3, 1fr); }}
  .toolbar {{ flex-direction: column; align-items: flex-start; }}
}}
</style>
</head>
<body>
<div class="layout">

<!-- ── Sidebar ── -->
<nav>
  <div class="nav-header">
    <div class="nav-logo">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#4f7df3" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>
        <line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/>
      </svg>
      <span class="nav-title">{escape(title)}</span>
    </div>
    <div class="nav-revs">
      <div>{escape(short_rev(root, old_rev))}</div>
      <div><span class="rev-arrow">↓</span> {escape(short_rev(root, new_rev))}</div>
    </div>
  </div>
  <div class="nav-actions">
    <button class="nav-btn" onclick="window.print()">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/>
        <rect x="6" y="14" width="12" height="8"/>
      </svg>
      Print / Save PDF
    </button>
    <button class="nav-btn" onclick="expandAll()">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="7 13 12 18 17 13"/><polyline points="7 6 12 11 17 6"/>
      </svg>
      Expand All
    </button>
    <button class="nav-btn" onclick="collapseAll()">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="7 11 12 6 17 11"/><polyline points="7 18 12 13 17 18"/>
      </svg>
      Collapse All
    </button>
  </div>
  <div class="nav-chapters">
    <div class="nav-section-label">Chapters</div>
    {nav_items}
  </div>
</nav>

<!-- ── Content ── -->
<div class="content">
<main>
  <!-- Toolbar -->
  <div class="toolbar">
    <h1 class="toolbar-title">Chapter-wise prose changes</h1>
    <div class="toolbar-actions">
      <button class="btn btn-ghost" onclick="expandAll()">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="7 13 12 18 17 13"/><polyline points="7 6 12 11 17 6"/>
        </svg>Expand All
      </button>
      <button class="btn btn-ghost" onclick="collapseAll()">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="7 11 12 6 17 11"/><polyline points="7 18 12 13 17 18"/>
        </svg>Collapse All
      </button>
      <button class="btn btn-primary" onclick="window.print()">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/>
          <rect x="6" y="14" width="12" height="8"/>
        </svg>Print / Save PDF
      </button>
    </div>
  </div>

  <!-- Hero -->
  <div class="hero">
    <div class="hero-meta">
      <span>📁 {escape(str(root))}</span>
      <span>🕐 {escape(generated_at)}</span>
      <span>🔍 ±{context} context blocks</span>
    </div>
    <div class="meta-grid">
      <div class="metric"><b>{len(chapter_diffs)}</b><span>chapters</span></div>
      <div class="metric"><b>{total_changed}</b><span>changed blocks</span></div>
      <div class="metric"><b>{total_old_words}</b><span>words (old)</span></div>
      <div class="metric"><b>{total_new_words}</b><span>words (new)</span></div>
      <div class="metric {'positive' if word_delta >= 0 else 'negative'}"><b>{word_delta_str}</b><span>word delta</span></div>
    </div>
  </div>

  <!-- Summary -->
  <div class="summary-card">
    <div class="card-header">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/>
        <line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>
      </svg>
      Overview
    </div>
    <table>
      <thead>
        <tr><th>#</th><th>File</th><th>Title</th><th>Status</th><th>Words</th></tr>
      </thead>
      <tbody>
        {summary_rows}
      </tbody>
    </table>
  </div>

  <!-- Chapter diffs -->
  {chapter_sections}

</main>

<!-- Footer -->
<footer>
  <span>
    <span class="footer-brand">LaTeX Content Diff</span> &nbsp;·&nbsp;
    {escape(old_label)} → {escape(new_label)}
  </span>
  <span>Designed by <strong>Yichong SUN</strong> 2026</span>
</footer>
</div><!-- .content -->

</div><!-- .layout -->

<script>
function toggleChapter(el) {{
  el.closest('.chapter').classList.toggle('collapsed');
}}
function expandAll() {{
  document.querySelectorAll('.chapter.collapsed').forEach(c => c.classList.remove('collapsed'));
}}
function collapseAll() {{
  document.querySelectorAll('.chapter:not(.collapsed)').forEach(c => c.classList.add('collapsed'));
}}
// Highlight active nav item on scroll
const chapters = document.querySelectorAll('.chapter[id]');
const navLinks  = document.querySelectorAll('nav .nav-item');
const observer  = new IntersectionObserver(entries => {{
  entries.forEach(e => {{
    if (e.isIntersecting) {{
      navLinks.forEach(a => a.style.background = '');
      const active = document.querySelector(`nav a[href="#${{e.target.id}}"]`);
      if (active) active.style.background = 'var(--nav-hover)';
    }}
  }});
}}, {{ rootMargin: '-20% 0px -70% 0px' }});
chapters.forEach(c => observer.observe(c));
</script>
</body>
</html>
"""


def render_summary_row(index: int, diff: ChapterDiff) -> str:
    word_delta = diff.new_word_count - diff.old_word_count
    sign = "+" if word_delta > 0 else ""
    status = (
        f'<span class="pill pill-changed">{diff.changed_blocks} changed</span>'
        if diff.changed_blocks
        else '<span class="pill pill-clean">no changes</span>'
    )
    return f"""<tr>
  <td class="num">{index}</td>
  <td><span class="path-mono">{escape(diff.path)}</span></td>
  <td>{escape(diff.title)}</td>
  <td>{status}</td>
  <td class="num">{diff.old_word_count} → {diff.new_word_count} <span style="color:{'#276749' if word_delta >= 0 else '#9b2c2c'};font-weight:600">({sign}{word_delta})</span></td>
</tr>"""


def render_chapter(index: int, diff: ChapterDiff) -> str:
    has_changes = bool(diff.changed_blocks)
    index_class = "chapter-index" if has_changes else "chapter-index no-change"
    badges = (
        f'<span class="badge badge-changed">{diff.changed_blocks} changed</span>'
        f'<span class="badge badge-modified">{diff.modified_blocks} modified</span>'
        f'<span class="badge badge-added">{diff.added_blocks} added</span>'
        f'<span class="badge badge-deleted">{diff.deleted_blocks} deleted</span>'
        f'<span class="badge badge-words">{diff.old_word_count} → {diff.new_word_count} words</span>'
    )
    if not diff.rows:
        body = '''<div class="no-changes">
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <polyline points="20 6 9 17 4 12"/>
  </svg>
  No normalized prose changes detected.
</div>'''
    else:
        row_html = "\n".join(render_diff_row(row) for row in diff.rows)
        body = f"""<table class="diff-table">
  <thead><tr><th>Old content</th><th>New content</th></tr></thead>
  <tbody>{row_html}</tbody>
</table>"""
    return f"""<section class="chapter" id="chapter-{index}">
  <div class="chapter-header" onclick="toggleChapter(this)">
    <div class="chapter-header-left">
      <div class="chapter-title-row">
        <div class="{index_class}">{index}</div>
        <h3 class="chapter-title">{escape(diff.title)}</h3>
      </div>
      <div class="chapter-path">{escape(diff.path)}</div>
      <div class="chapter-badges">{badges}</div>
    </div>
    <svg class="toggle-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
      <polyline points="6 9 12 15 18 9"/>
    </svg>
  </div>
  <div class="chapter-body">{body}</div>
</section>"""


def render_diff_row(row: dict[str, str]) -> str:
    kind = row["kind"]
    if kind == "skip":
        return '<tr class="skip"><td colspan="2">unchanged content omitted</td></tr>'
    return f'<tr class="{kind}"><td class="old">{row["old"]}</td><td class="new">{row["new"]}</td></tr>'


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a chapter-wise HTML report of thesis prose changes between two Git revisions.",
    )
    parser.add_argument("old_rev", help="Older Git commit, tag, or revision.")
    parser.add_argument("new_rev", help="Newer Git commit, tag, or revision.")
    parser.add_argument(
        "-o",
        "--output",
        help="Output HTML path. Defaults to diff_reports/thesis-content-diff_<old>_to_<new>.html.",
    )
    parser.add_argument(
        "--thesis",
        default="thesis.tex",
        help="Path to the thesis entry file relative to the repository root.",
    )
    parser.add_argument(
        "--context",
        type=int,
        default=DEFAULT_CONTEXT,
        help="Number of unchanged normalized blocks to show around each change.",
    )
    parser.add_argument(
        "--only-changed",
        action="store_true",
        help="Omit chapters with no normalized prose changes.",
    )
    parser.add_argument(
        "--chapter-prefix",
        default=DEFAULT_CHAPTER_PREFIX,
        help=(
            "Only collect files from \\\\include commands whose path starts with this prefix "
            f"(default: '{DEFAULT_CHAPTER_PREFIX}'). Pass an empty string to collect all \\\\include'd files."
        ),
    )
    parser.add_argument(
        "--title",
        default=DEFAULT_TITLE,
        help=f"Report title shown in the browser tab and navigation sidebar (default: '{DEFAULT_TITLE}').",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    root = find_repo_root(Path.cwd())
    old_label = commit_label(root, args.old_rev)
    new_label = commit_label(root, args.new_rev)
    old_short = short_rev(root, args.old_rev)
    new_short = short_rev(root, args.new_rev)

    new_chapters = list_chapters_at_rev(root, args.new_rev, args.thesis, args.chapter_prefix)
    old_chapters = list_chapters_at_rev(root, args.old_rev, args.thesis, args.chapter_prefix)
    chapter_paths = ordered_union(new_chapters, old_chapters)
    if not chapter_paths:
        raise SystemExit(
            f"No chapter files were found from '{args.thesis}' or '{args.chapter_prefix}' at the requested revisions."
        )

    chapter_diffs = [
        compare_chapter(root, args.old_rev, args.new_rev, path, max(args.context, 0))
        for path in chapter_paths
    ]
    if args.only_changed:
        chapter_diffs = [diff for diff in chapter_diffs if diff.changed_blocks]

    output = Path(args.output) if args.output else root / "diff_reports" / f"thesis-content-diff_{old_short}_to_{new_short}.html"
    if not output.is_absolute():
        output = root / output
    output.parent.mkdir(parents=True, exist_ok=True)

    report = render_html(root, args.old_rev, args.new_rev, old_label, new_label, chapter_diffs, max(args.context, 0), args.title)
    output.write_text(report, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
