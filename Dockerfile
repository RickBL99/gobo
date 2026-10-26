# Gobo: one image with the app and ffmpeg.
#
#   docker build -t gobo .
#   docker run -p 8000:8000 --env-file .env -v gobo-data:/data gobo
FROM python:3.12-slim-bookworm

# ffmpeg stitches clips, grabs frames and makes posters (Debian's build has x264 + AAC)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STUDIO_DIR=/data \
    STUDIO_ENV_FILE=""

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY studio ./studio

RUN useradd --create-home --uid 10001 studio && mkdir -p /data && chown studio /data
USER studio
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

# --forwarded-allow-ips: behind a host's front end (Azure App Service) the client address
# comes in X-Forwarded-For; without it every visitor is the front end, and the sign-in
# throttle would lock everyone out together.
CMD ["uvicorn", "studio.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
