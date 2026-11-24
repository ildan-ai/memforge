#!/usr/bin/env bash
# Builds the architecture PDF from the repository's own sources: the diagrams
# page, the architecture overview and the threat model in docs/architecture,
# with the rendered SVG diagrams. pandoc writes one HTML file and headless
# Chrome prints it. Nothing is fetched from the network and no file outside
# the inputs is read: every input, and every entry of the diagrams folder,
# must be a regular file, not a link;
# architecture.lua refuses raw content, images other than the diagrams,
# attributes and metadata in the Markdown, and writes the diagrams into the
# page; the stylesheet may load nothing; the page carries a content security
# policy that allows only those inline images and styles; and Chrome runs with
# no route to the network.
#
# The toolchain is pinned in toolchain.conf. A missing tool, or a version other
# than the pinned one, stops the build, so a reviewer who rebuilds with the
# pinned tools gets the same document the release attached.
# --allow-version-mismatch turns a version difference into a warning, for a
# local preview only; the workflows never pass it.
#
# usage: build-pdf.sh [--allow-version-mismatch] <output.pdf>
# environment: PANDOC and CHROME name the executables (default: pandoc and
# chrome on PATH).
set -euo pipefail

usage() {
  echo "usage: build-pdf.sh [--allow-version-mismatch] <output.pdf>" >&2
  exit 2
}

die() {
  echo "build-pdf: $*" >&2
  exit 1
}

allow_mismatch=0
if [ "${1:-}" = "--allow-version-mismatch" ]; then
  allow_mismatch=1
  shift
fi
[ "$#" -eq 1 ] || usage
case "$1" in
  *.pdf) ;;
  *) usage ;;
esac

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../.." && pwd)
arch="$root/docs/architecture"
out_dir=$(cd "$(dirname "$1")" 2>/dev/null && pwd) || die "output folder does not exist: $(dirname "$1")"
out="$out_dir/$(basename "$1")"

# Every folder and file the build reads must be the real thing, not a link: a
# link committed under an allowed name would make the build read, and the PDF
# print, a file from outside the repository.
for input in "$root/docs" "$arch" "$arch/diagrams" "$here"; do
  { [ -d "$input" ] && [ ! -L "$input" ]; } || die "not a regular folder: $input"
done
for input in "$here/architecture.lua" "$here/architecture.css" "$here/toolchain.conf" \
  "$root/pyproject.toml" "$arch/diagrams.md" "$arch/overview.md" "$arch/threat-model.md"; do
  { [ -f "$input" ] && [ ! -L "$input" ]; } || die "not a regular file: $input"
