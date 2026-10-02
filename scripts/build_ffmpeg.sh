#!/usr/bin/env bash
# Build a minimal LGPL FFmpeg into ./ffmpeg-bin (docs/04 §8: distribution must
# not use GPL builds). Works on macOS and Linux; CI (release.yml) runs this
# before packaging. Windows CI builds it via msys2 (see release.yml).
#
# libmp3lame (LGPL) is linked STATICALLY so the binaries carry no external
# dylib dependencies. Requires lame dev headers (brew install lame / apt
# libmp3lame-dev).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="8.1"
SRC="/tmp/ffmpeg-${VERSION}"

LAME_PREFIX="$(dirname "$(dirname "$(command -v lame >/dev/null 2>&1 && command -v lame)" 2>/dev/null || echo /opt/homebrew/bin/lame)")"
[[ -d "${LAME_PREFIX}" ]] || LAME_PREFIX="/usr"

if [[ ! -x "${ROOT}/ffmpeg-bin/ffmpeg" ]] || [[ -n "${FORCE:-}" ]]; then
  mkdir -p "${ROOT}/ffmpeg-bin"
fi

if [[ ! -d "${SRC}" ]]; then
  curl -sL --retry 3 -o "/tmp/ffmpeg-n${VERSION}.tar.gz" \
    "https://github.com/FFmpeg/FFmpeg/archive/refs/tags/n${VERSION}.tar.gz"
  mkdir -p "${SRC}"
  tar xzf "/tmp/ffmpeg-n${VERSION}.tar.gz" -C "${SRC}" --strip-components=1
fi

cd "${SRC}"
# --disable-x86asm: CI runners lack nasm; portable build with negligible cost.
./configure \
  --disable-doc --disable-debug --disable-autodetect --disable-network \
  --disable-x86asm \
  --enable-protocol=file \
  --enable-demuxer=mov,matroska,webm,mp3,wav,aac \
  --enable-muxer=mp3,null,adts \
  --enable-decoder=h264,hevc,vp9,mjpeg,aac,mp3,flac,pcm_s16le \
  --enable-encoder=mjpeg,aac,libmp3lame \
  --enable-swscale --enable-libmp3lame \
  --extra-cflags="-I${LAME_PREFIX}/include" \
  --extra-ldflags="-L${LAME_PREFIX}/lib"

# Static-link lame: replace -lmp3lame with the archive path before linking.
# sed (not python3) for portability inside msys2 on Windows CI.
sed -i.bak "s|-lmp3lame|${LAME_PREFIX}/lib/libmp3lame.a|g" ffbuild/config.mak
echo "config.mak: lame linked statically"

make -j"$(sysctl -n hw.ncpu 2>/dev/null || nproc)" ffmpeg ffprobe
rm -f ffmpeg ffprobe   # force relink AFTER the static patch (make caches ffmpeg_g)
make -j2 ffmpeg ffprobe

otool -L ffmpeg 2>/dev/null | grep -q lame && echo "WARNING: ffmpeg still links lame dynamically" >&2 || true
ldd ffmpeg 2>/dev/null | grep -q lame && echo "WARNING: ffmpeg still links lame dynamically" >&2 || true

cp -f ffmpeg ffprobe "${ROOT}/ffmpeg-bin/"
cp -f COPYING.LGPLv2.1 "${ROOT}/ffmpeg-bin/" 2>/dev/null || true
echo "LGPL ffmpeg ${VERSION} -> ${ROOT}/ffmpeg-bin/"
