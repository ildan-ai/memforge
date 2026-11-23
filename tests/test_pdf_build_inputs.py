"""The architecture PDF carries only what the Markdown says, from the
repository's own files.

Chrome prints the HTML pandoc writes. Raw HTML in the Markdown, an image
outside the diagrams folder, an attribute such as `style`, or document
metadata would each let a docs change make that HTML frame a local file, run a
script or fetch from the network at build time. The filter refuses each one,
and the build runs Chrome with no route to the network. The workflows that
build the PDF keep no token in the checkout.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
ARCH = REPO / "docs" / "architecture"
PDF = REPO / "docs" / "pdf"
FILES = ("diagrams.md", "overview.md", "threat-model.md")

needs_pandoc = pytest.mark.skipif(shutil.which("pandoc") is None, reason="needs pandoc")


def _checked_diagrams(arch: Path, listing: Path) -> Path:
    """The list build-pdf.sh hands the filter: the diagram names in arch's
    diagrams folder that are regular files, not links, spelled as listed."""
    names = [entry.name for entry in os.scandir(arch / "diagrams")
             if entry.is_file(follow_symlinks=False) and re.fullmatch(r"[A-Za-z0-9_-]+\.svg", entry.name)]
    listing.write_text("".join(f"{name}\n" for name in names), encoding="utf-8")
    return listing


def _pandoc(arch: Path, listing: Path | None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("ARCHITECTURE_DIAGRAMS", None)
    if listing is not None:
        env["ARCHITECTURE_DIAGRAMS"] = str(listing)
    return subprocess.run(
        ["pandoc", "--from", "markdown+gfm_auto_identifiers", "--to", "html5", "--standalone",
         "--lua-filter", str(PDF / "architecture.lua"), "--metadata", "title=MemForge",
         "--metadata", "subtitle=Architecture", *FILES],
        cwd=arch, env=env, capture_output=True, text=True, timeout=120,
    )


def _pandoc_checked(arch: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    return _pandoc(arch, _checked_diagrams(arch, tmp_path / "diagrams.list"))


def _with_overview_tail(tmp_path: Path, tail: str, head: str = "") -> Path:
    arch = tmp_path / "architecture"
    shutil.copytree(ARCH, arch)
    overview = arch / "overview.md"
    overview.write_text(head + overview.read_text(encoding="utf-8") + "\n" + tail + "\n", encoding="utf-8")
    return arch


@needs_pandoc
def test_the_repository_docs_still_build(tmp_path):
    result = _pandoc_checked(ARCH, tmp_path)
    assert result.returncode == 0, result.stderr


@needs_pandoc
@pytest.mark.parametrize("tail,refusal", [
    ('<iframe src="file:///etc/hosts"></iframe>', "raw HTML"),
    ('<object data="file:///etc/hosts"></object>', "raw HTML"),
    ("<script>document.title = 'ran'</script>", "raw HTML"),
    ("Text with <img src=\"http://127.0.0.1:9/beacon\"> inline.", "raw HTML"),
    ("```{=html}\n<iframe src=\"file:///etc/hosts\"></iframe>\n```", "raw HTML"),
    ("`<iframe src=\"file:///etc/hosts\">`{=html}", "raw HTML"),
    ("![a local file](/etc/hosts)", "image outside diagrams/"),
    ("![a local file](diagrams/../overview.md)", "image outside diagrams/"),
    ('[styled]{style="background-image: url(http://127.0.0.1:9/beacon)"}', "attribute"),
    ('## Heading {onclick="x()"}', "attribute"),
])
def test_active_or_outside_content_stops_the_build(tmp_path, tail, refusal):
    result = _pandoc_checked(_with_overview_tail(tmp_path, tail), tmp_path)
    assert result.returncode != 0
    assert refusal in result.stderr


# pandoc's HTML writer copies raw html, html4, html5 and the slide formats into
# the page as they stand, and other writers' formats are no use in the PDF, so
# no raw block or span is allowed whatever its format.
RAW_FORMATS = ("revealjs", "s5", "slidy", "slideous", "dzslides", "html5", "html4", "HTML", "latex",
               "tex", "openxml")


@needs_pandoc
@pytest.mark.parametrize("form", ["block", "inline"])
@pytest.mark.parametrize("fmt", RAW_FORMATS)
def test_raw_content_of_any_format_stops_the_build(tmp_path, fmt, form):
    raw = f"```{{={fmt}}}\n<p>RAW-PASSED</p>\n```" if form == "block" else f"Text `<b>RAW-PASSED</b>`{{={fmt}}}."
    result = _pandoc_checked(_with_overview_tail(tmp_path, raw), tmp_path)
    assert result.returncode != 0
    assert "raw HTML is not allowed" in result.stderr
    assert f"{{={fmt}}}" in result.stderr


@needs_pandoc
def test_a_tex_command_in_the_text_stops_the_build(tmp_path):
    """pandoc's Markdown reads a backslash command as raw tex."""
    result = _pandoc_checked(_with_overview_tail(tmp_path, "A page break \\newpage here."), tmp_path)
    assert result.returncode != 0
    assert "{=tex}" in result.stderr


