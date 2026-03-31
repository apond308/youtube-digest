# YouTube Digest

Automatically summarize new YouTube videos from your favorite channels and deliver them as email digests. Configure the channels you follow, point it at any OpenAI-compatible LLM, and get daily email summaries so you can absorb video content in 5 minutes of reading instead of 20 minutes of watching.

## Quick Start

```bash
git clone https://github.com/apond308/youtube-digest.git
cd youtube-digest
./install.sh
```

The installer walks you through everything interactively:

1. Creates a Python virtualenv and installs dependencies
2. Prompts for your API keys (OpenAI-compatible LLM, Gmail)
3. Sets up your channel list and subscriber config
4. Optionally installs as a systemd service and/or Cloudflare tunnel

After setup, run the server:

```bash
source .venv/bin/activate
youtube-digest serve
```

Or run a one-off batch digest:

```bash
source .venv/bin/activate
youtube-digest run
```

## How It Works

**Daily digest flow:**

1. Fetches RSS feeds from all configured YouTube channels
2. For each subscriber, filters to their channels and skips already-sent videos
3. Fetches the transcript, sends it to an LLM for summarization
4. Archives the markdown summary locally and emails the HTML digest
5. Tracks deliveries in SQLite to prevent duplicates

**On-demand flow:**

1. Open the web UI at `http://localhost:8080`
2. Paste any YouTube URL and pick a subscriber
3. Summary is generated in the background and emailed when ready

## Configuration

All user configuration lives in three files (created by the installer):

| File | Purpose |
|---|---|
| `.env` | API keys and credentials |
| `channels.yaml` | YouTube channels to follow |
| `subscribers.yaml` | Email subscribers and their channel preferences |

See [`.env.example`](.env.example), [`channels.example.yaml`](channels.example.yaml), and [`subscribers.example.yaml`](subscribers.example.yaml) for annotated templates.

### Environment Variables

| Variable | Description | Default |
|---|---|---|
| `OPENAI_API_KEY` | API key for your LLM provider | *(required)* |
| `OPENAI_BASE_URL` | OpenAI-compatible API base URL | `https://api.openai.com/v1` |
| `MODEL_NAME` | Model to use for summarization | `gpt-4o-mini` |
| `GMAIL_ADDRESS` | Gmail address for sending digests | *(required)* |
| `GMAIL_APP_PASSWORD` | Gmail app password ([create one here](https://support.google.com/accounts/answer/185833)) | *(required)* |
| `MAX_VIDEOS_PER_DAY` | Global max videos per subscriber per run | `0` (no limit) |

Any OpenAI-compatible API works -- OpenAI, local LLMs via llama.cpp/vLLM/Ollama, Anthropic via a proxy, etc.

## CLI Reference

```bash
youtube-digest run                # run one digest cycle
youtube-digest serve              # start the web server + daily scheduler
youtube-digest serve --port 9000  # custom port
youtube-digest serve --reload     # auto-reload for development
```

In server mode, APScheduler runs the daily digest automatically at **05:00 America/Los_Angeles**.

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Web form for on-demand video submission |
| `POST` | `/api/summarize` | Queue a video: `{"url": "...", "subscriber_email": "..."}` |
| `GET` | `/api/health` | Health check: `{"status": "ok"}` |

## Deployment

For production deployment options (systemd service, Cloudflare tunnel, reverse proxy), see [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

## Project Structure

```
youtube-digest/
├── install.sh                 # interactive installer
├── .env.example               # env var template
├── channels.example.yaml      # channel config template
├── subscribers.example.yaml   # subscriber config template
├── pyproject.toml
├── deploy/templates/          # systemd + cloudflare templates
├── docs/DEPLOYMENT.md
├── templates/                 # Jinja2 email + web templates
│   ├── email.html
│   └── form.html
└── youtube_digest/            # Python package
    ├── __main__.py            # CLI entry point
    ├── config.py              # settings + channel loading
    ├── models.py              # data models
    ├── pipeline.py            # orchestration logic
    ├── server.py              # FastAPI app + scheduler
    ├── services/
    │   ├── feed.py            # RSS feed fetching
    │   ├── transcript.py      # YouTube transcript retrieval
    │   └── summarizer.py      # LLM summarization
    ├── delivery/
    │   ├── archive.py         # markdown archive
    │   └── email.py           # Gmail SMTP delivery
    └── storage/
        ├── database.py        # SQLite sent-video tracking
        └── subscribers.py     # subscriber YAML loading
```

## Dependencies

| Package | Purpose |
|---|---|
| `openai` | OpenAI-compatible LLM client |
| `youtube-transcript-api` | Transcript retrieval |
| `feedparser` | RSS/Atom feed parsing |
| `requests` | HTTP requests |
| `jinja2` | HTML template rendering |
| `markdown` | Markdown to HTML conversion |
| `python-dotenv` | `.env` file loading |
| `pyyaml` | YAML config parsing |
| `fastapi` | Web server framework |
| `uvicorn` | ASGI server |
| `apscheduler` | Scheduled daily digest |

## License

[MIT](LICENSE)
