FROM apache/tika:3.2.3.0-full@sha256:21d8052de04e491ccf66e8680ade4da6f3d453a56d59f740b4167e54167219b7

USER root

# 中文 OCR 模型与字体固定在独立服务镜像中，Worker 保持轻量且可替换为其他 OCR Adapter。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-noto-cjk=1:20240730+repack1-1 \
        tesseract-ocr-chi-sim=1:4.1.0-2 \
    && rm -rf /var/lib/apt/lists/*

USER 35002:35002
