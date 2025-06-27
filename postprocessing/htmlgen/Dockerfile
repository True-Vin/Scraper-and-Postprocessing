FROM python:3.9

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install boto3

# Copy application files
COPY htmlgen.py .
COPY style1.css .
COPY style2.css .
COPY testMatrix.stripe0.png .
COPY testMatrix.stripe1.png .
COPY testMatrix.stripe2.png .
COPY testMatrix.stripe3.png .

# Create data directory and copy assets
RUN mkdir -p /app/data/output_html
COPY style1.css style2.css testMatrix.stripe0.png testMatrix.stripe1.png testMatrix.stripe2.png testMatrix.stripe3.png /app/data/output_html/

# Set up volume for persistent data
VOLUME ["/app/data"]

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5002/health || exit 1

CMD ["python", "htmlgen.py"]
