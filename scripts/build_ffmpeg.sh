#!/usr/bin/env bash
# Build a minimal LGPL FFmpeg into ./ffmpeg-bin (docs/04 §8: distribution must
# not use GPL builds). Works on macOS, Linux and Windows-msys2; CI
# (release.yml) runs this before packaging.
#
# lame 3.100 is compiled FROM SOURCE and linked statically: brew/apt lame
# static archives vary (newer brew lame needs mpg123 .a that bottles strip),
# source-built lame is self-contained (bundled mpglib, zero external deps).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FFMPEG_VERSION="8.1"
LAME_VERSION="3.100"
FFMPEG_SRC="/tmp/ffmpeg-${FFMPEG_VERSION}"
LAME_SRC="/tmp/lame-${LAME_VERSION}"
LAME_PREFIX="/tmp/lame-install"

mkdir -p "${ROOT}/ffmpeg-bin"

# -- lame (LGPL, static) ------------------------------------------------------
if [[ ! -f "${LAME_PREFIX}/lib/libmp3lame.a" ]]; then
  if [[ ! -d "${LAME_SRC}" ]]; then
    curl -sL --retry 3 -o /tmp/lame.tar.gz \
      "https://downloads.sourceforge.net/project/lame/lame/${LAME_VERSION}/lame-${LAME_VERSION}.tar.gz"
    mkdir -p "${LAME_SRC}"
    tar xzf /tmp/lame.tar.gz -C "${LAME_SRC}" --strip-components=1
  fi
  cd "${LAME_SRC}"
  ./configure --prefix="${LAME_PREFIX}" --disable-shared --enable-static \
    --disable-frontend --disable-dependency-tracking
  make -j"$(sysctl -n hw.ncpu 2>/dev/null || nproc)"
  make install
fi

# -- ffmpeg (LGPL, static lame) ------------------------------------------------
if [[ ! -d "${FFMPEG_SRC}" ]]; then
  curl -sL --retry 3 -o "/tmp/ffmpeg-n${FFMPEG_VERSION}.tar.gz" \
    "https://github.com/FFmpeg/FFmpeg/archive/refs/tags/n${FFMPEG_VERSION}.tar.gz"
  mkdir -p "${FFMPEG_SRC}"
  tar xzf "/tmp/ffmpeg-n${FFMPEG_VERSION}.tar.gz" -C "${FFMPEG_SRC}" --strip-components=1
fi

cd "${FFMPEG_SRC}"
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
# sed (not python3) for msys2 portability.
sed -i.bak "s|-lmp3lame|${LAME_PREFIX}/lib/libmp3lame.a|g" ffbuild/config.mak
echo "config.mak: lame linked statically (source-built)"

# Default target builds every enabled program — target-name differences
# across platforms (Windows emits .exe) stay out of the invocation.
make -j"$(sysctl -n hw.ncpu 2>/dev/null || nproc)"

otool -L ffmpeg 2>/dev/null | grep -q lame && echo "WARNING: ffmpeg still links lame dynamically" >&2 || true
ldd ffmpeg 2>/dev/null | grep -q lame && echo "WARNING: ffmpeg still links lame dynamically" >&2 || true

for name in ffmpeg ffprobe; do
  for candidate in "${name}.exe" "${name}"; do
    if [[ -f "${candidate}" ]]; then
      cp -f "${candidate}" "${ROOT}/ffmpeg-bin/${candidate}"
      break
    fi
  done
done
cp -f COPYING.LGPLv2.1 "${ROOT}/ffmpeg-bin/" 2>/dev/null || true
cp -f "${LAME_SRC}/COPYING" "${ROOT}/ffmpeg-bin/COPYING.lame" 2>/dev/null || true
ls -la "${ROOT}/ffmpeg-bin/"
echo "LGPL ffmpeg ${FFMPEG_VERSION} -> ${ROOT}/ffmpeg-bin/"
