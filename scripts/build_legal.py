#!/usr/bin/env python3
"""Builds a legal page (e.g. legal/privacy.md -> _site/privacy.html).

Usage: build_legal.py SOURCE.md OUTPUT.html

Converts the small Markdown subset the legal pages use (headings, paragraphs,
lists, tables, blockquotes, rules, bold/italic/code/links) with the standard
library only. Refuses to build while the page is unfinished — a DRAFT note or
any [BRACKETED PLACEHOLDER] left in — so a draft never goes live.
"""

import html
import re
import sys

# An ALL-CAPS bracket like [SUPPORT EMAIL] or [POSTAL ADDRESS: ...]; normal
# bracketed text such as "[your name]" or link labels isn't matched.
PLACEHOLDER = re.compile(r"\[[A-Z]{3,}(?: [A-Z]+)*(?:[:,][^\]]*)?\]")


def inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    # Web and email links: [label](https://…) or [label](mailto:…).
    text = re.sub(r"\[([^\]]+)\]\(((?:https?://|mailto:)[^)\s]+)\)",
                  lambda m: f'<a href="{html.escape(m.group(2))}">{m.group(1)}</a>', text)
    return text


def cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def convert(markdown: str) -> tuple[str, str]:
    lines = markdown.splitlines()
    out: list[str] = []
    title = "GP Passport"
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line:
            i += 1
        elif line.startswith("# "):
            title = line[2:].strip()
            out.append(f"<h1>{inline(title)}</h1>")
            i += 1
        elif line.startswith("## "):
            out.append(f"<h2>{inline(line[3:].strip())}</h2>")
            i += 1
        elif line == "---":
            out.append("<hr>")
            i += 1
        elif line.startswith("> "):
            quote = []
            while i < len(lines) and lines[i].startswith("> "):
                quote.append(inline(lines[i][2:].strip()))
                i += 1
            out.append(f"<blockquote><p>{' '.join(quote)}</p></blockquote>")
        elif line.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{inline(lines[i][2:].strip())}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(lines[i])
                i += 1
            head = "".join(f"<th>{inline(c)}</th>" for c in cells(rows[0]))
            body = "".join(
                "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells(r)) + "</tr>"
                for r in rows[2:]
            )
            out.append(f'<div class="table"><table><thead><tr>{head}</tr></thead>'
                       f"<tbody>{body}</tbody></table></div>")
        else:
            # A paragraph; single line breaks are kept (e.g. the contact block).
            para = []
            while i < len(lines) and lines[i].strip() and not re.match(r"(#|>|- |\||---$)", lines[i]):
                para.append(inline(lines[i].strip()))
                i += 1
            out.append(f"<p>{'<br>'.join(para)}</p>")
    return title, "\n".join(out)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{title}</title>
<style>
  body {{ font: 17px/1.55 -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
         max-width: 720px; margin: 0 auto; padding: 32px 20px 64px; color: #1d1d1f; background: #fff; }}
  h1 {{ font-size: 30px; line-height: 1.2; }}
  h2 {{ font-size: 21px; margin-top: 36px; }}
  a {{ color: #2f5bea; }}
  hr {{ border: 0; border-top: 1px solid #d2d2d7; margin: 32px 0; }}
  blockquote {{ margin: 0; padding: 4px 16px; border-left: 3px solid #d2d2d7; color: #6e6e73; }}
  code {{ font-size: 15px; }}
  .table {{ overflow-x: auto; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 15px; }}
  th, td {{ text-align: left; vertical-align: top; padding: 8px 10px; border-bottom: 1px solid #d2d2d7; }}
  th {{ font-weight: 600; }}
  @media (prefers-color-scheme: dark) {{
    body {{ color: #f5f5f7; background: #000; }}
    a {{ color: #6e8ff5; }}
    hr, th, td, blockquote {{ border-color: #424245; }}
    blockquote {{ color: #a1a1a6; }}
  }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    source, output = sys.argv[1], sys.argv[2]
    markdown = open(source, encoding="utf-8").read()

    problems = sorted(set(PLACEHOLDER.findall(markdown)))
    if "DRAFT" in markdown:
        problems.insert(0, "DRAFT note")
    if problems:
        print(f"{source} isn't ready to publish. Fill in or remove: {', '.join(problems)}")
        return 1

    title, body = convert(markdown)
    with open(output, "w", encoding="utf-8") as f:
        f.write(PAGE.format(title=html.escape(title), body=body))
    print(f"Built {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
