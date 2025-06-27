#!/usr/bin/env python3
"""
Advanced OCR Converter for Auction Data Processing

This script:
1. Queries DynamoDB for items where htmlgen_passed = true AND converter_passed = false
2. Downloads HTML files from S3
3. Captures screenshots using Selenium/Chrome
4. Performs advanced OCR using TrOCR model
5. Updates DynamoDB with OCR results
6. Cleans up local files

Requirements:
- Chrome/ChromeDriver installed
- CUDA-capable GPU (optional, falls back to CPU)
- Internet connection for model download
"""

import os
import sys
import time
import cv2
import numpy as np
import json
import gc  # For garbage collection
import boto3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from PIL import Image, ImageEnhance, ImageFilter
from transformers import TrOCRProcessor, VisionEncoderDecoderModel
import torch
import shutil  # <-- Added for copying files
import tempfile

def restart_script():
    """
    Restart this script by re-executing the Python interpreter
    with the same arguments.
    """
    print("🔄 Restarting script...")
    python = sys.executable
    os.execv(python, [python] + sys.argv)


# Set the directory for output HTML files
html_folder = "/app/data/output_html"
if not os.path.exists(html_folder):
    os.makedirs(html_folder)

# ------------------------------------------------
# Verify required files exist in /app/data/output_html;
# if missing, copy them from the same directory as converter.py
# ------------------------------------------------
script_dir = os.path.dirname(os.path.abspath(__file__))  # Root directory of converter.py
required_files = [
    "style1.css",
    "style2.css",
    "testMatrix.stripe0.png",
    "testMatrix.stripe1.png",
    "testMatrix.stripe2.png",
    "testMatrix.stripe3.png"
]

for f in required_files:
    dest_path = os.path.join(html_folder, f)
    if not os.path.exists(dest_path):
        source_path = os.path.join(script_dir, f)
        if os.path.exists(source_path):
            shutil.copy(source_path, dest_path)
            print(f"✅ Copied '{f}' from '{script_dir}' to '{html_folder}'.")
        else:
            print(f"⚠️ Could not find '{f}' in the script directory: {script_dir}.")

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

# Initialize AWS services
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

# Initialize the advanced OCR model (TrOCR) from Hugging Face Hub
print("🔄 Loading advanced OCR model from Hugging Face Hub...")
try:
    processor = TrOCRProcessor.from_pretrained("microsoft/trocr-base-printed", use_fast=True)
    model = VisionEncoderDecoderModel.from_pretrained("microsoft/trocr-base-printed")
except Exception as e:
    print("❌ Error loading the model:", e)
    exit(1)

device = "cuda" if torch.cuda.is_available() else "cpu"
model.to(device)
print("✅ Advanced OCR model loaded.")

# -------------------------------
# Function to download an HTML file from S3 using the URL stored in DynamoDB
# -------------------------------
def download_html_from_s3(stock_number, html_s3_url):
    local_path = os.path.join(html_folder, f"{stock_number}.html")
    try:
        # Extract bucket name and object key from the URL
        bucket_name = html_s3_url.split("/")[2].split(".")[0]
        object_key = "/".join(html_s3_url.split("/")[3:])
        s3.download_file(bucket_name, object_key, local_path)
        print(f"✅ Downloaded {stock_number}.html from S3 to {local_path}")
        return local_path
    except Exception as e:
        print(f"❌ Error downloading HTML for {stock_number}: {e}")
        return None

# -------------------------------
# Function to capture a screenshot of an HTML file using Selenium
# -------------------------------
def capture_screenshot(html_file):
    print(f"🔄 Starting screenshot capture for {html_file}...")
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920x1080")
    chrome_options.add_argument("--hide-scrollbars")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    # Try to initialize the WebDriver
    try:
        driver = webdriver.Chrome(options=chrome_options)
    except Exception as e:
        msg = str(e)
        if ("tab crashed" in msg
                or "Unable to obtain driver for chrome" in msg
                or "session not created" in msg):
            print(f"🔄 Critical error encountered: {msg}, restarting script...")
            restart_script()
        print(f"❌ Error initializing Chrome driver: {e}")
        return None

    try:
        file_url = "file:///" + os.path.abspath(html_file)
        driver.get(file_url)
        time.sleep(5)

        try:
            text_element = driver.find_element(By.CSS_SELECTOR, "#text-content")
            screenshot_path = "screenshot.png"
            text_element.screenshot(screenshot_path)
            print(f"✅ Captured screenshot for {html_file}.")
        except Exception as e:
            print(f"⚠️ Element not found; using full page screenshot. ({e})")
            screenshot_path = "screenshot.png"
            driver.save_screenshot(screenshot_path)
            print("✅ Full page screenshot captured.")

    except Exception as e:
        msg = str(e)
        if ("tab crashed" in msg
                or "Unable to obtain driver for chrome" in msg
                or "session not created" in msg):
            print(f"🔄 Critical error encountered: {msg}, restarting script...")
            restart_script()
        print(f"❌ Error during screenshot capture: {e}")
        return None

    finally:
        driver.quit()

    if os.path.exists(screenshot_path):
        print("✅ Screenshot saved:", screenshot_path)
        return screenshot_path
    else:
        print(f"❌ Error: Screenshot not saved for {html_file}!")
        return None

