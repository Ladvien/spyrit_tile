#!/usr/bin/env bash
# Increment the patch version in the extension manifest; prints the new version.
set -euo pipefail
cd "$(dirname "$0")/.."
MANIFEST=addon/spyrite_tile/blender_manifest.toml
OLD=$(sed -nE 's/^version = "([^"]+)"/\1/p' "$MANIFEST")
NEW=$(echo "$OLD" | awk -F. '{printf "%d.%d.%d", $1, $2, $3 + 1}')
sed -i '' -E "s/^version = \"$OLD\"/version = \"$NEW\"/" "$MANIFEST"
grep -q "^version = \"$NEW\"" "$MANIFEST"
echo "$NEW"
