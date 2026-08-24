# NoFlare - lightweight Cloudflare bypass service

Once FlareSolverr and Byparr stopped working for me, the snake convinced me write my own!

NoFlare is a small, async-first Python service that automates solving Cloudflare JavaScript challenges and Turnstile checks. It is built as a drop-in, modern alternative to projects like FlareSolverr and Byparr, using an undetected Chromium backend via `nodriver` and an async FastAPI HTTP API.

### Why NoFlare?
- Minimal, async-first design for handling many concurrent requests.
- Designed to be a drop-in replacement for FlareSolverr/Byparr with a single HTTP endpoint.
- Packaged for PyPI and Docker for easy deployment.

### Features
- Single POST `/v1` endpoint that accepts a JSON payload with `url` and `timeout`.
- Returns page HTML, cookies (including `cf_clearance`/Turnstile solutions), and user-agent.
- Configurable concurrency, headless mode, language, user data directory, and proxy via environment variables.

### Quickstart - PyPI
1. Install from PyPI:

   `pip install noflare`

2. Run the service:

   `python -m noflare`

3. Example request with `httpie`:
   ```
   http ://localhost:8191/v1 url="https://1337x.to"
   ```

### Environment variables
See `.env.example` for a full list. Notable variables:
- `PORT` - HTTP port (default: `8191`)
- `MAX_TABS` - Max concurrent Chromium tabs (default: `10`)
- `HEADLESS` - `true`/`false` to run Chromium headless (default: `false`)
- `DATA_DIR` - Path to persistent user-data directory (default: `~/.noflare/data`)
- `PROXY_SERVER` - Proxy server URL (e.g. `http://1.2.3.4:3128`)
- `BROWSER_LOCALE` - Browser language/locale (default: `en-US`)

### Docker
Build and run locally with Docker:

```bash
docker build -t noflare:local .
docker run --rm -p 8191:8191 \
  -v /path/to/data:/data/noflare \
  --env-file .env \
  noflare:local
```

### Docker Compose
Use the provided `compose.yml` for local development. Copy `.env.example` to `.env` and edit values before launching.

### Development
- Python 3.11+
- Install dev deps (project uses `pyproject.toml`):

   ```bash
   pip install -e .[dev]
   ```

   Run with reload for development:

   ```bash
   python -m noflare --debug
   ```

## Contributing
- Contributions, PRs and issues are welcome. Please open issues for bugs or feature requests.
- Follow the repository `CODEOWNERS` and maintainers' guidelines where provided.

## License
MIT - see `LICENSE` for details.

## Credits & Dependencies
- FastAPI - high-performance async API framework
- nodriver - Chromium automation backend (undetected/stealth)
- uvicorn - ASGI server