@needs_pandoc
def test_the_filter_stops_without_the_list_of_checked_diagrams(tmp_path):
    result = _pandoc(ARCH, None)
    assert result.returncode != 0
    assert "ARCHITECTURE_DIAGRAMS" in result.stderr


@needs_pandoc
def test_the_filter_opens_no_diagram_missing_from_the_checked_list(tmp_path):
    listing = _checked_diagrams(ARCH, tmp_path / "diagrams.list")
    listing.write_text(listing.read_text(encoding="utf-8").replace("topology.svg\n", ""), encoding="utf-8")
    result = _pandoc(ARCH, listing)
    assert result.returncode != 0
    assert "image not found: diagrams/topology.svg" in result.stderr


@needs_pandoc
@pytest.mark.parametrize("front,refusal", [
    ("---\nheader-includes: '<script>document.title = 1</script>'\n---\n\n", "raw HTML"),
    ("---\nheader-includes: '`<script>document.title = 1</script>`{=html}'\n---\n\n", "raw HTML"),
    ("---\ncss: http://127.0.0.1:9/beacon.css\n---\n\n", "metadata"),
    ("---\ninclude-before: text\n---\n\n", "metadata"),
])
def test_document_metadata_from_the_markdown_stops_the_build(tmp_path, front, refusal):
    result = _pandoc_checked(_with_overview_tail(tmp_path, "", head=front), tmp_path)
    assert result.returncode != 0
    assert refusal in result.stderr


needs_build = pytest.mark.skipif(os.name == "nt" or shutil.which("pandoc") is None,
                                 reason="needs bash and pandoc")


def _build(tmp_path: Path, root: Path = REPO) -> subprocess.CompletedProcess:
    """Run root's build-pdf.sh with a stand-in Chrome that records its arguments
    in chrome-args and keeps the page it was given as printed.html."""
    args, page = tmp_path / "chrome-args", tmp_path / "printed.html"
    chrome = tmp_path / "chrome"
    chrome.write_text(
        "#!/bin/sh\n"
        '[ "$1" = "--version" ] && { echo "Google Chrome 0.0.0.0"; exit 0; }\n'
        f'for a in "$@"; do echo "$a" >> "{args}"; case "$a" in '
        '--print-to-pdf=*) printf "%%PDF-1.4\\n" > "${a#--print-to-pdf=}" ;; '
        f'file://*) cp "${{a#file://}}" "{page}" ;; esac; done\n',
        encoding="utf-8",
    )
    chrome.chmod(chrome.stat().st_mode | stat.S_IXUSR)
    env = dict(os.environ, CHROME=str(chrome), PANDOC=shutil.which("pandoc"))
    return subprocess.run(["bash", str(root / "docs" / "pdf" / "build-pdf.sh"), "--allow-version-mismatch",
                           str(tmp_path / "out.pdf")], env=env, capture_output=True, text=True,
                          timeout=120)


def _build_tree(tmp_path: Path) -> Path:
    """A copy of the files the PDF build reads."""
    root = tmp_path / "repo"
    shutil.copytree(ARCH, root / "docs" / "architecture")
    shutil.copytree(PDF, root / "docs" / "pdf")
    shutil.copy(REPO / "pyproject.toml", root / "pyproject.toml")
    return root


@needs_build
def test_chrome_prints_with_no_route_to_the_network(tmp_path):
    result = _build(tmp_path)
    assert result.returncode == 0, result.stderr
    given = (tmp_path / "chrome-args").read_text(encoding="utf-8").splitlines()
    for flag in ("--use-mock-keychain", "--password-store=basic", "--proxy-server=127.0.0.1:9",
                 "--proxy-bypass-list=<-loopback>", "--host-resolver-rules=MAP * ~NOTFOUND"):
        assert flag in given


