FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directory for SQLite
RUN mkdir -p data

# Expose ports
EXPOSE 5001 5002

# Production: use gunicorn with eventlet for WebSocket support
# Override CMD in docker-compose per service
CMD ["gunicorn", "--bind", "0.0.0.0:5002", "--worker-class", "eventlet", "--workers", "1", "--timeout", "120", "backend.server:app"]
