#!/usr/bin/env bash
# Publish a Gear release: push the branch and upload out/make/* to GitHub Releases.
# Usage: ./release.sh 1.0.0   (after `npm run make`; needs `gh auth login`)
set -euo pipefail
VERSION=${1:?version, e.g. 1.0.0}
REPO=${REPO:-wildmansignal/gear}
cd "$(dirname "$0")"
if ! gh repo view "$REPO" >/dev/null 2>&1; then
  gh repo create "$REPO" --public --description "Build your own software with AI, on your own machine."
fi
git remote get-url gear-origin >/dev/null 2>&1 || git remote add gear-origin "https://github.com/$REPO.git"
git push gear-origin HEAD:gear
# Upstream Dyad already used plain vX.Y.Z tags, so Gear tags are prefixed.
TAG="gear-v$VERSION"
git tag -f "$TAG" HEAD
git push -f gear-origin "refs/tags/$TAG"
ASSETS=$(ls out/make/*.dmg out/make/zip/darwin/*/*.zip 2>/dev/null || true)
[ -n "$ASSETS" ] || { echo "no installers in out/make; run npm run make first"; exit 1; }
gh release create "$TAG" $ASSETS --repo "$REPO" --verify-tag --title "Gear $VERSION" --notes "Gear $VERSION. Mac (Apple Silicon). First open: right-click → Open." --latest
echo "download: https://github.com/$REPO/releases/latest/download/Gear-$VERSION-arm64.dmg"