@needs_build
def test_the_printed_page_loads_no_file(tmp_path):
    result = _build(tmp_path)
    assert result.returncode == 0, result.stderr
    page = (tmp_path / "printed.html").read_text(encoding="utf-8")
    assert ('content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'"') in page
    assert "<link" not in page
    # pandoc's own document styles stay off, as they were beside --css.
    assert "max-width: 36em" not in page
    sources = re.findall(r'<img\s[^>]*?\bsrc="([^"]*)"', page)
    assert len(sources) == len(list((ARCH / "diagrams").glob("*.svg")))
    assert all(source.startswith("data:image/svg+xml,") for source in sources)


@needs_build
@pytest.mark.parametrize("relative", [
    "docs/architecture/diagrams/topology.svg",
    "docs/architecture/overview.md",
    "docs/pdf/architecture.css",
    "docs/pdf/architecture.lua",
])
def test_an_input_file_that_is_a_link_stops_the_build(tmp_path, relative):
    root = _build_tree(tmp_path)
    target = root / relative
    outside = tmp_path / ("outside" + target.suffix)
    shutil.copy(target, outside)
    target.unlink()
    target.symlink_to(outside)
    result = _build(tmp_path, root)
    assert result.returncode != 0
    assert f"not a regular file: {target}" in result.stderr
    assert not (tmp_path / "chrome-args").exists()


def _add_diagram_section(root: Path, name: str) -> None:
    page = root / "docs" / "architecture" / "diagrams.md"
    page.write_text(page.read_text(encoding="utf-8") + f"\n### Extra\n\n![extra](diagrams/{name}.svg)\n",
                    encoding="utf-8")


def _outside_svg(tmp_path: Path) -> Path:
    outside = tmp_path / "outside.svg"
    outside.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
                       "<text>OUTSIDE-SECRET</text></svg>", encoding="utf-8")
    return outside


@needs_build
def test_a_linked_diagram_under_a_case_variant_name_stops_the_build(tmp_path):
    """On a case-insensitive file system (macOS, Windows) the filter's open of
    diagrams/extra.svg reaches a link committed as extra.SVG."""
    root = _build_tree(tmp_path)
    link = root / "docs" / "architecture" / "diagrams" / "extra.SVG"
    link.symlink_to(_outside_svg(tmp_path))
    _add_diagram_section(root, "extra")
    result = _build(tmp_path, root)
    assert result.returncode != 0
    assert f"not a regular file: {link}" in result.stderr
    assert not (tmp_path / "chrome-args").exists()


@needs_build
@pytest.mark.parametrize("name,kind", [("notes.txt", "link"), (".hidden", "link"), ("drafts", "folder")])
def test_every_entry_of_the_diagrams_folder_must_be_a_regular_file(tmp_path, name, kind):
    root = _build_tree(tmp_path)
    entry = root / "docs" / "architecture" / "diagrams" / name
    if kind == "link":
        entry.symlink_to(_outside_svg(tmp_path))
    else:
        entry.mkdir()
    result = _build(tmp_path, root)
    assert result.returncode != 0
    assert f"not a regular file: {entry}" in result.stderr
    assert not (tmp_path / "chrome-args").exists()


@needs_build
def test_a_diagram_is_opened_only_under_its_exact_name(tmp_path):
    root = _build_tree(tmp_path)
    diagrams = root / "docs" / "architecture" / "diagrams"
    (diagrams / "topology.svg").rename(diagrams / "Topology.svg")
    result = _build(tmp_path, root)
    assert result.returncode != 0
    assert "image not found: diagrams/topology.svg" in result.stderr
    assert not (tmp_path / "chrome-args").exists()


@needs_build
@pytest.mark.parametrize("rule", [
    "body { background: url(file:///etc/hosts); }",
    "@import 'other.css';",
    "body { background-image: image-set('a.png' 1x); }",
    "body { background: URL(a.png); }",
    "</style><meta http-equiv=refresh>",
])
def test_a_stylesheet_that_can_load_or_inject_anything_stops_the_build(tmp_path, rule):
    root = _build_tree(tmp_path)
    css = root / "docs" / "pdf" / "architecture.css"
    css.write_text(css.read_text(encoding="utf-8") + "\n" + rule + "\n", encoding="utf-8")
    result = _build(tmp_path, root)
    assert result.returncode != 0
    assert "architecture.css may not" in result.stderr
    assert not (tmp_path / "chrome-args").exists()


def _checkouts(workflow: str, job: str) -> list[dict]:
    data = yaml.safe_load((REPO / ".github" / "workflows" / workflow).read_text(encoding="utf-8"))
    steps = data["jobs"][job]["steps"]
    return [step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@")]


@pytest.mark.parametrize("workflow,job", [("ci.yml", "architecture-pdf"), ("release.yml", "pdf")])
def test_the_pdf_jobs_keep_no_token_in_the_checkout(workflow, job):
    checkouts = _checkouts(workflow, job)
    assert checkouts
    for step in checkouts:
        assert (step.get("with") or {}).get("persist-credentials") is False
