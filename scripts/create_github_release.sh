#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${REPO_DIR}/dist"

VERSION="$(tr -d '[:space:]' <"${REPO_DIR}/VERSION")"
TAG="v${VERSION}"
REPO_SLUG="AngelBePro/scrobload"

if [[ -n "${1:-}" ]]; then
  REPO_SLUG="$1"
fi

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "GITHUB_TOKEN is required (classic token or fine-grained token with contents:write)." >&2
  exit 1
fi

DEB_FILE="${DIST_DIR}/scrobload_${VERSION}_all.deb"
ARCH_FILE="${DIST_DIR}/scrobload-${VERSION}-1-any.pkg.tar.zst"

for f in "$DEB_FILE" "$ARCH_FILE"; do
  if [[ ! -f "$f" ]]; then
    echo "Missing release asset: $f" >&2
    echo "Run ./scripts/release_packages.sh first." >&2
    exit 1
  fi
done

git -C "$REPO_DIR" tag -f "$TAG"
git -C "$REPO_DIR" push origin "$TAG" --force

RELEASE_PAYLOAD="$(cat <<JSON
{
  "tag_name": "${TAG}",
  "name": "Scrobload ${TAG}",
  "body": "Automated release for ${TAG}",
  "draft": false,
  "prerelease": false
}
JSON
)"

RELEASE_RESP="$(curl -sS -X POST \
  -H "Authorization: token ${GITHUB_TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/${REPO_SLUG}/releases" \
  -d "$RELEASE_PAYLOAD")"

UPLOAD_URL="$(printf '%s' "$RELEASE_RESP" | sed -n 's/.*"upload_url": *"\([^"]*\){?name,label}?".*/\1/p')"
HTML_URL="$(printf '%s' "$RELEASE_RESP" | sed -n 's/.*"html_url": *"\([^"]*\)".*/\1/p')"

if [[ -z "$UPLOAD_URL" ]]; then
  echo "Failed creating release. API response:" >&2
  echo "$RELEASE_RESP" >&2
  exit 1
fi

upload_asset() {
  local file="$1"
  local name
  name="$(basename "$file")"
  curl -sS -X POST \
    -H "Authorization: token ${GITHUB_TOKEN}" \
    -H "Content-Type: application/octet-stream" \
    --data-binary @"$file" \
    "${UPLOAD_URL}?name=${name}" >/dev/null
  echo "Uploaded: ${name}"
}

upload_asset "$DEB_FILE"
upload_asset "$ARCH_FILE"

echo "Release published: ${HTML_URL}"
