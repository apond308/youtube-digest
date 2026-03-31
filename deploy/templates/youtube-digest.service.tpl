[Unit]
Description=YouTube Digest FastAPI server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=__RUN_USER__
WorkingDirectory=__WORKING_DIR__
ExecStart=__PYTHON_BIN__ -m youtube_digest serve --host __SERVER_HOST__ --port __SERVER_PORT__
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
