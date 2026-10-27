#!/usr/bin/env bash
# Pack the Zotero 7 plugin as an .xpi (a zip of this folder). Install it in Zotero: Tools → Plugins → ⚙ → Install
# Plugin From File…
set -euo pipefail
cd "$(dirname "$0")"
out=../dist/aurora-zotero.xpi
mkdir -p ../dist
rm -f "$out"
zip -qr "$out" manifest.json bootstrap.js aurora.js prefs.js prefs.xhtml icons locale
echo "$out"
