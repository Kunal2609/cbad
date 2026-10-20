# Python Code Execution Sandbox

[![Build Status](https://img.shields.io/badge/build-passing-brightgreen.svg)]()
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.14-blue.svg)]()
[![Docker](https://img.shields.io/badge/docker-isolated-2496ED.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()

A high-performance, production-grade Python Code Execution Sandbox engine. Designed with a strict threat model and multi-layered defense-in-depth isolation, it securely executes arbitrary, untrusted Python source code in ephemeral, resource-constrained Docker containers.

---

## Architecture

```mermaid
flowchart TD
    A[Browser / API Client] -->|POST /api/execute| B[Flask API Host]
    B --> C{Validation & Limiter}
    C -->|Invalid / Disallowed Keys| C1[400 invalid_request]
    C -->|Rate Limit Exceeded| C2[429 rate_limited]
    C -->|Concurrency Saturated| C3[503 at_capacity]
    C -->|Valid Payload| D[Stage script.py: 0o444 on tmpfs]
    D --> E[DockerManager: Spawn Ephemeral Container]
    E --> F[python-sandbox-exec:latest<br/>no network, read-only root, cap_drop=ALL, pids/mem/cpu limit]
    F -->|Bounded Streamed Logs| G[Capture Output & Check Truncation]
    G --> H[Finally Block: Force Kill/Remove Container & Wipe Temp Dir]
    H -->|200 JSON Response| A
    
    I[Startup & Periodic Reapers] -.->|Check sandbox=true label| E
```

---

## Technology Stack

- **Backend Framework**: Python 3, Flask 3.1, Gunicorn
- **Orchestration & Containerization**: Docker SDK for Python (`docker-py`), Docker Engine
- **Concurrency & Rate Limiting**: Python Threading Semaphores, Flask-Limiter, Redis
- **Frontend IDE**: Vanilla JavaScript, Monaco Editor with responsive `<textarea>` line-number gutter fallback
- **Testing**: Pytest, Pytest-Mock

---

## Security Model & Threat Mitigation

User-submitted code is **always treated as hostile**. The platform implements layered defense-in-depth:

| Security Vector | Implementation & Controls |
| :--- | :--- |
| **No Host Execution** | User code is **never** executed using `exec()`, `eval()`, or host subprocess. All execution occurs inside dedicated containers. |
| **Two-Image Model** | The trusted Flask API image interacts with Docker. The execution container image (`python-sandbox-exec:latest`) is completely unprivileged. |
| **Non-Root Execution** | Sandboxed containers run as a dedicated, non-root `sandbox:sandbox` user created without a shell or home directory. |
| **Network Isolation** | `network_mode="none"` blocks all egress and ingress network connections (sockets, HTTP/HTTPS, DNS). |
| **Filesystem Lockdown** | `read_only=True` enforces an immutable root filesystem. A size-capped (16MB) `tmpfs` is mounted at `/tmp` with `noexec,nosuid,nodev`. |
| **Capability Dropping** | `cap_drop=["ALL"]` drops all Linux kernel capabilities. `no-new-privileges:true` prevents privilege escalation. |
| **Memory Exhaustion** | Strict memory limit (`MEMORY_LIMIT=50m`). Swap is disabled by matching `MEMSWAP_LIMIT=50m` (preventing swap amplification attacks). |
| **Fork Bombs & Threads** | `PIDS_LIMIT=64` stops thread and fork bombs immediately. |
| **CPU Saturation** | `CPU_NANO=500000000` caps CPU usage to 0.5 vCPU. |
| **External Watchdog** | Bounded timeouts via `ThreadPoolExecutor` and Docker API wait prevent container hangs from locking worker threads. |
| **Unbounded Output Cap** | Log streaming terminates at `MAX_OUTPUT_SIZE=65536` bytes with a truncation marker, preventing memory exhaustion. |
| **Secret Sanitization** | Sandboxed containers receive an explicitly empty environment (`environment={}`). Host environment variables and secrets are never passed. |
| **Strict Request Validation** | Non-string code, oversized payloads, NUL bytes (`\x00`), and client-injected Docker configs are rejected before staging. |
| **Orphan Container Reapers** | Startup reaper wipes crash leftovers; periodic background reaper thread purges containers exceeding execution timeouts. |

---

## Residual Risks & Stronger Isolation

> [!WARNING]
> While Docker namespaces and cgroups provide robust containerization, shared-kernel container environments have residual risks:
> - **Kernel-Level Vulnerabilities**: Linux kernel zero-days or cgroup exploits could breach standard runc isolation.
> - **Microarchitectural Side-Channels**: Speculative execution timing (Spectre/Meltdown) across CPU cores.
> 
> **Recommendations for High-Security Multi-Tenant Deployments**:
> 1. **gVisor (`runsc`)**: The codebase includes automatic detection for gVisor. If `runsc` is installed on your Docker host, the sandbox automatically selects `runtime="runsc"`, providing user-space kernel syscall interception.
> 2. **MicroVMs (Firecracker / Kata Containers)**: For multi-tenant hosting, consider wrapping execution inside lightweight Firecracker microVMs.

---

## Configuration Reference

All settings can be customized via `.env` or environment variables:

| Env Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `EXECUTION_TIMEOUT` | int (sec) | `3` | Hard wall-clock limit per execution |
| `TIMEOUT_GRACE_PERIOD` | int (sec) | `2` | Extra time allowed for kill/remove before API gives up |
| `MEMORY_LIMIT` | str | `50m` | Docker container memory limit |
| `MEMSWAP_LIMIT` | str | `50m` | Must equal `MEMORY_LIMIT` to disable swap |
| `CPU_NANO` | int | `500000000` | CPU quota in nano_cpus (0.5 vCPU) |
| `PIDS_LIMIT` | int | `64` | Maximum processes/threads inside container |
| `TMPFS_SIZE` | str | `16m` | Size cap for container writable `/tmp` |
| `MAX_CODE_SIZE` | int (bytes) | `32768` | Maximum accepted source code size |
| `MAX_CODE_LINES` | int | `1000` | Maximum source code line count |
| `MAX_OUTPUT_SIZE` | int (bytes) | `65536` | Combined stdout + stderr cap before truncation |
| `MAX_CONCURRENT_EXECUTIONS` | int | `10` | Concurrency semaphore limit; excess gets 503 |
| `RATE_LIMIT` | str | `20/minute` | Per-IP rate limit via Flask-Limiter |
| `RATE_LIMIT_STORAGE_URI` | str | `memory://` | Limiter storage (`redis://...` for production) |
| `EXECUTION_IMAGE` | str | `python-sandbox-exec:latest` | Tag of pre-built hardened execution image |
| `STAGING_ROOT` | str | `/dev/shm/sandbox` | File staging root (`/dev/shm` tmpfs or fallback) |
| `DOCKER_TIMEOUT` | int (sec) | `10` | HTTP client timeout for Docker SDK calls |

---

## API Specification

### 1. Execute Code
`POST /api/execute`

**Request Body:**
```json
{
  "code": "print('Hello World')\n"
}
```

**Success Response (`200 OK`):**
```json
{
  "success": true,
  "execution_id": "8f3e2b1a9c0d4e5f6a7b8c9d0e1f2a3b",
  "status": "completed",
  "stdout": "Hello World\n",
  "stderr": "",
  "exit_code": 0,
  "execution_time_ms": 142,
  "truncated": false
}
```

**Timeout Response (`200 OK`):**
```json
{
  "success": false,
  "execution_id": "8f3e2b1a9c0d4e5f6a7b8c9d0e1f2a3b",
  "status": "timeout",
  "error": "Execution timed out after 3 seconds.",
  "stdout": "",
  "stderr": "",
  "execution_time_ms": 3000,
  "truncated": false
}
```

**Validation Error (`400 Bad Request`):**
```json
{
  "success": false,
  "status": "invalid_request",
  "error": "Code exceeds maximum size of 32768 bytes."
}
```

**Rate Limited (`429 Too Many Requests`):**
```json
{
  "success": false,
  "status": "rate_limited",
  "error": "Too many requests. Try again shortly."
}
```

**At Capacity (`503 Service Unavailable`):**
```json
{
  "success": false,
  "status": "at_capacity",
  "error": "Sandbox is at capacity. Try again shortly."
}
```

**Internal Error (`500 Internal Server Error`):**
```json
{
  "success": false,
  "status": "internal_error",
  "error": "Execution could not be completed."
}
```

---

### 2. Health Check
`GET /api/health`

**Healthy (`200 OK`):**
```json
{
  "status": "healthy",
  "runtime": "runc (default)",
  "image": "python-sandbox-exec:latest",
  "max_concurrent": 10,
  "timeout_sec": 3
}
```

**Degraded (`503 Service Unavailable`):**
```json
{
  "status": "degraded",
  "reason": "docker_unreachable"
}
```

---

## Directory Structure

```
code-sandbox/
├── app/
│   ├── __init__.py          # Flask app factory, rate limiter, security headers
│   ├── config.py            # Typed environment configuration
│   ├── docker_manager.py    # Isolated Docker SDK interactions & hardening
│   ├── executor.py          # SandboxExecutor: staging, execution orchestration
│   ├── reaper.py            # Startup & periodic orphan container cleanup
│   ├── routes.py            # Thin Flask views and API routing
│   ├── security.py          # Request validation, bounds, sanitization
│   └── utils.py             # Structured JSON logger, truncation helpers
├── docker/
│   └── Dockerfile.sandbox   # Hardened Alpine non-root Python execution image
├── templates/
│   └── index.html           # Dark modern IDE UI template
├── static/
│   ├── css/style.css        # Responsive dark theme styling
│   └── js/app.js            # Monaco editor integration & async fetch
├── sandbox/
│   └── .gitkeep             # Fallback staging root
├── tests/
│   ├── test_api.py          # API contract & error handling tests
│   ├── test_executor.py     # Executor unit tests with mocked Docker SDK
│   ├── test_security.py     # Validation logic & edge case tests
│   ├── test_reaper.py       # Orphan container cleanup tests
│   └── test_docker_integration.py # 13 Adversarial integration tests
├── Dockerfile               # Trusted Flask API host image
├── docker-compose.yml       # Multi-target Compose configuration
├── requirements.txt         # Pinned production dependencies
├── run.py                   # Development entrypoint script
└── README.md
```

---

## Quickstart & Local Setup

### 1. Clone & Install Dependencies
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Build the Sandbox Execution Image
```bash
docker build -t python-sandbox-exec:latest -f docker/Dockerfile.sandbox .
```

### 3. Run the Application
```bash
python run.py
```
Open your browser at `http://localhost:5000`.

---

## Running via Docker Compose

Run the entire stack (API + Sandbox image + Redis) with a single command:

```bash
docker compose up --build -d
```

View logs:
```bash
docker compose logs -f api
```

---

## Running Test Suites

Run the full unit and adversarial test suite:

```bash
# Run all unit tests
pytest tests/test_security.py tests/test_executor.py tests/test_api.py tests/test_reaper.py -v

# Run live Docker integration tests (requires Docker daemon)
pytest tests/test_docker_integration.py -v
```

---

## Production AWS EC2 Deployment Guide

Follow these steps for deploying the sandbox on an Ubuntu LTS EC2 instance:

### 1. Provision EC2 Instance
- **Instance Type**: `t3.medium` or larger (depending on `MAX_CONCURRENT_EXECUTIONS × MEMORY_LIMIT`).
- **OS**: Ubuntu 24.04 / 22.04 LTS.
- **Security Group**: Allow inbound `80` (HTTP) and `443` (HTTPS) from `0.0.0.0/0`. Allow inbound `22` (SSH) **only from your admin IP**. Do NOT expose port 5000 or the Docker daemon port publicly.

### 2. Install Docker & Redis
```bash
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y docker.io docker-compose-plugin redis-server nginx certbot python3-certbot-nginx
sudo usermod -aG docker ubuntu
```

### 3. Clone Repository & Setup Environment
```bash
git clone <repository_url> /opt/code-sandbox
cd /opt/code-sandbox
cp .env.example .env
```
Edit `.env` to configure production parameters:
```env
RATE_LIMIT=15/minute
RATE_LIMIT_STORAGE_URI=redis://localhost:6379/0
MAX_CONCURRENT_EXECUTIONS=12
STAGING_ROOT=/dev/shm/sandbox
```

### 4. Build Images & Start Stack
```bash
# Build the execution image
docker build -t python-sandbox-exec:latest -f docker/Dockerfile.sandbox .

# Start containers with Docker Compose
docker compose -f docker-compose.yml up -d
```

### 5. Configure Nginx Reverse Proxy with HTTPS
Create `/etc/nginx/sites-available/sandbox`:
```nginx
server {
    server_name sandbox.yourdomain.com;

    # Limit client request body size
    client_max_body_size 64k;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Timeouts matching sandbox execution window
        proxy_connect_timeout 10s;
        proxy_read_timeout 15s;
    }
}
```
Enable site and acquire SSL certificate:
```bash
sudo ln -s /etc/nginx/sites-available/sandbox /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d sandbox.yourdomain.com
```

### 6. Configure Daemon Log Rotation
Prevent container logs from exhausting disk space in `/etc/docker/daemon.json`:
```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
```
Reload Docker: `sudo systemctl restart docker`.
