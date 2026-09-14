# Deployment & Production Setup

> [!NOTE]
> This guide outlines how to deploy Sentinel Gujarat for production environments using Docker Compose, Gunicorn, and Nginx.

## 1. Server Requirements

- **CPU**: Minimum 8 Cores (AVX2 support required for CPU fallback).
- **RAM**: 16 GB DDR4 minimum (32 GB recommended for in-memory ring buffers).
- **GPU (Highly Recommended)**: NVIDIA RTX 3060 / 4060 or better (Tesla T4/L4 for cloud). Must support CUDA 12.x.
- **OS**: Ubuntu 22.04 LTS or Debian 12.

## 2. Docker Compose Deployment

The repository includes a `docker-compose.yml` for standing up the PostgreSQL database and Redis (if used for caching).

### Step 1: Environment Variables
Create your `.env` file from the template:
```bash
cp .env.example .env
nano .env
```
Ensure you set secure values for `POSTGRES_PASSWORD` and `JWT_SECRET`.

### Step 2: Start the Database Layer
```bash
docker-compose up -d
```
Verify PostgreSQL is running: `docker ps`.

### Step 3: Database Migrations
Before starting the application, apply Alembic migrations to create the tables:
```bash
alembic upgrade head
```

## 3. Running the FastAPI Application

In production, do not use `uvicorn --reload`. Use Gunicorn with Uvicorn workers.

```bash
# Install dependencies in a virtual environment
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

# Start Gunicorn (Example: 4 workers)
gunicorn backend.app.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

## 4. Nginx Reverse Proxy (SSL Termination)

You must secure the application with HTTPS. Below is a sample Nginx configuration.

```nginx
server {
    listen 80;
    server_name sentinel.gujaratpolice.gov.in;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name sentinel.gujaratpolice.gov.in;

    ssl_certificate /etc/letsencrypt/live/sentinel/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/sentinel/privkey.pem;

    # Frontend UI
    location /ui/ {
        proxy_pass http://127.0.0.0:8000/ui/;
        proxy_set_header Host $host;
    }

    # REST API
    location / {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # WebSocket Hub (/ws/alerts)
    location /ws/ {
        proxy_pass http://127.0.0.1:8000/ws/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }
}
```

## 5. Scaling AI Ingestion

The FastAPI application handles the API and HLS streaming natively via AsyncIO. However, ingesting RTSP streams and running YOLO inference is highly CPU/GPU bound.

To scale across 30+ cameras:
1. Do not run the ingestion loop inside the main API process.
2. Use the dedicated CLI script `scripts/start_stream.py` to spawn ingestion workers.
3. Example: Use `supervisord` or `systemd` to spawn 30 separate `python scripts/start_stream.py cam01`, `cam02`, etc., processes. These will push data directly to the database and trigger API webhooks.
