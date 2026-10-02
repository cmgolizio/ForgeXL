#!/usr/bin/env bash
# Finder entry point after the one-time documented setup and production build.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
if ! command -v node >/dev/null || ! command -v npm >/dev/null; then
  echo "Node.js/npm is not available in this window. Complete README.md setup first."
  read -r -p "Press Return to close. " || true
  exit 1
fi
npm run start:open
status=$?
if [ "$status" -ne 0 ] && [ -t 0 ]; then
  read -r -p "Startup failed. Review the instructions above; press Return to close. " || true
fi
exit "$status"
