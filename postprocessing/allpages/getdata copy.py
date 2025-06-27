import time
import random
import os
import re
import requests
import json
import boto3
from botocore.exceptions import ClientError
from pyvirtualdisplay import Display
from bs4 import BeautifulSoup
from undetected_chromedriver import Chrome, ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

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

# ──────────────────────────────────────────────────────────────
# AWS Resources
# ──────────────────────────────────────────────────────────────
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

# ──────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────
OUTPUT_DIR = "./data"
os.makedirs(OUTPUT_DIR, exist_ok=True)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:108.0) Gecko/20100101 Firefox/108.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.140 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.140 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edg/116.0.1938.69"
]

# If your relative image paths ("/prod/images/...") need a domain:
BASE_IMAGE_URL = "https://www.iaai.com"

# ──────────────────────────────────────────────────────────────
# SHARDING CONFIG (Environment Variables)
# ──────────────────────────────────────────────────────────────
SHARD_ID = int(os.getenv("SHARD_ID", "0"))     # 0-based index of this worker
TOTAL_SHARDS = int(os.getenv("TOTAL_SHARDS", "1")) # total number of parallel workers

# ---------------------------------------
# Chrome Options & Virtual Display Setup
# ---------------------------------------
def get_chrome_options():
    """
    Returns a fresh instance of ChromeOptions with the desired settings.
    """
    options = ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-web-security")
    options.add_argument("--allow-running-insecure-content")
    options.add_argument("--incognito")
    options.add_argument(f"user-agent={random.choice(USER_AGENTS)}")
    return options

# -------------------------
# Utility Functions
# -------------------------
def sanitize_label(label):
    """
    Replace all non-alphanumeric/underscore characters with underscores.
    For instance, "Stock #" => "Stock_".
    """
    return re.sub(r"[^A-Za-z0-9_]+", "_", label)

def upload_bytes_to_s3(byte_data, s3_key):
    """
    Upload given bytes to S3 under s3_key, returning the object URL.
    """
    s3.put_object(
        Bucket=S3_BUCKET_NAME,
        Key=s3_key,
        Body=byte_data
        # Optionally add ACL='public-read' if your bucket supports it.
    )
    return f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{s3_key}"

def parse_vehicle_data(html_content):
    """
    Parses HTML to extract fields from <li class="data-list__item">
    and thumbnail images from <img.vehicle-image__thumb>.
    
    Additionally checks <h1 class="heading-2 heading-2-semi mb-0 rtl-disabled">
    for vehicle title.
    
    Returns:
      {
        "fields": { "Year": "...", "VIN": "...", ... },
        "images": ["img_url_1", "img_url_2", ...]
      }
    """
    soup = BeautifulSoup(html_content, "html.parser")
    data_fields = {}
    
    # Check for vehicle title
    title_elem = soup.select_one("h1.heading-2.heading-2-semi.mb-0.rtl-disabled")
    if title_elem:
        data_fields["VehicleTitle"] = title_elem.get_text(strip=True)
    
    for li in soup.select("li.data-list__item"):
        label_elem = li.select_one("span.data-list__label")
        value_elem = li.select_one("span.data-list__value")
        if label_elem and value_elem:
            label_text = label_elem.get_text(strip=True)
            if label_text.endswith(":"):
                label_text = label_text[:-1].strip()
            value_text = value_elem.get_text(strip=True)
            data_fields[label_text] = value_text

    images = []
    for img_tag in soup.select("img.vehicle-image__thumb"):
        src = img_tag.get("src")
        if src:
            images.append(src)
    
    return {
        "fields": data_fields,
        "images": images
    }