done
# Every entry of the diagrams folder is checked, whatever its name: on a
# case-insensitive file system the filter's open of diagrams/x.svg reaches an
# entry named x.SVG, which a pattern for *.svg would pass over. The filter
# opens a diagram only under a name on the list kept here, spelled exactly.
diagrams=()
shopt -s nullglob dotglob
for input in "$arch"/diagrams/*; do
  { [ -f "$input" ] && [ ! -L "$input" ]; } || die "not a regular file: $input"
  name=$(basename "$input")
  if [[ "$name" =~ ^[A-Za-z0-9_-]+\.svg$ ]]; then
    diagrams+=("$name")
  fi
done
shopt -u nullglob dotglob
# The stylesheet is pasted into the page, so it may load nothing (url(),
# image-set(), @import, or a backslash escape that spells one) and may not
# close its style element.
if grep -Eiq 'url\(|image-set\(|@import|\\|<' "$here/architecture.css"; then
  die "architecture.css may not contain url(, image-set(, @import, a backslash or <"
fi

pin() {
  local value
  value=$(sed -n "s/^$1=//p" "$here/toolchain.conf")
  [ -n "$value" ] || die "toolchain.conf has no $1"
  printf '%s\n' "$value"
}

find_tool() {
  local name="$1" variable="$2" path="$3"
  if [ -z "$path" ]; then
    path=$(command -v "$name" 2>/dev/null) || die "$name not found; install it or set $variable to its path"
  fi
  [ -x "$path" ] || die "$name is not executable: $path"
  printf '%s\n' "$path"
}

check_version() {
  local name="$1" found="$2" want="$3"
  if [ "$found" = "$want" ]; then
    return
  fi
  if [ "$allow_mismatch" -eq 1 ]; then
    echo "build-pdf: WARNING: $name $found is not the pinned $want; this PDF is a local preview" >&2
    return
  fi
  die "$name $found is not the pinned version $want (see docs/pdf/toolchain.conf)"
}

pandoc_bin=$(find_tool pandoc PANDOC "${PANDOC:-}")
chrome_bin=$(find_tool chrome CHROME "${CHROME:-}")
pandoc_version=$("$pandoc_bin" --version | sed -n '1s/^pandoc //p')
chrome_version=$("$chrome_bin" --version | awk '{ print $NF }')
[ -n "$pandoc_version" ] || die "cannot read the pandoc version from $pandoc_bin"
[ -n "$chrome_version" ] || die "cannot read the Chrome version from $chrome_bin"
check_version pandoc "$pandoc_version" "$(pin PANDOC_VERSION)"
check_version chrome "$chrome_version" "$(pin CHROME_VERSION)"
echo "toolchain: pandoc $pandoc_version ($pandoc_bin)"
echo "toolchain: chrome $chrome_version ($chrome_bin)"

version=$(sed -n 's/^version = "\(.*\)"$/\1/p' "$root/pyproject.toml" | head -n 1)
[ -n "$version" ] || die "no version in pyproject.toml"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
# The page loads no file at all: the filter writes each diagram into the page
# as a data: URL and the stylesheet is pasted in, so the policy allows data:
# images and inline styles and nothing else (no file:, which a page cannot
# narrow to one folder, and no script, frame, object, font or connection).
{
  printf '%s\n' "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'\">"
  printf '<style>\n'
  cat "$here/architecture.css"
  printf '</style>\n'
} >"$work/head.html"
printf '%s\n' ${diagrams[@]+"${diagrams[@]}"} >"$work/diagrams.list"

# pandoc runs from docs/architecture so the relative links and image paths in
# the Markdown resolve as they do on GitHub. pandoc's own document styles stay
# off, as they were when the stylesheet was linked with --css (an empty value
# is false to the template; the word false would be true).
(
  cd "$arch"
  ARCHITECTURE_DIAGRAMS="$work/diagrams.list" "$pandoc_bin" --from markdown+gfm_auto_identifiers --to html5 --standalone \
    --lua-filter "$here/architecture.lua" \
    --variable document-css= \
    --include-in-header "$work/head.html" \
    --metadata title="MemForge $version" \
    --metadata subtitle="Architecture diagrams and description" \
    --output "$work/architecture.html" \
    diagrams.md overview.md threat-model.md
)

# A print job must not touch the user's keychain or keyring:
# --use-mock-keychain keeps Chrome off the macOS keychain and
# --password-store=basic keeps it off the Linux keyring.
# Nor may it reach the network: every request goes to a proxy on a closed
# local port (--proxy-server), loopback included (--proxy-bypass-list=<-loopback>
# removes Chrome's implicit bypass for it), and no host name resolves
# (--host-resolver-rules). Chrome has no flag that confines file: reads to one
# folder; the page policy allows no file: load at all.
"$chrome_bin" --headless --disable-gpu --no-first-run --no-default-browser-check \
  --use-mock-keychain --password-store=basic \
  --proxy-server=127.0.0.1:9 --proxy-bypass-list='<-loopback>' \
  --host-resolver-rules='MAP * ~NOTFOUND' \
  --disable-extensions --disable-sync --disable-background-networking \
  --disable-component-update --user-data-dir="$work/profile" \
  --no-pdf-header-footer --generate-pdf-document-outline \
  --print-to-pdf="$work/architecture.pdf" "file://$work/architecture.html" 2>"$work/chrome.log" \
  || { cat "$work/chrome.log" >&2; die "Chrome failed to print the PDF"; }

[ "$(head -c 5 "$work/architecture.pdf" 2>/dev/null)" = "%PDF-" ] \
  || { cat "$work/chrome.log" >&2; die "Chrome wrote no PDF"; }
mv "$work/architecture.pdf" "$out"
echo "wrote $out"
