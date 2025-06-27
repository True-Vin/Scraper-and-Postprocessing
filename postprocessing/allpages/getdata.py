import random
import re
import requests
import json
import boto3
import itertools
import threading
from botocore.exceptions import ClientError
import os

# -------------------------
# AWS Credentials (from environment)
# -------------------------
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")  # Changed to us-east-1

# -------------------------
# AWS Resources
# -------------------------
S3_BUCKET_NAME = "auctionshtml"  # Must match your actual bucket name
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
table = dynamodb.Table("AuctionData")

# -------------------------
# Constants
# -------------------------
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:108.0) Gecko/20100101 Firefox/108.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.140 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.140 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edg/116.0.1938.69"
]

# -------------------------
# Utility Functions
# -------------------------

def upload_bytes_to_s3(byte_data, s3_key):
    """
    Uploads given bytes to the configured S3 bucket under s3_key, returning the object URL.
    """
    s3.put_object(
        Bucket=S3_BUCKET_NAME,
        Key=s3_key,
        Body=byte_data
    )
    return f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{s3_key}"


def fetch_one_images(one_image_url):
    """
    Sequentially fetches images by incrementing the I-index in one_image_url until none remain.
    """
    downloaded = []
    match = re.search(r"^(.*~I)(\d+)(~.*)$", one_image_url)
    if not match:
        print(f"[!] Could not parse one_image_url: {one_image_url}")
        return downloaded
    prefix, _, suffix = match.groups()

    for i in itertools.count(1):
        url = f"{prefix}{i}{suffix}"
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        print(f"[INFO] Fetching normal image URL: {url}")
        try:
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code == 200 and "image" in r.headers.get("Content-Type", ""):
                downloaded.append(r.content)
            else:
                print(f"[INFO] No more normal images at index {i} (HTTP {r.status_code}).")
                break
        except Exception as e:
            print(f"[!] Error fetching normal image {i}: {e}")
            break
    return downloaded


def fetch_threesixty_images(partition_key):
    """
    Fetches up to 12 360° images using the stock_number as partition_key.
    """
    downloaded = []
    for i in range(1, 13):
        url = (
            f"https://mediaretriever.iaai.com/api/ThreeSixtyImageRetriever?"
            f"tenant=iaai&partitionKey={partition_key}&imageOrder={i}"
        )
        print(f"[INFO] Fetching 360° image URL: {url}")
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200 and "image" in r.headers.get("Content-Type", ""):
                downloaded.append((i, r.content))
            else:
                print(f"[INFO] No 360° image at order {i} (HTTP {r.status_code}).")
        except Exception as e:
            print(f"[!] Error fetching 360° image {i}: {e}")
    return downloaded


def get_all_items(table):
    """
    Scans DynamoDB for items where allpagedata_passed is not true.
    """
    items = []
    response = table.scan(
        FilterExpression="attribute_not_exists(allpagedata_passed) OR allpagedata_passed = :val",
        ExpressionAttributeValues={":val": False},
        ProjectionExpression="stock_number, one_image, allpagedata_passed"
    )
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = table.scan(
            FilterExpression="attribute_not_exists(allpagedata_passed) OR allpagedata_passed = :val",
            ExpressionAttributeValues={":val": False},
            ExclusiveStartKey=response["LastEvaluatedKey"],
            ProjectionExpression="stock_number, one_image, allpagedata_passed"
        )
        items.extend(response.get("Items", []))
    return items


