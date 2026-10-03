# Сборка: из корня репозитория  docker compose -f docker/compose.yml up -d --build

FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
# PyTorch с CUDA 12.8 (поддерживает и RTX 50xx); без GPU работает на CPU
ARG TORCH_INDEX=https://download.pytorch.org/whl/cu128
RUN pip install torch torchvision --index-url ${TORCH_INDEX}
WORKDIR /app
COPY service/ ./service
RUN pip install "./service[ml]"
COPY --from=web /web/dist ./web
ENV CATDETECT_DATA_DIR=/data CATDETECT_WEB_DIR=/app/web
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["python", "-m", "catdetect", "--port", "8000"]
