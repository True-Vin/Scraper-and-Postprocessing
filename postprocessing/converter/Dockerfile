# Use Python 3.9 (Bullseye-based) as the base image
FROM python:3.9-bullseye

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# ------------------------------------------------
# Install system dependencies for OpenCV, Selenium, and Chrome
# ------------------------------------------------
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libnss3 \
    libgconf-2-4 \
    fonts-liberation \
    wget \
    gnupg \
    unzip \
    curl \
    xvfb \
    libxss1 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libgbm1 \
    libxkbcommon0 \
    libpango-1.0-0 \
    libcups2 \
    libasound2 \
    libgtk-3-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------
# Set up working directory
# ------------------------------------------------
WORKDIR /app

# ------------------------------------------------
# Install Google Chrome Stable
# ------------------------------------------------
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome-keyring.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------
# Install ChromeDriver (pinned to a compatible version)
# ------------------------------------------------
RUN wget -q "https://storage.googleapis.com/chrome-for-testing-public/135.0.7049.84/linux64/chromedriver-linux64.zip" \
    && unzip chromedriver-linux64.zip \
    && rm chromedriver-linux64.zip \
    && chmod +x chromedriver-linux64/chromedriver \
    && mv chromedriver-linux64/chromedriver /usr/local/bin/

# ------------------------------------------------
# Set environment variables for Chrome
# ------------------------------------------------
ENV CHROME_BIN=/usr/bin/google-chrome
ENV PATH="/usr/local/bin:${PATH}"

# ------------------------------------------------
# Install Python dependencies
# ------------------------------------------------
COPY requirements.txt .
RUN pip install -r requirements.txt
RUN pip install boto3

# ------------------------------------------------
# Copy project files (your main script and assets)
# ------------------------------------------------
COPY . .
RUN mkdir -p /app/data /app/data/output_html
COPY style1.css style2.css testMatrix.stripe0.png testMatrix.stripe1.png testMatrix.stripe2.png testMatrix.stripe3.png /app/data/output_html/

# ------------------------------------------------
# Set up volume to persist data
# ------------------------------------------------
VOLUME ["/app/data"]

# ------------------------------------------------
# Health check
# ------------------------------------------------
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import boto3; print('Health check passed')" || exit 1

# ------------------------------------------------
# Set the default command to run the script
# ------------------------------------------------
CMD ["python", "converter.py"]
