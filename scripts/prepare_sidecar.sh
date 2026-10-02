#!/usr/bin/env bash
# Place the sidecar executable for Tauri externalBin:
#   dev     -> wrapper script that runs the uv-managed python (fast loop)
#   release -> PyInstaller onefile binary (docs/04 §8)
# The file must carry the target-triple suffix in src-tauri/binaries/.
set -euo pipefail

MODE="${1:-dev}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TRIPLE="$("${ROOT}/src-tauri/binaries/rust-triple.sh" 2>/dev/null || rustc -vV | sed -n 's/^host: //p')"
BIN_DIR="${ROOT}/src-tauri/binaries"
TARGET="${BIN_DIR}/video-tagger-sidecar-${TRIPLE}"
mkdir -p "${BIN_DIR}"

if [[ "${MODE}" == "dev" ]]; then
  cat > "${TARGET}" <<EOF
#!/usr/bin/env bash
# DEV wrapper — real binary comes from PyInstaller in release builds.
cd "${ROOT}"
exec uv run python -m sidecar.api.run "\$@"
EOF
  chmod +x "${TARGET}"
  echo "sidecar (dev wrapper) -> ${TARGET}"
  exit 0
fi

# release: PyInstaller onefile
if [[ ! -x "${ROOT}/ffmpeg-bin/ffmpeg" ]]; then
  echo "ffmpeg-bin/ffmpeg missing — run scripts/build_ffmpeg.sh first (docs/04 §8 LGPL)" >&2
  exit 1
fi
cd "${ROOT}"
uv run pyinstaller \
  --onefile \
  --name video-tagger-sidecar \
  --distpath "${BIN_DIR}/dist" \
  --workpath "${BIN_DIR}/build" \
  --specpath "${BIN_DIR}" \
  --add-data "${ROOT}/sidecar/taxonomy/default.json:sidecar/taxonomy" \
  --add-data "${ROOT}/ffmpeg-bin:ffmpeg-bin" \
  --collect-submodules uvicorn \
  sidecar/api/run.py

# Windows: PyInstaller appends .exe and Tauri resolves externalBin as
# name-<triple>.exe — keep the suffix there, none on macOS/Linux.
if [[ "${TRIPLE}" == *windows* ]]; then
  mv "${BIN_DIR}/dist/video-tagger-sidecar.exe" "${TARGET}.exe"
else
  mv "${BIN_DIR}/dist/video-tagger-sidecar" "${TARGET}"
fi
echo "sidecar (pyinstaller) -> ${TARGET}"
