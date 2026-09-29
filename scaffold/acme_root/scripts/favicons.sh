#!/usr/bin/env bash
# Draw the raster icons from the mark, apps/site/public/favicon.svg, into a
# public/ directory: favicon-32.png (transparent, for a browser that takes no
# SVG icon) and apple-touch-icon.png (180 by 180, on the mark's own indigo, so
# the corners a home screen rounds are never see-through). Headless Chrome
# draws them, so the pixels are the browser's reading of the same SVG. The
# output is committed; this runs again only when the mark changes.
#
#   scripts/favicons.sh apps/portal/public
#   scripts/favicons.sh apps/site/public
set -euo pipefail

out="${1:?usage: favicons.sh <public dir>}"
mark="$(cd "$(dirname "$0")/.." && pwd)/apps/site/public/favicon.svg"
chrome="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cp "$mark" "$work/favicon.svg"

draw() { # size, background, output
  printf '<html><body style="margin:0;background:%s"><img src="favicon.svg" width="%s" height="%s" style="display:block"></body></html>' \
    "$2" "$1" "$1" >"$work/page.html"
  "$chrome" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
    --default-background-color=00000000 --window-size="$1,$1" \
    --screenshot="$work/out.png" "file://$work/page.html" 2>/dev/null
  cp "$work/out.png" "$3"
}

draw 32 transparent "$out/favicon-32.png"
draw 180 "#5250d6" "$out/apple-touch-icon.png"
echo "wrote $out/favicon-32.png and $out/apple-touch-icon.png"
