"""Turn a run's memo.md into a self-contained memo.html that opens in any browser and prints to PDF.

This is a small converter for exactly the Markdown the memo uses: headings, paragraphs,
pipe tables with alignment, bulleted and numbered lists (nested by indentation), block
quotes, fenced code, horizontal rules, bold, italics, inline code, links and bare URLs,
and source citations such as [S12] or [S13 unverified].

Memo text is written by models and quotes outside sources, so every character of it is
escaped before any tag is added. Links are made only for http, https and mailto
addresses. The page carries a content security policy that allows no script and no
outside requests, so it stays safe and complete when opened offline or mailed.
"""
from __future__ import annotations

import html
from pathlib import Path
import re
from urllib.parse import urlsplit

_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_HEADING = re.compile(r'^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$')
_RULE = re.compile(r'^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$')
_SEPARATOR = re.compile(r'^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$')
_ITEM = re.compile(r'^(\s*)([-*+]|\d{1,9}[.)])[ \t]+(.*)$')
_FENCE = re.compile(r'^\s*(```|~~~)')
_PLACEHOLDER = '{}'

STYLE = '''
:root{color-scheme:light;--ink:#1d2329;--muted:#5b6670;--rule:#d9dee3;--soft:#f4f6f8;--accent:#1f5f8b;--cite:#5b4a8a}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:#fff;color:var(--ink);font:16.5px/1.62 Charter,"Bitstream Charter","Iowan Old Style","Palatino Linotype",Georgia,serif}
main{max-width:52rem;margin:0 auto;padding:2.5rem 1.25rem 4rem}
h1,h2,h3,h4,h5,h6{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;line-height:1.25;margin:2rem 0 .6rem;font-weight:650}
h1{font-size:1.9rem;margin-top:0;letter-spacing:-.01em}
h2{font-size:1.35rem;padding-bottom:.3rem;border-bottom:1px solid var(--rule)}
h3{font-size:1.1rem}h4,h5,h6{font-size:1rem}
p,ul,ol,blockquote,pre,.table{margin:0 0 1rem}
ul,ol{padding-left:1.4rem}li{margin:.2rem 0}li>ul,li>ol{margin:.2rem 0}
a{color:var(--accent);text-decoration:underline;text-decoration-thickness:1px;text-underline-offset:2px;overflow-wrap:anywhere}
strong{font-weight:650}
code{font:.88em/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--soft);padding:.05em .3em;border-radius:3px}
pre{background:var(--soft);padding:.8rem 1rem;border-radius:6px;overflow-x:auto}pre code{background:none;padding:0}
blockquote{margin-left:0;padding:.1rem 1rem;border-left:3px solid var(--rule);color:var(--muted)}
hr{border:0;border-top:1px solid var(--rule);margin:2rem 0}
.cite{color:var(--cite);font-size:.82em;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;white-space:nowrap}
.table{overflow-x:auto;border:1px solid var(--rule);border-radius:6px}
table{border-collapse:collapse;width:100%;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;font-variant-numeric:tabular-nums}
th,td{padding:.42rem .65rem;border-bottom:1px solid var(--rule);vertical-align:top;text-align:left}
th{background:var(--soft);font-weight:600}
tbody tr:last-child td{border-bottom:0}
tbody tr:nth-child(even) td{background:#f8f9fa}
.r{text-align:right}.c{text-align:center}
footer{margin-top:3rem;padding-top:1rem;border-top:1px solid var(--rule);color:var(--muted);font-size:.85rem}
@media (max-width:600px){body{font-size:16px}main{padding:1.5rem 1rem 3rem}table{font-size:13px}}
@media print{
 @page{margin:16mm 14mm}
 body{font-size:10.5pt;background:#fff;color:#000}
 main{max-width:none;padding:0}
 h1,h2,h3,h4{break-after:avoid;page-break-after:avoid}
 .table{overflow:visible;border:0}
 table{font-size:8.5pt}
 thead{display:table-header-group}
 tr,li,blockquote{break-inside:avoid;page-break-inside:avoid}
 a{color:inherit;text-decoration:none}
 tbody tr:nth-child(even) td{background:#f3f3f3;-webkit-print-color-adjust:exact;print-color-adjust:exact}
 footer{font-size:8pt}
}
'''


def _escape(text):
    return html.escape(text, quote=True)


def _safe_href(url):
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() in ('http', 'https') and parts.netloc:
        return url
    if parts.scheme.lower() == 'mailto' and '@' in url:
        return url
    return None


