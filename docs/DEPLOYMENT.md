# Deployment Guide

This guide covers production deployment options beyond the basic `youtube-digest serve` workflow.

## Systemd Service (Linux)

The installer can set this up automatically (`./install.sh` and answer "yes" to the systemd prompt), or you can do it manually:

### Manual Setup

1. Render the service template:

```bash
sed -e 's|__RUN_USER__|youruser|g' \
    -e 's|__WORKING_DIR__|/path/to/youtube-digest|g' \
    -e 's|__PYTHON_BIN__|/path/to/youtube-digest/.venv/bin/python|g' \
    -e 's|__SERVER_HOST__|0.0.0.0|g' \
    -e 's|__SERVER_PORT__|8080|g' \
    deploy/templates/youtube-digest.service.tpl \
    | sudo tee /etc/systemd/system/youtube-digest.service
```

2. Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now youtube-digest
```

### Common Operations

```bash
sudo systemctl status youtube-digest      # check status
sudo systemctl restart youtube-digest     # restart
sudo journalctl -u youtube-digest -f      # follow logs
```

## Cloudflare Tunnel

A Cloudflare tunnel exposes your local server to the internet without opening ports.

### Prerequisites

- A Cloudflare account with a domain
- `cloudflared` installed ([install guide](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/))
- `jq` installed for JSON parsing

The installer can handle this automatically (`./install.sh` and answer "yes" to the Cloudflare prompt), or follow these manual steps:

### Manual Setup

1. Authenticate with Cloudflare:

```bash
cloudflared tunnel login
```

2. Create a tunnel:

```bash
cloudflared tunnel create youtube-digest
```

3. Note the tunnel ID and route DNS:

```bash
TUNNEL_ID=$(cloudflared tunnel list --output json | jq -r '.[] | select(.name=="youtube-digest") | .id')
cloudflared tunnel route dns "$TUNNEL_ID" youtube.yourdomain.com
```

4. Copy credentials and write config:

```bash
sudo mkdir -p /etc/cloudflared
sudo cp ~/.cloudflared/${TUNNEL_ID}.json /etc/cloudflared/
sudo chmod 600 /etc/cloudflared/${TUNNEL_ID}.json

sudo tee /etc/cloudflared/config.yml <<EOF
tunnel: ${TUNNEL_ID}
credentials-file: /etc/cloudflared/${TUNNEL_ID}.json

ingress:
  - hostname: youtube.yourdomain.com
    service: http://127.0.0.1:8080
  - service: http_status:404
EOF
```

5. Start cloudflared:

```bash
sudo systemctl enable --now cloudflared
```

6. **Important:** Configure a Cloudflare Access policy in the [Zero Trust dashboard](https://one.dash.cloudflare.com/) to restrict who can reach your instance.

## Reverse Proxy (nginx / Caddy)

If you prefer a traditional reverse proxy instead of Cloudflare tunnels:

### nginx

```nginx
server {
    listen 80;
    server_name youtube.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Add TLS with [certbot](https://certbot.eff.org/):

```bash
sudo certbot --nginx -d youtube.yourdomain.com
```

### Caddy

```
youtube.yourdomain.com {
    reverse_proxy 127.0.0.1:8080
}
```

Caddy handles TLS automatically.

## Verification

After deployment, verify everything is working:

```bash
# Local health check
curl -fsS http://127.0.0.1:8080/api/health

# Service status
sudo systemctl status youtube-digest

# Trigger a test run
source .venv/bin/activate
youtube-digest run
```

## Troubleshooting

### Service fails to start

```bash
sudo journalctl -u youtube-digest -n 100 --no-pager
```

Common causes:
- Missing or invalid `.env` values
- LLM endpoint not reachable
- Port already in use: `ss -lntp | grep :8080`

### Emails not sending

- Verify `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` in `.env`
- Ensure "Less secure apps" or App Passwords are configured in Gmail
- Check logs for SMTP errors

### No videos processed

- Verify channels in `channels.yaml` have valid YouTube channel IDs
- Check that subscriber channel names in `subscribers.yaml` match names in `channels.yaml` exactly
- Verify the RSS feed works: `curl "https://www.youtube.com/feeds/videos.xml?channel_id=CHANNEL_ID"`
