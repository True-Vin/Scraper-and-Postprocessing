import boto3
import json
import os
import time
from botocore.exceptions import NoCredentialsError, PartialCredentialsError

# ──────────────────────────────────────────────────────────────
# CONFIGURATION (Environment Variables)
# ──────────────────────────────────────────────────────────────
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
DYNAMODB_TABLE_NAME = os.getenv("DYNAMODB_TABLE_NAME", "AuctionData")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "auctionshtml")

# Validate required environment variables
if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
    raise ValueError("AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set as environment variables")

# Initialize DynamoDB
dynamodb = boto3.resource(
    "dynamodb",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION
)
table = dynamodb.Table(DYNAMODB_TABLE_NAME)

# File Path (Modify if needed)
RESULT_FILE = "result.json"  # Ensure this file exists in the same directory

def upload_json_to_dynamodb():
    """Reads result.json and uploads data to DynamoDB."""
    if not os.path.exists(RESULT_FILE):
        print(f"❌ Error: {RESULT_FILE} not found.")
        return
    
    try:
        # Read JSON file
        with open(RESULT_FILE, "r", encoding="utf-8") as json_file:
            try:
                data_instances = json.load(json_file)
            except json.JSONDecodeError:
                print(f"❌ Error: {RESULT_FILE} contains invalid JSON.")
                return

        # Upload each entry to DynamoDB
        for instance in data_instances:
            stock_number = instance.get("stock_number", f"stock_{int(time.time())}")  # Use timestamp if missing
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())

            item = {
                "stock_number": stock_number,  # Primary Key
                "timestamp": timestamp,
                "stock_number_href": instance.get("stock_number_href", ""),
                "final_bid": instance.get("Final_Bid", "N/A"),
                "vin_display": instance.get("VINDisplay", ""),
                "details": json.dumps(instance.get("Details", {})),  # Convert dictionary to JSON string
                "images": json.dumps(instance.get("Images", [])),  # Convert list to JSON string
                "html_s3_url": f"https://auction-assets.s3.amazonaws.com/{stock_number}.html"  # Placeholder S3 URL
            }

            table.put_item(Item=item)
            print(f"✅ Uploaded: {stock_number}")

    except (NoCredentialsError, PartialCredentialsError):
        print("❌ AWS credentials are missing or incorrect.")
    except Exception as e:
        print(f"❌ Error uploading to DynamoDB: {e}")

# Run upload function
upload_json_to_dynamodb()