def fetch_threesixty_images(partition_key):
    """
    Fetches up to 12 images from:
    https://mediaretriever.iaai.com/api/ThreeSixtyImageRetriever?tenant=iaai...
    
    Returns list of tuples (order, image_bytes).
    """
    downloaded = []
    for i in range(1, 13):
        url = (
            f"https://mediaretriever.iaai.com/api/ThreeSixtyImageRetriever?"
            f"tenant=iaai&partitionKey={partition_key}&imageOrder={i}"
        )
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                content_type = r.headers.get("Content-Type", "")
                if "image" in content_type:
                    downloaded.append((i, r.content))
        except Exception:
            pass
    return downloaded

def get_all_items(table):
    """
    Retrieves all items matching the filter by iterating through the paginated scan responses.
    """
    items = []
    response = table.scan(
        FilterExpression="attribute_not_exists(allpagedata_passed) OR allpagedata_passed = :val",
        ExpressionAttributeValues={":val": False},
        ProjectionExpression="stock_number, stock_number_href, allpagedata_passed"
    )
    items.extend(response.get("Items", []))
    
    while "LastEvaluatedKey" in response:
        response = table.scan(
            FilterExpression="attribute_not_exists(allpagedata_passed) OR allpagedata_passed = :val",
            ExpressionAttributeValues={":val": False},
            ExclusiveStartKey=response["LastEvaluatedKey"],
            ProjectionExpression="stock_number, stock_number_href, allpagedata_passed"
        )
        items.extend(response.get("Items", []))
    
    return items

