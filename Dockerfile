FROM python:3.11-slim

ENV PORT=8191 \
    HEADLESS=false \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data/noflare \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_DYNAMIC_VERSIONING_BYPASS=1.2.1 \
    DISPLAY=:99

# Combine apt installations to reduce layers and install dumb-init for PID 1 handling
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    wget \
    gnupg \
    curl \
    fonts-liberation \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxrandr2 \
    libxss1 \
    libasound2 \
    xvfb \
    xauth \
    unzip \
    dumb-init \
    && wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && (dpkg -i google-chrome-stable_current_amd64.deb || apt-get install -y --no-install-recommends -f) \
    && rm google-chrome-stable_current_amd64.deb \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r appuser && useradd -r -g appuser -ms /bin/bash appuser
WORKDIR /app

COPY src/ /app/src
COPY pyproject.toml README.md LICENSE /app/

RUN pip install -U pip setuptools wheel --root-user-action=ignore \
    && pip install --no-cache-dir -e . --root-user-action=ignore

RUN mkdir -p /data/noflare && chown -R appuser:appuser /data/noflare /app
USER appuser
EXPOSE 8191

VOLUME ["/data/noflare"]

ENTRYPOINT ["dumb-init", "--"]

CMD ["sh", "-c", "if [ \"$HEADLESS\" = \"false\" ]; then exec xvfb-run -a -s \"-screen 0 1280x1024x24\" python -m noflare; else exec python -m noflare; fi"]
