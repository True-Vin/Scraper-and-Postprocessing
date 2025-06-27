# EC2 Deployment Guide for Postprocessing Application

This guide explains how to deploy the postprocessing application on AWS EC2 with proper environment variable configuration.

## 🏗️ Architecture Overview

The application consists of 4 main services:

1. **getdata** - Scrapes auction data from IAAI website
2. **htmlgen** - Generates HTML files from scraped data
3. **converter** - Converts HTML to text using OCR
4. **newdata** - Updates DynamoDB with processed data

## 📋 Prerequisites

### AWS Resources Required
- **DynamoDB Table**: `AuctionData` (or custom name)
- **S3 Bucket**: `auctionshtml` (or custom name)
- **EC2 Instance**: t3.medium or larger (recommended)
- **IAM Role/User**: With permissions for DynamoDB and S3

### EC2 Instance Requirements
- **OS**: Ubuntu 20.04 LTS or later
- **RAM**: Minimum 4GB (8GB recommended)
- **Storage**: Minimum 20GB
- **Security Groups**: Allow ports 22 (SSH), 5002, 5003

## 🚀 Quick Deployment

### 1. Launch EC2 Instance
```bash
# Connect to your EC2 instance
ssh -i your-key.pem ubuntu@your-ec2-ip
```

### 2. Clone Repository
```bash
git clone <your-repository-url>
cd postprocessing
```

### 3. Configure Environment
```bash
# Copy environment template
cp .env.example .env

# Edit with your actual values
nano .env
```

### 4. Run Deployment Script
```bash
# Make script executable
chmod +x deploy-ec2.sh

# Run deployment
./deploy-ec2.sh
```

## ⚙️ Environment Configuration

### Required Environment Variables

Create a `.env` file with the following variables:

```bash
# AWS Credentials
AWS_ACCESS_KEY_ID=your_aws_access_key_here
AWS_SECRET_ACCESS_KEY=your_aws_secret_access_key_here
AWS_REGION=eu-north-1

# DynamoDB Configuration
DYNAMODB_TABLE_NAME=AuctionData

# S3 Configuration
S3_BUCKET_NAME=auctionshtml

# Sharding Configuration (for parallel processing)
SHARD_ID=0
TOTAL_SHARDS=1

# Python Configuration
PYTHONUNBUFFERED=1

# Chrome Configuration (for converter service)
CHROME_BIN=/usr/bin/google-chrome
```

### Environment Variable Details

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `AWS_ACCESS_KEY_ID` | AWS Access Key | - | ✅ |
| `AWS_SECRET_ACCESS_KEY` | AWS Secret Key | - | ✅ |
| `AWS_REGION` | AWS Region | eu-north-1 | ✅ |
| `DYNAMODB_TABLE_NAME` | DynamoDB table name | AuctionData | ✅ |
| `S3_BUCKET_NAME` | S3 bucket name | auctionshtml | ✅ |
| `SHARD_ID` | Worker shard ID (0-based) | 0 | ❌ |
| `TOTAL_SHARDS` | Total number of workers | 1 | ❌ |
| `PYTHONUNBUFFERED` | Python output buffering | 1 | ❌ |
| `CHROME_BIN` | Chrome binary path | /usr/bin/google-chrome | ❌ |

## 🔧 Manual Deployment Steps

If you prefer manual deployment:

### 1. Install Dependencies
```bash
# Update system
sudo apt-get update && sudo apt-get upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
```

### 2. Configure Environment
```bash
# Create .env file
cp .env.example .env
nano .env  # Edit with your values
```

### 3. Build and Run
```bash
# Build images
docker-compose build

# Start services
docker-compose up -d

# Check status
docker-compose ps
```

## 📊 Monitoring and Management

### View Logs
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f htmlgen
docker-compose logs -f converter
docker-compose logs -f newdata
docker-compose logs -f getdata
```

### Service Management
```bash
# Stop all services
docker-compose down

# Restart specific service
docker-compose restart htmlgen

# View service status
docker-compose ps

# Scale services (for parallel processing)
docker-compose up -d --scale getdata=3
```

### Health Checks
Each service includes health checks:
- **htmlgen**: HTTP health check on port 5002
- **converter**: Python import check
- **newdata**: Python import check
- **getdata**: Python import check

## 🔄 Scaling for Production

### Parallel Processing
To run multiple instances for parallel processing:

1. **Update environment variables**:
```bash
# For worker 1
SHARD_ID=0
TOTAL_SHARDS=3

# For worker 2
SHARD_ID=1
TOTAL_SHARDS=3

# For worker 3
SHARD_ID=2
TOTAL_SHARDS=3
```

2. **Scale services**:
```bash
docker-compose up -d --scale getdata=3
```

### Resource Optimization
For better performance on EC2:

1. **Instance Types**:
   - **Development**: t3.medium
   - **Production**: t3.large or c5.large
   - **High Performance**: c5.xlarge

2. **Storage**:
   - Use EBS gp3 volumes for better performance
   - Consider EFS for shared storage across instances

3. **Memory**:
   - Monitor memory usage: `docker stats`
   - Adjust container memory limits if needed

## 🛠️ Troubleshooting

### Common Issues

1. **AWS Credentials Error**:
```bash
# Check environment variables
docker-compose exec htmlgen env | grep AWS
```

2. **Chrome/ChromeDriver Issues**:
```bash
# Check Chrome installation
docker-compose exec converter google-chrome --version
```

3. **DynamoDB Connection Issues**:
```bash
# Test AWS connectivity
docker-compose exec htmlgen python -c "import boto3; print('AWS OK')"
```

4. **Memory Issues**:
```bash
# Check container resource usage
docker stats
```

### Log Analysis
```bash
# Search for errors
docker-compose logs | grep -i error

# Search for specific patterns
docker-compose logs | grep "AWS_ACCESS_KEY_ID"
```

## 🔒 Security Considerations

### IAM Permissions
Ensure your AWS credentials have minimal required permissions:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:UpdateItem",
                "dynamodb:Scan",
                "dynamodb:Query"
            ],
            "Resource": "arn:aws:dynamodb:eu-north-1:*:table/AuctionData"
        },
        {
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:PutObject",
                "s3:DeleteObject"
            ],
            "Resource": "arn:aws:s3:::auctionshtml/*"
        }
    ]
}
```

### Environment Security
- Never commit `.env` files to version control
- Use AWS IAM roles instead of access keys when possible
- Rotate access keys regularly
- Use VPC and security groups to restrict access

## 📈 Performance Monitoring

### Key Metrics to Monitor
- **DynamoDB Read/Write Units**
- **S3 Request Count**
- **EC2 CPU/Memory Usage**
- **Container Health Status**
- **Processing Queue Length**

### CloudWatch Integration
Consider setting up CloudWatch alarms for:
- High CPU usage (>80%)
- High memory usage (>85%)
- DynamoDB throttling
- S3 errors

## 🆘 Support

For issues or questions:
1. Check the logs: `docker-compose logs -f`
2. Verify environment variables: `docker-compose exec service env`
3. Test AWS connectivity: `docker-compose exec service python -c "import boto3; print('OK')"`
4. Check service health: `docker-compose ps` 