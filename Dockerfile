FROM python:3.11-slim

ENV PORT=8191 \
    HEADLESS=true \
    DATA_DIR=/data/noflare \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_DYNAMIC_VERSIONING_BYPASS=0.0.0-docker

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
    unzip \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update && apt-get install -y --no-install-recommends wget gnupg \
    && wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && (dpkg -i google-chrome-stable_current_amd64.deb || apt-get install -y --no-install-recommends -f) \
    && rm google-chrome-stable_current_amd64.deb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY src/ /app/src
COPY pyproject.toml README.md LICENSE /app/

RUN pip install --upgrade pip setuptools wheel \
    && pip install --no-cache-dir .

EXPOSE 8191

VOLUME ["/data/noflare"]

CMD ["python", "-m", "noflare"]
