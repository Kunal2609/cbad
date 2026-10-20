# ==============================================================================
# Flask API Host Image (Trusted Orchestrator)
# Note: Interacts with Docker daemon via /var/run/docker.sock (Docker-out-of-Docker)
# ==============================================================================

FROM python:3.10-slim-bullseye

# Install system dependencies and security updates
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create unprivileged application user
RUN groupadd -g 10001 appuser && \
    useradd -u 10001 -g appuser -s /bin/bash -m appuser

WORKDIR /app

# Copy dependency definition and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Create staging directory fallback and assign permissions
RUN mkdir -p /app/sandbox && chown -R appuser:appuser /app

# Switch to non-root application user
USER appuser:appuser

# Expose API port
EXPOSE 5000

# Run with Gunicorn WSGI server
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "--timeout", "30", "--access-logfile", "-", "--error-logfile", "-", "run:app"]
