FROM python:3.11-slim

ENV PORT=8191 \
    HEADLESS=false \
    NO_SANDBOX=true \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data/noflare \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_DYNAMIC_VERSIONING_BYPASS=1.2.3 \
    DISPLAY=:99

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    wget \
    curl \
    xvfb \
    xauth \
    unzip \
    gnupg \
    libxi6 \
    libxss1 \
    libnss3 \
    libcups2 \
    dumb-init \
    libxrandr2 \
    libasound2* \
    libatk1.0-0 \
    libxkbcommon0 \
    libxcomposite1 \
    fonts-liberation \
    libatk-bridge2.0-0

RUN wget -q -O - https://google.com | apt-key add - \
    && echo "deb [arch=amd64] http://google.com stable main" >> /etc/apt/sources.list.d/google.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

#    && wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
#    && (dpkg -i google-chrome-stable_current_amd64.deb || apt-get install -y --no-install-recommends -f) \
#    && rm google-chrome-stable_current_amd64.deb \
#    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY src/ /app/src
COPY pyproject.toml README.md LICENSE /app/

RUN pip install -U pip setuptools wheel --root-user-action=ignore \
    && pip install --no-cache-dir -e . --root-user-action=ignore

EXPOSE 8191

VOLUME ["/data/noflare"]

ENTRYPOINT ["dumb-init", "--"]

CMD ["sh", "-c", "if [ \"$HEADLESS\" = \"false\" ]; then exec xvfb-run -a -s \"-screen 0 1280x768x24\" python -m noflare; else exec python -m noflare; fi"]
