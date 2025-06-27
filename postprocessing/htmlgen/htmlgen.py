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

# Initialize AWS Clients
s3 = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION
)
dynamodb = boto3.resource(
    "dynamodb",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION
)
table = dynamodb.Table(DYNAMODB_TABLE_NAME)

# 🔄 Monitor DynamoDB for new data
print("🔄 Monitoring DynamoDB for new auction data...")

def upload_to_s3(file_name, data, content_type="text/html"):
    """Uploads a file to S3 and returns its URL."""
    try:
        s3.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=file_name,
            Body=data,
            ContentType=content_type
        )
        return f"https://{S3_BUCKET_NAME}.s3.amazonaws.com/{file_name}"
    except Exception as e:
        print(f"❌ Failed to upload {file_name} to S3: {e}")
        return None

while True:
    try:
        # Build scan parameters with LastEvaluatedKey logic
        scan_kwargs = {
            "FilterExpression": "htmlgen_passed = :status",
            "ExpressionAttributeValues": {":status": False}
        }
        items = []
        last_evaluated_key = None
        while True:
            if last_evaluated_key:
                scan_kwargs["ExclusiveStartKey"] = last_evaluated_key
            response = table.scan(**scan_kwargs)
            items.extend(response.get("Items", []))
            last_evaluated_key = response.get("LastEvaluatedKey")
            if not last_evaluated_key:
                break

        for instance in items:
            stock_number = instance.get("stock_number", "default_stock")

            # Fetch details and VINDisplay
            
            vin_display = instance.get("vin_display", "")
            vin_display_html = vin_display if vin_display else "<p>VIN Display Not Available</p>"

            # Build the final HTML content
            html_header = """<!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Styled Auction Page</title>
                
                <!-- External CSS -->
                <link rel="stylesheet" href="./style1.css">
                <link rel="stylesheet" href="./style2.css">

                <!-- Internal CSS -->
                <style>
                    .icon-container {{
                        display: flex; 
                        flex-wrap: nowrap; 
                        gap: 0; 
                    }}       
                    .icon-container i {{
                        display: inline-block;
                    }}
                </style>
            </head>
            <body>
                <div class="icon-container" id="text-content">
            """

            html_footer = """
                </div>
            </body>
            </html>
            """

            final_html = html_header + vin_display_html + html_footer

            # Upload HTML to S3
            html_file_name = f"{stock_number}.html"
            html_url = upload_to_s3(html_file_name, final_html)

            if html_url:
                # ✅ Update DynamoDB: Store S3 URL and set htmlgen_passed to true
                table.update_item(
                    Key={"stock_number": stock_number},
                    UpdateExpression="SET html_s3_url = :url, htmlgen_passed = :status",
                    ExpressionAttributeValues={
                        ":url": html_url,
                        ":status": True
                    }
                )

                print(f"✅ Processed and updated: {stock_number} - HTML uploaded to S3 & DynamoDB updated.")

        time.sleep(5)  # Wait before the next batch

    except KeyboardInterrupt:
        print("🛑 Stopping monitoring process.")
        break
    except Exception as e:
        print(f"❌ Error in monitoring DynamoDB: {e}")
        time.sleep(5)
