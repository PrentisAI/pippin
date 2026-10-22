"""A run's report, for reading and for taking away.

* :func:`render_html`: the report as safe HTML for the console, with its recorded results and its
  papers marked so the console can show them as chips;
* :func:`to_latex`: the report as a LaTeX article, with ``\\cite`` for papers, a reference to an
  appendix table for each recorded result, and the figures;
* :func:`reproduce_script`: ``reproduce.sh``, the commands that produced the results, in order, in
  the run's image with the packages it had installed;
* :func:`export_zip`: the report, LaTeX source, figures, bibliography, results, the scripts that
  produced them and ``reproduce.sh`` in one archive (or the whole workspace).
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
import shlex
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote

from aurora.results.ledger import ResultsLedger, result_refs

_KEY = r"[A-Z][A-Za-z\-']{1,16}\d{2}[a-z]?"
_KEYS = re.compile(rf"\[({_KEY}(?:\s*[,;]\s*{_KEY})*)\]")
_RESULT = re.compile(r"\{(R\d+)\}")
_IMG = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_FIGURE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf", ".webp"}


def latest_report(campaign_dir: Path) -> Path | None:
    drafts = sorted((Path(campaign_dir) / "phases" / "writing").glob("draft_*.md"),
                    key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    return drafts[-1] if drafts else None


def figures_in(md: str, report_dir: Path, campaign_dir: Path) -> list[tuple[str, Path]]:
    """(link as written, file) for each figure the report shows that exists in the campaign."""
    out = []
    root = campaign_dir.resolve()
    for _, src in _IMG.findall(md):
        if re.match(r"^(https?:|data:)", src):
            continue
        target = (report_dir / src).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            alt = (root / "workspace" / src.lstrip("/").removeprefix("app/")).resolve()
            target = alt if alt.is_relative_to(root) and alt.is_file() else target
        if target.is_file() and target.is_relative_to(root):
            out.append((src, target))
    return out


# ---------------------------------------------------------------------------- HTML

_ALLOWED = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "strong", "em", "b", "i", "code", "pre",
            "blockquote", "table", "thead", "tbody", "tr", "th", "td", "a", "img", "hr", "br", "span", "sup", "sub",
            "del", "div", "figure", "figcaption", "button"}
_VOID = {"img", "hr", "br"}
_ATTRS = {"a": {"href", "title"}, "img": {"src", "alt", "title"}, "th": {"align"}, "td": {"align"},
          "button": {"data-result", "data-cite", "class", "type"}, "span": {"class"}}


class _Sanitizer(HTMLParser):
    """Keeps a small set of tags and attributes; drops scripts, styles, event handlers and the like."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "iframe", "object", "embed", "form", "svg", "math"):
            self._skip += 1
            return
        if self._skip or tag not in _ALLOWED:
            return
        keep = []
        for k, v in attrs:
            if k not in _ATTRS.get(tag, set()) or v is None:
                continue
            if k in ("href", "src") and re.match(r"^\s*(javascript|vbscript|data:text)", v, re.I):
                continue
            keep.append(f' {k}="{html.escape(v, quote=True)}"')
        if tag == "a":
            keep.append(' target="_blank" rel="noreferrer"')
        self.out.append(f"<{tag}{''.join(keep)}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "iframe", "object", "embed", "form", "svg", "math"):
            self._skip = max(0, self._skip - 1)
            return
        if self._skip or tag not in _ALLOWED or tag in _VOID:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.out.append(html.escape(data, quote=False))


def sanitize(markup: str) -> str:
    s = _Sanitizer()
    s.feed(markup)
    s.close()
    return "".join(s.out)


def render_html(md: str, *, image_url: Any) -> str:
    """The report as HTML.  ``image_url(src) -> str`` gives the URL a figure is served from (or '').
    Results ``{R3}`` and papers ``[Key]`` become buttons the console turns into chips."""
    import markdown

    def img(m: re.Match[str]) -> str:
        url = image_url(m.group(2))
        return f"![{m.group(1)}]({url})" if url else f"*[figure not found: {m.group(2)}]*"
    text = _IMG.sub(img, md)
    out = sanitize(markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"]))
    # outside code: {R3} and [Key] as buttons
    parts = re.split(r"(<pre>.*?</pre>|<code>.*?</code>)", out, flags=re.S)
    for i, part in enumerate(parts):
        if part.startswith(("<pre>", "<code>")):
            continue
        part = _RESULT.sub(lambda m: f'<button type="button" class="res-chip" data-result="{m.group(1)}">{m.group(1)}</button>', part)
        part = _KEYS.sub(lambda m: "".join(f'<button type="button" class="cite p" data-cite="{k}">{k}</button>'
                                           for k in re.split(r"\s*[,;]\s*", m.group(1))), part)
        parts[i] = part
    return "".join(parts)


# ---------------------------------------------------------------------------- LaTeX

_TEX_ESC = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
            "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def _esc(text: str) -> str:
    return "".join(_TEX_ESC.get(c, c) for c in text)


def _inline(text: str, results: dict[str, dict[str, Any]], figure_names: dict[str, str]) -> str:
    """Inline Markdown to LaTeX: math kept, code, bold, italic, links, [Key] and {R3}."""
    tokens: list[str] = []

    def keep(s: str) -> str:
        tokens.append(s)
        return f"\x00{len(tokens) - 1}\x00"
    text = re.sub(r"\$\$(.+?)\$\$", lambda m: keep(f"\\[{m.group(1)}\\]"), text)
    text = re.sub(r"(?<!\\)\$(.+?)(?<!\\)\$", lambda m: keep(f"${m.group(1)}$"), text)
    text = re.sub(r"`([^`]+)`", lambda m: keep(r"\texttt{" + _esc(m.group(1)) + "}"), text)
    text = _KEYS.sub(lambda m: keep(r"\citep{" + ",".join(re.split(r"\s*[,;]\s*", m.group(1))) + "}"), text)
    text = _RESULT.sub(lambda m: keep(r"\textsuperscript{\hyperref[res:" + m.group(1) + "]{" + m.group(1) + "}}"
                                      if m.group(1) in results else r"\textsuperscript{" + m.group(1) + "?}"), text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", lambda m: keep(r"\href{" + m.group(2).replace("%", r"\%")
                                                                          + "}{" + _esc(m.group(1)) + "}"), text)
    text = _esc(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\\emph{\1}", text)
    text = re.sub(r"(?<![\w\\])\\_(?!\s)(.+?)(?<!\s)\\_(?![\w])", r"\\emph{\1}", text)
    text = text.replace("±", r"$\pm$").replace("≈", r"$\approx$").replace("≤", r"$\leq$").replace("≥", r"$\geq$")
    text = text.replace("×", r"$\times$").replace("−", "-")
    return re.sub("\x00(\\d+)\x00", lambda m: tokens[int(m.group(1))], text)


def to_latex(md: str, *, results: list[dict[str, Any]], figure_names: dict[str, str], title: str = "") -> str:
    """The report as a LaTeX article.  ``figure_names``: link as written -> file name in ``figures/``."""
    by_id = {r["id"]: r for r in results}
    lines = md.splitlines()
    body: list[str] = []
    i = 0
    doc_title = title
    in_list: str = ""

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            body.append(r"\end{" + in_list + "}")
            in_list = ""
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith("```"):
            close_list()
            j = i + 1
            code = []
            while j < len(lines) and not lines[j].strip().startswith("```"):
                code.append(lines[j])
                j += 1
            body.append("\\begin{verbatim}\n" + "\n".join(code) + "\n\\end{verbatim}")
            i = j + 1
            continue
        if m := re.match(r"^(#{1,6})\s+(.*)$", s):
            close_list()
            level = len(m.group(1))
            text = _inline(m.group(2).strip(), by_id, figure_names)
            if level == 1 and not doc_title:
                doc_title = text
            elif level == 1 or level == 2:
                body.append(r"\section{" + text + "}")
            elif level == 3:
                body.append(r"\subsection{" + text + "}")
            else:
                body.append(r"\paragraph{" + text + "}")
            i += 1
            continue
        if (m := _IMG.fullmatch(s)) and m.group(2) in figure_names:
            close_list()
            body.append("\\begin{figure}[ht]\n\\centering\n\\includegraphics[width=0.8\\linewidth]{figures/"
                        + figure_names[m.group(2)] + "}\n\\caption{" + _inline(m.group(1), by_id, figure_names)
                        + "}\n\\end{figure}")
            i += 1
            continue
        if s.startswith("|") and i + 1 < len(lines) and re.match(r"^\|?\s*:?-{2,}", lines[i + 1].strip()):
            close_list()
            rows = []
            j = i
            while j < len(lines) and lines[j].strip().startswith("|"):
                if j != i + 1:
                    rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            ncol = max(len(r) for r in rows)
            out = ["\\begin{table}[ht]\n\\centering\n\\small\n\\begin{tabular}{" + "l" * ncol + "}\n\\hline"]
            for k, r in enumerate(rows):
                out.append(" & ".join(_inline(c, by_id, figure_names) for c in r + [""] * (ncol - len(r))) + r" \\"
                           + ("\n\\hline" if k == 0 else ""))
            out.append("\\hline\n\\end{tabular}\n\\end{table}")
            body.append("\n".join(out))
            i = j
            continue
        if m := re.match(r"^\s*([-*+]|\d+[.)])\s+(.*)$", line):
            kind = "enumerate" if m.group(1)[0].isdigit() else "itemize"
            if in_list != kind:
                close_list()
                body.append(r"\begin{" + kind + "}")
                in_list = kind
            body.append(r"\item " + _inline(m.group(2), by_id, figure_names))
            i += 1
            continue
        if s.startswith(">"):
            close_list()
            body.append("\\begin{quote}\n" + _inline(s.lstrip("> "), by_id, figure_names) + "\n\\end{quote}")
            i += 1
            continue
        if not s:
            close_list()
            body.append("")
            i += 1
            continue
        if s in ("---", "***"):
            close_list()
            i += 1
            continue
        if re.match(r"^#{1,2}\s*References\s*$", s, re.I):
            break
        close_list()
        para = [s]
        j = i + 1
        while j < len(lines) and lines[j].strip() and not re.match(r"^(#|\s*[-*+]\s|\s*\d+[.)]\s|```|\||>|!\[)", lines[j]):
            para.append(lines[j].strip())
            j += 1
        body.append(_inline(" ".join(para), by_id, figure_names))
        i = j
    close_list()
    # the reference list the report ends with is replaced by the bibliography
    text = "\n".join(body)
    text = re.sub(r"\\section\{References\}.*", "", text, flags=re.S)
    cited = [r for r in results if r["id"] in result_refs(md)]
    table = ""
    if cited:
        rows = []
        for r in cited:
            where = (r.get("script") or {}).get("path", "") or (r["outputs"][0]["path"] if r.get("outputs") else "")
            rows.append("\\label{res:" + r["id"] + "}" + r["id"] + " & " + _esc(r["name"]) + " & "
                        + _esc(ResultsLedger.show_value(r)) + " & \\texttt{" + _esc(where) + "} & "
                        + _esc(r.get("job") or "") + r" \\")
        table = ("\n\\appendix\n\\section{Provenance of the reported results}\n"
                 "Each number in the text marked R$n$ was recorded when it was computed, with the script and "
                 "command that produced it (copies of the scripts are in \\texttt{provenance/}).\n\n"
                 "\\begin{longtable}{lp{0.3\\linewidth}lp{0.25\\linewidth}l}\n\\hline\nID & Result & Value & Script / output & Job"
                 r" \\" + "\n\\hline\n" + "\n".join(rows) + "\n\\hline\n\\end{longtable}\n")
    return ("\\documentclass[11pt]{article}\n\\usepackage[utf8]{inputenc}\n\\usepackage[T1]{fontenc}\n"
            "\\usepackage{graphicx,amsmath,amssymb,booktabs,longtable}\n\\usepackage[numbers]{natbib}\n"
            "\\usepackage[hidelinks]{hyperref}\n\\usepackage[margin=1in]{geometry}\n\n"
            f"\\title{{{doc_title or 'Report'}}}\n\\author{{Written with AURORA}}\n\\date{{\\today}}\n\n"
            "\\begin{document}\n\\maketitle\n\n" + text.strip() + "\n"
            + ("\n\\bibliographystyle{plainnat}\n\\bibliography{references}\n" if _KEYS.search(md) else "")
            + table + "\n\\end{document}\n")


# ---------------------------------------------------------------------------- reproduce.sh

def reproduce_script(campaign_dir: Path, *, image: str, results: list[dict[str, Any]],
                     cluster_jobs: list[Any], run_name: str = "") -> str:
    """The commands that produced the run's results, in the order they were recorded, in its image."""
    steps: list[tuple[float, str, str]] = []
    seen: set[str] = set()
    for r in results:
        cmd = (r.get("command") or "").strip()
        if cmd and cmd not in seen:
            seen.add(cmd)
            steps.append((r.get("at", 0), cmd, f"{', '.join(x['id'] for x in results if (x.get('command') or '').strip() == cmd)}"))
    for j in cluster_jobs:
        cmd = (getattr(j, "command", "") or "").strip()
        if cmd and cmd not in seen:
            seen.add(cmd)
            steps.append((getattr(j, "submitted_at", 0) or 0, cmd, f"cluster job {j.id} ({j.name}, {j.gpus} GPU)"))
    steps.sort(key=lambda s: s[0])
    env = Path(campaign_dir) / "workspace" / ".aurora" / "environment.txt"
    lines = [
        "#!/usr/bin/env bash",
        f"# Reproduce the results of the AURORA run {run_name or Path(campaign_dir).name}.",
        "# It replays, in the run's container image, the commands that produced the recorded results,",
        "# in the order they were run. Run it from the folder that holds the run's workspace/ (this archive).",
        "# Data the agents downloaded during the run is fetched again by the same commands if they did so.",
        "set -euo pipefail",
        'HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        f"IMAGE={shlex.quote(image or 'python:3.11-slim')}",
        "",
        "run() {   # one step, in the image, with the workspace as /app",
        '  docker run --rm -v "$HERE/workspace:/app" -w /app "$IMAGE" bash -lc "$1"',
        "}",
        "",
    ]
    if env.is_file():
        lines += ["# the packages the run had installed", "run 'pip install -r /app/.aurora/requirements-reproduce.txt'", ""]
    if not steps:
        lines.append("echo 'This run recorded no commands; see the report and provenance/ for the scripts.'")
    for n, (_, cmd, what) in enumerate(steps, 1):
        lines.append(f"# step {n}: {what}")
        lines.append(f"run {shlex.quote(cmd)}")
        lines.append("")
    return "\n".join(lines) + "\n"


def requirements_from(env_text: str) -> str:
    """pip requirements from the environment file (its first line is the Python version)."""
    return "\n".join(line for line in env_text.splitlines() if "==" in line and not line.startswith("#")) + "\n"


# ---------------------------------------------------------------------------- the archive

def results_csv(results: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "name", "value", "uncertainty", "unit", "job", "script", "command", "inputs", "outputs",
                "supersedes", "description"])
    for r in results:
        w.writerow([r["id"], r["name"], r["value"], r.get("uncertainty") if r.get("uncertainty") is not None else "",
                    r.get("unit", ""), r.get("job", ""), (r.get("script") or {}).get("path", ""), r.get("command", ""),
                    " ".join(f["path"] for f in r.get("inputs") or []), " ".join(f["path"] for f in r.get("outputs") or []),
                    r.get("supersedes", ""), r.get("description", "")])
    return buf.getvalue()


def export_zip(campaign_dir: Path, *, what: str, image: str, run_name: str, bib: str = "",
               cluster_jobs: list[Any] | None = None, max_bytes: int = 2 * 1024**3) -> bytes:
    """``what``: ``report`` (report, LaTeX, figures, bibliography, results, scripts, reproduce.sh) or
    ``workspace`` (everything the run produced, without hidden caches)."""
    campaign_dir = Path(campaign_dir)
    buf = io.BytesIO()
    ledger = ResultsLedger(campaign_dir)
    results = ledger.all()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if what == "workspace":
            total = 0
            for f in sorted(campaign_dir.rglob("*")):
                rel = f.relative_to(campaign_dir)
                if not f.is_file() or any(p in (".git", "__pycache__", ".venv", "node_modules", ".cache")
                                          for p in rel.parts):
                    continue
                total += f.stat().st_size
                if total > max_bytes:
                    z.writestr("TRUNCATED.txt", f"The workspace is larger than {max_bytes // 1024**2} MB; "
                                                "the rest was left out. Copy it from the server instead.")
                    break
                z.write(f, str(rel))
        report = latest_report(campaign_dir)
        md = report.read_text(errors="replace") if report else ""
        figures = figures_in(md, report.parent, campaign_dir) if report else []
        names: dict[str, str] = {}
        for src, f in figures:
            name = f.name
            while name in names.values() and names.get(src) != name:
                name = f"{f.stem}_{len(names)}{f.suffix}"
            names[src] = name
        prefix = "" if what == "report" else "export/"
        if report:
            z.writestr(prefix + "report.md", md)
            z.writestr(prefix + "report.tex", to_latex(md, results=results, figure_names=names))
            for src, f in figures:
                z.write(f, prefix + "figures/" + names[src])
        if bib:
            z.writestr(prefix + "references.bib", bib)
        if results:
            z.writestr(prefix + "results.csv", results_csv(results))
            z.writestr(prefix + "results.json", json.dumps(results, indent=1))
            snaps = campaign_dir / "provenance"
            if snaps.is_dir():
                for f in sorted(snaps.rglob("*")):
                    if f.is_file():
                        z.write(f, prefix + str(f.relative_to(campaign_dir)))
        env = campaign_dir / "workspace" / ".aurora" / "environment.txt"
        if env.is_file():
            z.writestr(prefix + "environment.txt", env.read_text(errors="replace"))
            z.writestr(prefix + "workspace/.aurora/requirements-reproduce.txt", requirements_from(env.read_text(errors="replace")))
        z.writestr(prefix + "reproduce.sh", reproduce_script(campaign_dir, image=image, results=results,
                                                              cluster_jobs=cluster_jobs or [], run_name=run_name))
        z.writestr(prefix + "README.txt", (
            f"{run_name}\n\nreport.md / report.tex   the report (LaTeX: compile with pdflatex + bibtex)\n"
            "figures/                 the figures the report shows\nreferences.bib           the papers it cites (all checked)\n"
            "results.csv, .json       every recorded result, with the script, command, files and job\n"
            "provenance/R<n>/         the script as it was when result R<n> was recorded\n"
            "reproduce.sh             replays the commands that produced the results, in the run's image\n"
            + ("" if what == "report" else "the rest                 the run's campaign directory (its workspace/ is the container's /app)\n")))
    return buf.getvalue()


def image_url_for(campaign_id: str, run_id: str, report: Path, campaign_dir: Path) -> Any:
    """``src -> URL`` for a report's figures, served by the console's workspace route."""
    root = campaign_dir.resolve()

    def url(src: str) -> str:
        if re.match(r"^https?:", src):
            return ""
        target = (report.parent / src).resolve()
        if not (target.is_relative_to(root) and target.is_file()):
            alt = (root / "workspace" / src.lstrip("/").removeprefix("app/")).resolve()
            target = alt if alt.is_relative_to(root) and alt.is_file() else target
        if not (target.is_relative_to(root) and target.is_file()) or target.suffix.lower() not in _FIGURE_EXT:
            return ""
        rel = target.relative_to(root).as_posix()
        return f"/api/campaigns/{campaign_id}/experiments/{run_id}/workspace/raw?path={quote(rel)}"
    return url
