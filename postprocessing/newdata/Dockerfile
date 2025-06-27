# ─────────────────────────────────────────────────────────────
# Image for the "newdata" service (runs newdata.py)
# ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir \
        boto3 \
        requests \
        beautifulsoup4

# Copy the scraper code into the container
WORKDIR /app
COPY newdata.py /app/

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import boto3; print('Health check passed')" || exit 1

# Default command
CMD ["python", "newdata.py"]