# -------------------------------
# Function to preprocess the screenshot image for OCR
# -------------------------------
def preprocess_image(image_path):
    print("🔄 Preprocessing image for OCR...")
    if not os.path.exists(image_path):
        print(f"❌ Error: Image file '{image_path}' not found.")
        return None
    try:
        image = Image.open(image_path).convert("RGB")
        image = image.resize((image.width // 2, image.height // 2), Image.LANCZOS)
        img_cv = np.array(image)
        img_gray = cv2.cvtColor(img_cv, cv2.COLOR_RGB2GRAY)
        img_thresh = cv2.adaptiveThreshold(
            img_gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 31, 15
        )
        contours, _ = cv2.findContours(img_thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = [cnt for cnt in contours if cnt.size > 0]
        if len(contours) == 0:
            print("❌ Error: No valid contours found!")
            return None
        all_points = np.concatenate(contours, axis=0)
        x, y, w, h = cv2.boundingRect(all_points)
        cropped_img = img_gray[y:y+h, x:x+w]
        pil_cropped = Image.fromarray(cropped_img)
        enhancer = ImageEnhance.Contrast(pil_cropped)
        pil_cropped = enhancer.enhance(2)
        pil_cropped = pil_cropped.filter(ImageFilter.SHARPEN)
        pil_cropped = pil_cropped.filter(ImageFilter.EDGE_ENHANCE_MORE)
        pil_cropped = pil_cropped.resize((pil_cropped.width * 2, pil_cropped.height * 2), Image.LANCZOS)
        pil_cropped = pil_cropped.filter(ImageFilter.MedianFilter(size=3))
        processed_image = np.array(pil_cropped)
        print("✅ Image preprocessing complete.")
        return processed_image
    except Exception as e:
        print(f"❌ Error processing image: {e}")
        return None

# -------------------------------
# Function to perform advanced OCR on the preprocessed image
# -------------------------------
def perform_advanced_ocr(image_array):
    print("🔄 Performing advanced OCR...")
    try:
        pil_image = Image.fromarray(image_array).convert("RGB")
        pixel_values = processor(images=pil_image, return_tensors="pt").pixel_values.to(device)
        generated_ids = model.generate(pixel_values)
        extracted_text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
        print(f"✅ OCR result: {extracted_text[:100]}...")
        return extracted_text.strip()
    except Exception as e:
        print(f"❌ Error performing OCR: {e}")
        return None

# -------------------------------
# Main continuous processing loop:
# - Query DynamoDB for items where htmlgen_passed is true and converter_passed is false.
# - Download the corresponding HTML file from S3.
# - Process the file (capture screenshot, preprocess, OCR).
# - Update DynamoDB with the OCR result and set converter_passed = true.
# - Delete the downloaded HTML file from local and from S3.
if __name__ == "__main__":
    while True:
        try:
            # Query only items where htmlgen_passed = true AND converter_passed = false using pagination
            scan_kwargs = {
                "FilterExpression": "htmlgen_passed = :true_val AND converter_passed = :false_val",
                "ExpressionAttributeValues": {
                    ":true_val": True,
                    ":false_val": False
                }
            }
            items = []
            done = False
            last_evaluated_key = None
            while not done:
                if last_evaluated_key:
                    scan_kwargs["ExclusiveStartKey"] = last_evaluated_key
                response = table.scan(**scan_kwargs)
                items.extend(response.get("Items", []))
                last_evaluated_key = response.get("LastEvaluatedKey")
                if not last_evaluated_key:
                    done = True

            if not items:
                print("⏳ No items to process. Waiting...")
                time.sleep(5)
                continue

            for item in items:
                stock_number = item.get("stock_number")
                html_s3_url = item.get("html_s3_url")
                if not stock_number or not html_s3_url:
                    print("⚠️ Skipping item with missing stock_number or html_s3_url.")
                    continue

                # Step 1: Download the HTML file from S3
                local_html = download_html_from_s3(stock_number, html_s3_url)
                if not local_html:
                    continue

                # Step 2: Capture a screenshot from the downloaded HTML file
                screenshot_path = capture_screenshot(local_html)
                if not screenshot_path:
                    continue

                # Step 3: Preprocess the screenshot for OCR
                processed_img = preprocess_image(screenshot_path)
                if processed_img is None:
                    continue

                # Step 4: Perform OCR on the processed image
                ocr_result = perform_advanced_ocr(processed_img)
                if ocr_result is None:
                    continue

                 # Step 5: Persist OCR result back into DynamoDB
                try:
                    table.update_item(
                        Key={ "stock_number": stock_number },
                        UpdateExpression="SET ocr_result = :ocr, converter_passed = :ok",
                        ExpressionAttributeValues={
                            ":ocr": ocr_result,
                            ":ok": True
                        }
                    )
                    print(f"✅ DynamoDB updated for {stock_number}: ocr_result stored, converter_passed set to true")
                except Exception as e:
                    print(f"❌ Failed to update DynamoDB for {stock_number}: {e}")
                    # decide whether to continue or restart; here we skip to next item
                    continue

                # (Optionally) Step 6: delete local HTML and screenshot to free space
                try:
                    os.remove(local_html)
                    os.remove(screenshot_path)
                    print(f"🧹 Cleaned up local files for {stock_number}")
                except Exception:
                    pass
        except Exception as e:
            print(f"❌ Unhandled exception in main loop: {e}")
            # On any unexpected exception, restart the script
            restart_script()
