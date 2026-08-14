#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 固定镜像、字体、画布与文字，确保中文 OCR 样本只含可复现的合成数据。
docker run --rm \
  --user "$(id -u):$(id -g)" \
  --entrypoint convert \
  -v "$ROOT_DIR/generated:/out" \
  ai-platform-tika:3.2.3-chi-sim \
  -background white \
  -fill black \
  -font Noto-Sans-CJK-SC \
  -pointsize 128 \
  -size 960x300 \
  -gravity center \
  "label:中国" \
  /out/synthetic-chinese-ocr.png