def main():
    """
    Main flow:
      1. Scan for items needing processing
      2. For each item:
         a. Atomically claim it
         b. Fetch and upload normal images
         c. Extract partition_key and fetch/upload 360° images
         d. Download & upload video in separate thread (just rename .mp4)
         e. Update DynamoDB and log
    """
    all_items = get_all_items(table)
    print(f"[*] Retrieved {len(all_items)} items to process.")

    if not all_items:
        print("[!] No items to process. Exiting.")
        return

    for idx, item in enumerate(all_items, start=1):
        stock_number = item.get("stock_number")
        one_image_url = item.get("one_image")
        print(f"\n[{idx}] Processing stock_number={stock_number}")

        if not one_image_url:
            print(f"[-] Missing 'one_image' for {stock_number}. Skipping.")
            continue

        # Atomic claim
        try:
            table.update_item(
                Key={"stock_number": stock_number},
                UpdateExpression="SET in_progress = :true",
                ConditionExpression="attribute_not_exists(in_progress)",
                ExpressionAttributeValues={":true": True}
            )
        except ClientError as ce:
            if ce.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                print(f"[!] Already in progress. Skipping {stock_number}.")
                continue
            else:
                raise

        # Fetch & upload normal images
        normal_images = fetch_one_images(one_image_url)
        s3_normal_links = []
        for i, img_bytes in enumerate(normal_images, start=1):
            s3_key = f"normal-images/{stock_number}/normal_{i}.jpg"
            try:
                url = upload_bytes_to_s3(img_bytes, s3_key)
                s3_normal_links.append(url)
                print(f"[+] Uploaded normal image #{i} to {url}")
            except Exception as e:
                print(f"[!] Upload failed for normal image #{i}: {e}")

        # Extract partition_key (strip '~US') and fetch/upload 360° images
        partition_key = stock_number.split("~")[0]
        three_sixty_images = fetch_threesixty_images(partition_key)
        s3_360_links = []
        for order_idx, img_bytes in three_sixty_images:
            s3_key = f"three-sixty/{partition_key}/threeSixty_{order_idx}.jpg"
            try:
                url = upload_bytes_to_s3(img_bytes, s3_key)
                s3_360_links.append(url)
                print(f"[+] Uploaded 360° image #{order_idx} to {url}")
            except Exception as e:
                print(f"[!] Upload failed for 360° image #{order_idx}: {e}")

        # Download & upload video in separate thread
        video_s3_link = ""

        def fetch_and_upload_video(part_key):
            nonlocal video_s3_link
            base_url = f"https://mediastorageaccountprod.blob.core.windows.net/media/{part_key}_VES-100_1"
            for ext in ["", ".mp4"]:
                url = base_url + ext
                headers = {"User-Agent": random.choice(USER_AGENTS)}
                print(f"[INFO] Fetching video URL: {url}")
                try:
                    r = requests.get(url, headers=headers, timeout=30)
                except Exception as e:
                    print(f"[!] Error fetching video for {part_key}{ext}: {e}")
                    continue
                if r.status_code != 200:
                    print(f"[INFO] No video for {part_key}{ext} (HTTP {r.status_code}).")
                    continue
                # Regardless of content-type, treat as mp4
                print(f"[INFO] Downloaded video content for {part_key}{ext}")
                video_bytes = r.content
                s3_key_vid = f"videos/{part_key}/{part_key}_VES-100_1.mp4"
                try:
                    video_s3_link = upload_bytes_to_s3(video_bytes, s3_key_vid)
                    print(f"[+] Uploaded video to {video_s3_link}")
                except Exception as e:
                    print(f"[!] Upload failed for video {part_key}: {e}")
                return
            print(f"[INFO] No video found for {part_key} at either URL.")

        vid_thread = threading.Thread(target=fetch_and_upload_video, args=(partition_key,))
        vid_thread.start()
        vid_thread.join()

        # Update DynamoDB with images and video link
        try:
            table.update_item(
                Key={"stock_number": stock_number},
                UpdateExpression="REMOVE in_progress SET allpagedata_images = :imgs, allpagedata_3sixty = :threesixty, veh_video_link = :video, allpagedata_passed = :passed",
                ExpressionAttributeValues={
                    ":imgs": s3_normal_links,
                    ":threesixty": s3_360_links,
                    ":video": video_s3_link,
                    ":passed": True
                }
            )
            print(f"[+] DynamoDB updated for {stock_number}")
        except Exception as e:
            print(f"[!] DynamoDB update error for {stock_number}: {e}")

        # JSON log of payload
        payload = {
            "stock_number": stock_number,
            "normal_images_uploaded": len(s3_normal_links),
            "three_sixty_images_uploaded": len(s3_360_links),
            "video_uploaded": bool(video_s3_link)
        }
        print(json.dumps(payload, ensure_ascii=False))

    print("[*] Processing complete.")

if __name__ == "__main__":
    main()
