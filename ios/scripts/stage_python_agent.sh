#!/bin/sh
set -eu

# Package the exact same Python sources Android stages in android/app/build.gradle.kts.
# State remains in Application Support/MaxAlpha after the app copies this bundle once.
#
# NOTE: The agent/ directory lives outside the Flutter project root and is only
# present in a full local checkout.  On CI (e.g. Codemagic) it will be absent.
# That is intentional: MaxAlphaPythonBridge.mm already guards all Python calls
# behind #if MAX_ALPHA_PYTHON, so the app builds and runs without the runtime —
# bot calls simply return PYTHON_RUNTIME_MISSING, matching the graceful-
# degradation design.  We therefore warn and exit cleanly instead of failing.
project_root="$(cd "${PROJECT_DIR}/../.." && pwd)"
source_agent="${project_root}/agent"
destination="${TARGET_BUILD_DIR}/${UNLOCALIZED_RESOURCES_FOLDER_PATH}/python"

if [ ! -d "${source_agent}" ]; then
  echo "warning: Max Alpha agent source was not found at ${source_agent} — skipping Python bundle (bot calls will return PYTHON_RUNTIME_MISSING)." >&2
  exit 0
fi

rm -rf "${destination}"
mkdir -p "${destination}"
rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude 'mobile_api.py' "${source_agent}/" "${destination}/agent/"
cp "${project_root}/symbol_map.json" "${destination}/agent/symbol_map.json"
cp "${project_root}/max_alpha_mobile/assets/dashboard_v5_bot2.html" "${destination}/agent/dashboard_v5_bot2.html"
