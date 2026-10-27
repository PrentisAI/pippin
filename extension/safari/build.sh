#!/bin/sh
# The Safari version of the extension: the same code, wrapped in a macOS app by Xcode's converter.
# Run on a Mac with Xcode, from the repository root:  sh extension/safari/build.sh
set -e
here="$(cd "$(dirname "$0")" && pwd)"
out="$here/../../build/AURORA-Safari"     # outside the extension folder, which is copied into it
rm -rf "$out"
xcrun safari-web-extension-converter "$here/.." --project-location "$out" --app-name "AURORA" \
  --bundle-identifier ai.prentis.aurora.safari --macos-only --copy-resources --no-open --force
echo "Open $out/AURORA/AURORA.xcodeproj in Xcode, build and run it once, then turn the extension on in"
echo "Safari → Settings → Extensions (for a build you made yourself: Develop → Allow Unsigned Extensions)."
