# GrubTrack: the web app, the CLI and the agent skill in one image. Data lives in /data.
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    GT_DATA=/data GT_STATIC=/app/frontend/dist TZ=UTC
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY skill/ skill/
COPY tools/ tools/
COPY --from=web /web/dist frontend/dist
RUN printf '#!/bin/sh\nexec python3 /app/skill/food-inventory/scripts/fooddb_sqlite.py "$@"\n' > /usr/local/bin/grubtrack \
    && chmod +x /usr/local/bin/grubtrack \
    && useradd --uid 1000 --create-home grubtrack && mkdir -p /data && chown grubtrack /data
USER grubtrack
VOLUME /data
EXPOSE 8888
WORKDIR /app/backend
HEALTHCHECK --interval=60s --timeout=5s CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8888/api/build', timeout=4)"
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8888"]