# -------------------------
# Main Processing Flow
# -------------------------
def main():
    """
    1. Query DynamoDB for items with allpagedata_passed != True
    2. Shard the list so this container only works on its slice
    3. Start a virtual display and launch Chrome once
    4. For each item, atomically "claim" it, then scrape and upload data
    5. Update DynamoDB with the processed data attributes and mark as passed.
    """
    # Start virtual display for headless operation
    display = Display(visible=0, size=(1920, 1080))
    display.start()
    print("[*] Virtual display started.")

    # 1) Get items needing processing
    all_items = get_all_items(table)

    # 2) Shard the items deterministically using hash(stock_number)
    items_to_process = [
        it for it in all_items
        if (hash(it["stock_number"]) % TOTAL_SHARDS) == SHARD_ID
    ]

    if not items_to_process:
        print(f"[!] No items found for shard {SHARD_ID}/{TOTAL_SHARDS}.")
        display.stop()
        return

    # 3) Prepare a single Chrome instance with our custom options
    print(f"[*] Launching undetected Chrome with custom options for shard {SHARD_ID}...")
    driver = Chrome(options=get_chrome_options())

    try:
        # Optional: Navigate to a neutral page first
        driver.get("https://www.google.com")
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(random.uniform(2, 5))
        
        # Process each item
        for idx, item in enumerate(items_to_process, start=1):
            stock_number = item.get("stock_number")
            if not stock_number:
                print(f"[-] Item missing 'stock_number'. Skipping: {item}")
                continue

            detail_url = item.get("stock_number_href")
            if not detail_url:
                print(f"[-] Item missing 'detail_url'. Skipping: {item}")
                continue

            # ----- Atomic claim so only one worker processes this row -----
            try:
                table.update_item(
                    Key={"stock_number": stock_number},
                    UpdateExpression="SET in_progress = :true",
                    ConditionExpression="attribute_not_exists(in_progress)",
                    ExpressionAttributeValues={":true": True}
                )
            except ClientError as ce:
                if ce.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    # Someone else already claimed it
                    continue
                raise

            print(f"\n[{idx}] Processing stock_number={stock_number}, URL={detail_url}")
            try:
                driver.get(detail_url)
                WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                # extra wait for vehicle data / images to load
                WebDriverWait(driver, 12).until(
                    lambda d: d.find_elements(By.CSS_SELECTOR, "li.data-list__item") or
                              d.find_elements(By.CSS_SELECTOR, "img.vehicle-image__thumb")
                )
                time.sleep(1)
            except Exception as e:
                print(f"[!] Error navigating to {detail_url}: {e}")
                # release claim so another shard can retry
                table.update_item(Key={"stock_number": stock_number},
                                  UpdateExpression="REMOVE in_progress")
                continue

            # Grab page HTML for parsing
            html_content = driver.page_source
            parsed_data = parse_vehicle_data(html_content)
            fields_dict = parsed_data["fields"]
            normal_img_urls = parsed_data["images"]

            # Skip if nothing parsed and release claim
            if not fields_dict and not normal_img_urls:
                table.update_item(Key={"stock_number": stock_number},
                                  UpdateExpression="REMOVE in_progress")
                print(f"[!] No data found for {stock_number}, released for retry.")
                continue

            # Extract partition key from detail_url
            partition_match = re.search(r"/VehicleDetail/(\d+)~US", detail_url)
            partition_key = partition_match.group(1) if partition_match else None

            # ====== Process Normal Images ======
            s3_normal_image_links = []
            for i, img_url in enumerate(normal_img_urls, start=1):
                if img_url.startswith("/"):
                    img_url = BASE_IMAGE_URL + img_url

                if not re.match(r"^https?://", img_url):
                    print(f"[!] Invalid URL: {img_url}")
                    continue

                # Replace width and height parameters in URL if present
                enhanced_img_url = re.sub(
                    r"width=\d+&height=\d+",
                    "width=845&height=633",
                    img_url
                )

                print(f"[INFO] Original URL: {img_url}")
                print(f"[INFO] Enhanced URL: {enhanced_img_url}")

                try:
                    r = requests.get(enhanced_img_url, timeout=15)
                    if r.status_code == 200:
                        s3_key = f"normal-images/{partition_key}/normal_{i}.jpg"
                        uploaded_url = upload_bytes_to_s3(r.content, s3_key)
                        s3_normal_image_links.append(uploaded_url)
                    else:
                        print(f"[!] Could not download {enhanced_img_url}, HTTP {r.status_code}")
                except Exception as e:
                    print(f"[!] Failed to download {enhanced_img_url}: {e}")

            # ====== Process 360 Images ======
            s3_threesixty_links = []
            if partition_key:
                raw_360 = fetch_threesixty_images(partition_key)
                for order_idx, image_bytes in raw_360:
                    s3_key = f"three-sixty/{partition_key}/threeSixty_{order_idx}.jpg"
                    try:
                        uploaded_url = upload_bytes_to_s3(image_bytes, s3_key)
                        s3_threesixty_links.append(uploaded_url)
                    except Exception as e:
                        print(f"[!] Failed to upload 360 image #{order_idx}: {e}")

            # =========== Update DynamoDB ===========
            sanitized_fields = {}
            for label, val in fields_dict.items():
                clean_label = sanitize_label(label)
                sanitized_fields[clean_label] = val

            payload = {
                "stock_number": stock_number,
                "fields": sanitized_fields,
                "images": s3_normal_image_links,
                "three_sixty": s3_threesixty_links
            }

            try:
                table.update_item(
                    Key={"stock_number": stock_number},
                    UpdateExpression="""
                        REMOVE in_progress
                        SET
                          allpagedata_fields = :flds,
                          allpagedata_images = :imgs,
                          allpagedata_3sixty = :threesixty,
                          allpagedata_passed = :passed
                    """,
                    ExpressionAttributeValues={
                        ":flds": sanitized_fields,
                        ":imgs": s3_normal_image_links,
                        ":threesixty": s3_threesixty_links,
                        ":passed": True
                    }
                )
                print(f"[+] DynamoDB updated for stock_number={stock_number}")
            except Exception as e:
                print(f"[!] DynamoDB update error for stock_number={stock_number}: {e}")
                payload["error"] = str(e)

            # ---- JSON log of what was appended ----
            print(json.dumps(payload, ensure_ascii=False))
          
    except Exception as e:
        print(f"[!] Main flow error: {e}")
    finally:
        driver.quit()
        display.stop()
        print(f"[*] Finished processing shard {SHARD_ID}/{TOTAL_SHARDS} and stopped the virtual display.")

if __name__ == "__main__":
    main()
