#!/bin/sh
set -eu

# Package the exact same Python sources Android stages in android/app/build.gradle.kts.
# State remains in Application Support/MaxAlpha after the app copies this bundle once.
project_root="$(cd "${PROJECT_DIR}/../.." && pwd)"
source_agent="${project_root}/agent"
destination="${TARGET_BUILD_DIR}/${UNLOCALIZED_RESOURCES_FOLDER_PATH}/python"

if [ ! -d "${source_agent}" ]; then
  echo "error: Max Alpha agent source was not found at ${source_agent}" >&2
  exit 1
fi

rm -rf "${destination}"
mkdir -p "${destination}"
rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude 'mobile_api.py' "${source_agent}/" "${destination}/agent/"
cp "${project_root}/symbol_map.json" "${destination}/agent/symbol_map.json"
cp "${project_root}/max_alpha_mobile/assets/dashboard_v5_bot2.html" "${destination}/agent/dashboard_v5_bot2.html"
