#!/bin/bash

# EC2 Deployment Script for Postprocessing Application
# This script sets up the environment and deploys the application on EC2

set -e  # Exit on any error

echo "🚀 Starting EC2 deployment for postprocessing application..."

# Check if running on EC2
if ! curl -s http://169.254.169.254/latest/meta-data/instance-id > /dev/null 2>&1; then
    echo "⚠️  Warning: This script is designed to run on EC2 instances"
fi

# Update system packages
echo "📦 Updating system packages..."
sudo apt-get update
sudo apt-get upgrade -y

# Install Docker if not already installed
if ! command -v docker &> /dev/null; then
    echo "🐳 Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker $USER
    rm get-docker.sh
fi

# Install Docker Compose if not already installed
if ! command -v docker-compose &> /dev/null; then
    echo "🐳 Installing Docker Compose..."
    sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
fi

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo "📝 Creating .env file from template..."
    cp .env.example .env
    echo "⚠️  Please edit .env file with your actual AWS credentials and configuration"
    echo "   Required variables:"
    echo "   - AWS_ACCESS_KEY_ID"
    echo "   - AWS_SECRET_ACCESS_KEY"
    echo "   - AWS_REGION"
    echo "   - DYNAMODB_TABLE_NAME"
    echo "   - S3_BUCKET_NAME"
    exit 1
fi

# Validate required environment variables
echo "🔍 Validating environment variables..."
source .env

required_vars=("AWS_ACCESS_KEY_ID" "AWS_SECRET_ACCESS_KEY" "AWS_REGION" "DYNAMODB_TABLE_NAME" "S3_BUCKET_NAME")
for var in "${required_vars[@]}"; do
    if [ -z "${!var}" ]; then
        echo "❌ Error: $var is not set in .env file"
        exit 1
    fi
done

echo "✅ Environment variables validated"

# Create data directory
echo "📁 Creating data directory..."
mkdir -p data

# Build and start services
echo "🔨 Building Docker images..."
docker-compose build

echo "🚀 Starting services..."
docker-compose up -d

# Wait for services to be ready
echo "⏳ Waiting for services to be ready..."
sleep 30

# Check service status
echo "📊 Checking service status..."
docker-compose ps

# Show logs
echo "📋 Recent logs:"
docker-compose logs --tail=20

echo "✅ Deployment completed successfully!"
echo ""
echo "📋 Useful commands:"
echo "  - View logs: docker-compose logs -f"
echo "  - Stop services: docker-compose down"
echo "  - Restart services: docker-compose restart"
echo "  - View service status: docker-compose ps"
echo ""
echo "🌐 Services are running on:"
echo "  - htmlgen: http://localhost:5002"
echo "  - converter: http://localhost:5003" 