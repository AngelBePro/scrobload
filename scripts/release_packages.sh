#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

"${SCRIPT_DIR}/build_deb.sh"
"${SCRIPT_DIR}/build_arch_pkg.sh"

echo "Release artifacts available in ./dist"
