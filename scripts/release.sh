#!/usr/bin/env bash
# Cut a GitHub release from master for the version in the extension manifest.
#
#   scripts/release.sh "<notes>"
#
# The PR that ships a fix bumps `version` in addon/spyrite_tile/blender_manifest.toml
# (scripts/bump_version.sh); after it is merged, run this on an up-to-date master. It
# builds the extension zip with Blender's own builder, validates it, tags v<version>,
# pushes the tag and creates the release with the zip attached. Refuses a dirty tree,
# a master behind origin, or an existing tag.
set -euo pipefail
cd "$(dirname "$0")/.."
BLENDER=${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}
NOTES=${1:?usage: scripts/release.sh "<release notes>"}
MANIFEST=addon/spyrite_tile/blender_manifest.toml

[ "$(git branch --show-current)" = master ] || { echo "not on master" >&2; exit 1; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "dirty tree" >&2; exit 1; }
git fetch -q origin
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/master)" ] || { echo "master != origin/master" >&2; exit 1; }

VERSION=$(sed -nE 's/^version = "([^"]+)"/\1/p' "$MANIFEST")
TAG="v$VERSION"
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && { echo "tag $TAG exists" >&2; exit 1; }

OUT=$(mktemp -d)
"$BLENDER" --command extension validate "addon/spyrite_tile"
"$BLENDER" --command extension build --source-dir addon/spyrite_tile --output-dir "$OUT"
ZIP=$(ls "$OUT"/*.zip)
"$BLENDER" --command extension validate "$ZIP"
LISTING=$(unzip -l "$ZIP")
grep -q "api.py" <<<"$LISTING" || { echo "zip lacks api.py" >&2; exit 1; }
if grep -q "__pycache__" <<<"$LISTING"; then echo "zip contains __pycache__" >&2; exit 1; fi

git tag -a "$TAG" -m "Spyrite Tile $VERSION"
git push -q origin "$TAG"
gh release create "$TAG" "$ZIP" -R ladvien/spyrit_tile --title "Spyrite Tile $VERSION" --notes "$NOTES"
echo "RELEASED $TAG $(gh release view "$TAG" -R ladvien/spyrit_tile --json url --jq .url)"