def _short(url, limit=60):
    """Host and path, for display; the full address stays in the link."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url[:limit]
    shown = (parts.netloc + parts.path).rstrip('/') or url
    if parts.query:
        shown += '?…'
    return shown if len(shown) <= limit else shown[:limit - 1] + '…'


class _Inline:
    """Inline formatting on escaped text, with finished fragments held aside as placeholders."""

    def __init__(self):
        self.held = []

    def hold(self, fragment):
        self.held.append(fragment)
        return _PLACEHOLDER.format(len(self.held) - 1)

    def render(self, text):
        text = text.replace('', '').replace('', '')
        # Code spans first: their contents are shown literally.
        text = re.sub(r'(`+)(.+?)\1', lambda m: self.hold('<code>' + _escape(m.group(2).strip()) + '</code>'), text)
        # Links [label](address); an address that is not http(s) or mailto stays as text.
        def link(match):
            label, href = match.group(1), _safe_href(match.group(2))
            if not href:
                return self.hold(_escape(match.group(0)))
            return self.hold(f'<a href="{_escape(href)}">{self.render_plain(label)}</a>')
        text = re.sub(r'\[([^\[\]\n]{1,500})\]\((\S{1,4000}?)\)', link, text)
        # Bare addresses, shown as host and path.
        def bare(match):
            url = match.group(0)
            trail = ''
            while url and url[-1] in '.,;:!?)\'"]':
                if url[-1] == ')' and url.count('(') >= url.count(')'):
                    break
                trail = url[-1] + trail
                url = url[:-1]
            href = _safe_href(url)
            if not href:
                return _escape(match.group(0))
            return self.hold(f'<a href="{_escape(href)}" title="{_escape(href)}">{_escape(_short(href))}</a>') + _escape(trail)
        text = re.sub(r'(?:https?://|mailto:)[^\s<>\uE000]+', bare, text)
        # Source citations: [S12], [S13 unverified], [S2, S4].
        text = re.sub(r'\[(S\d{1,4}(?:[ ,;][^\[\]\n]{0,60})?)\]',
                      lambda m: self.hold('<span class="cite">[' + _escape(m.group(1)) + ']</span>'), text)
        return self.restore(self.render_plain(text))

    def render_plain(self, text):
        """Escape, then bold and italics. Placeholders pass through untouched."""
        out = _escape(text)
        out = re.sub(r'\*\*(?=\S)(.+?)(?<=\S)\*\*', r'<strong>\1</strong>', out)
        out = re.sub(r'(?<![\w*])__(?=\S)(.+?)(?<=\S)__(?![\w*])', r'<strong>\1</strong>', out)
        out = re.sub(r'(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])', r'<em>\1</em>', out)
        out = re.sub(r'(?<![\w])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w])', r'<em>\1</em>', out)
        return out

    def restore(self, text):
        for _ in range(4):  # held fragments can contain other held fragments (a link label with a citation)
            replaced = re.sub('(\\d+)', lambda m: self.held[int(m.group(1))], text)
            if replaced == text:
                break
            text = replaced
        return text


def _cells(line):
    line = line.strip()
    if line.startswith('|'):
        line = line[1:]
    if line.endswith('|') and not line.endswith('\\|'):
        line = line[:-1]
    cells, current, escaped = [], '', False
    for char in line:
        if escaped:
            current += char if char == '|' else '\\' + char
            escaped = False
        elif char == '\\':
            escaped = True
        elif char == '|':
            cells.append(current.strip())
            current = ''
        else:
            current += char
    if escaped:
        current += '\\'
    cells.append(current.strip())
    return cells


def _alignments(line):
    out = []
    for cell in _cells(line):
        cell = cell.strip()
        if cell.startswith(':') and cell.endswith(':'):
            out.append(' class="c"')
        elif cell.endswith(':'):
            out.append(' class="r"')
        else:
            out.append('')
    return out


def _slug(text, used):
    base = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:60] or 'section'
    slug, number = base, 2
    while slug in used:
        slug, number = f'{base}-{number}', number + 1
    used.add(slug)
    return slug


def body(markdown):
    """The memo as an HTML fragment. Every piece of memo text is escaped."""
    inline = _Inline()
    lines = _CONTROL.sub('', markdown.replace('\r\n', '\n').replace('\r', '\n')).split('\n')
    out, used, index = [], set(), 0
    paragraph = []

    def flush():
        if paragraph:
            parts = []
            for number, line in enumerate(paragraph):
                hard = line.endswith('  ') and number < len(paragraph) - 1
                parts.append(inline.render(line.strip()) + ('<br>' if hard else ''))
            out.append('<p>' + '\n'.join(parts) + '</p>')
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            flush()
            index += 1
            continue
        fence = _FENCE.match(line)
        if fence:
            flush()
            marker, code = fence.group(1), []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith(marker):
                code.append(lines[index])
                index += 1
            index += 1
            out.append('<pre><code>' + _escape('\n'.join(code)) + '</code></pre>')
            continue
        heading = _HEADING.match(stripped)
        if heading:
            flush()
            level, text = len(heading.group(1)), heading.group(2)
            out.append(f'<h{level} id="{_slug(text, used)}">{inline.render(text)}</h{level}>')
            index += 1
            continue
        if stripped.startswith('|') and index + 1 < len(lines) and _SEPARATOR.match(lines[index + 1]) \
                and '-' in lines[index + 1]:
            flush()
            header, align = _cells(stripped), _alignments(lines[index + 1])
            width = len(header)
            align = (align + [''] * width)[:width]
            rows = []
            index += 2
            while index < len(lines) and lines[index].strip().startswith('|'):
                rows.append((_cells(lines[index]) + [''] * width)[:width])
                index += 1
            parts = ['<div class="table"><table><thead><tr>']
            parts += [f'<th{a}>{inline.render(cell)}</th>' for cell, a in zip(header, align)]
            parts.append('</tr></thead><tbody>')
            for row in rows:
                parts.append('<tr>' + ''.join(f'<td{a}>{inline.render(cell)}</td>' for cell, a in zip(row, align)) + '</tr>')
            parts.append('</tbody></table></div>')
            out.append(''.join(parts))
            continue
        if _RULE.match(line) and not paragraph:
            out.append('<hr>')
            index += 1
            continue
        if stripped.startswith('>'):
            flush()
            quoted = []
            while index < len(lines) and lines[index].strip().startswith('>'):
                quoted.append(re.sub(r'^\s*>\s?', '', lines[index]))
                index += 1
            out.append('<blockquote>' + body('\n'.join(quoted)) + '</blockquote>')
            continue
        item = _ITEM.match(line)
        if item and (not paragraph or not item.group(2)[0].isdigit()):
            flush()
            index = _list(lines, index, out, inline)
            continue
        paragraph.append(line)
        index += 1
    flush()
    return '\n'.join(out)


def _list(lines, index, out, inline):
    """Nested lists by indentation; returns the index after the list."""
    stack = []  # (indent, tag)
    open_item = []

    def close_to(indent):
        while stack and stack[-1][0] > indent:
            out.append('</li></' + stack.pop()[1] + '>')

    while index < len(lines):
        line = lines[index]
        if not line.strip():
            # A blank line ends the list unless the next line continues it.
            following = lines[index + 1] if index + 1 < len(lines) else ''
            if _ITEM.match(following) and stack:
                index += 1
                continue
            break
        item = _ITEM.match(line)
        if not item:
            if stack and (line.startswith('  ') or line.startswith('\t')):
                out.append(' ' + inline.render(line.strip()))
                index += 1
                continue
            break
        indent = len(item.group(1).expandtabs(4))
        tag = 'ol' if item.group(2)[0].isdigit() else 'ul'
        close_to(indent)
        if stack and stack[-1][0] == indent:
            if stack[-1][1] != tag:
                out.append('</li></' + stack.pop()[1] + '>')
                out.append(f'<{tag}><li>')
                stack.append((indent, tag))
            else:
                out.append('</li><li>')
        else:
            start = ''
            if tag == 'ol':
                number = int(re.match(r'\d+', item.group(2)).group(0))
                start = f' start="{number}"' if number != 1 else ''
            out.append(f'<{tag}{start}><li>')
            stack.append((indent, tag))
        out.append(inline.render(item.group(3)))
        open_item.append(True)
        index += 1
    close_to(-1)
    return index


def _title(markdown):
    for line in markdown.splitlines():
        match = _HEADING.match(line.strip())
        if match and len(match.group(1)) == 1:
            return re.sub(r'[*_`]', '', match.group(2)).strip()
    return 'Research memo'


def convert(markdown, title=None):
    """A complete, self-contained HTML document for ``markdown``."""
    title = title or _title(markdown)
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; '
            'img-src data:; base-uri \'none\'; form-action \'none\'">\n'
            '<meta name="referrer" content="no-referrer">\n'
            f'<title>{_escape(title)}</title>\n<style>{STYLE}</style>\n</head>\n<body>\n<main>\n'
            + body(markdown) +
            '\n<footer>Written by IntrinsicValue from memo.md in the same folder. Valuation research: '
            'it is not investment advice. To save a PDF, use your browser\'s Print command.</footer>\n'
            '</main>\n</body>\n</html>\n')


MAX_MEMO_BYTES = 10_000_000


def write_beside(folder):
    """Write memo.html next to memo.md in ``folder``; return its path, or None when there is no memo."""
    folder = Path(folder)
    memo = folder / 'memo.md'
    if not memo.is_file() or memo.is_symlink() or memo.stat().st_size > MAX_MEMO_BYTES:
        return None
    target = folder / 'memo.html'
    temporary = folder / 'memo.html.tmp'
    temporary.write_text(convert(memo.read_text(encoding='utf-8', errors='replace')), encoding='utf-8')
    temporary.replace(target)
    return target


def write_for(markdown_path, html_path=None):
    """Convert one Markdown file; the HTML goes beside it unless ``html_path`` is given."""
    source = Path(markdown_path)
    target = Path(html_path) if html_path else source.with_suffix('.html')
    target.write_text(convert(source.read_text(encoding='utf-8', errors='replace')), encoding='utf-8')
    return target


def main(argv=None):
    import sys
    argv = sys.argv[1:] if argv is None else argv
    if not argv or len(argv) > 2:
        print('Usage: python3 -m investmentideas.memo_html MEMO.md [MEMO.html]', file=sys.stderr)
        return 2
    print(write_for(*argv))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
