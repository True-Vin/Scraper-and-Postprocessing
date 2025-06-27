#!/usr/bin/env python3
# newdata.py
"""
Fetch IAAI vehicle pages for every AuctionData item where
`newdata_passed` is missing or False, store the parsed fields under
`newdata`, flag the item as processed (only creating `newdata_passed`
if it doesn't already exist), and save the first 11 VIN characters
(asterisks removed) into `unprocessed_vin`. Logs every write.

Runs continuously: when it runs out of items it waits 5 minutes and
then scans the table again.
"""

from __future__ import annotations

import json
import re
import sys
import time
import os
from typing import Dict, Any, List

import boto3
from boto3.dynamodb.conditions import Attr
import requests
from bs4 import BeautifulSoup

# ──────────────────────────────────────────────────────────────
# AWS / DynamoDB CONFIG (Environment Variables)
# ──────────────────────────────────────────────────────────────
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
TABLE_NAME = os.getenv("DYNAMODB_TABLE_NAME", "AuctionData")

# Validate required environment variables
if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
    raise ValueError("AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set as environment variables")

session  = boto3.Session(
    aws_access_key_id     = AWS_ACCESS_KEY_ID,
    aws_secret_access_key = AWS_SECRET_ACCESS_KEY,
    region_name           = AWS_REGION
)
dynamodb = session.resource("dynamodb")
table    = dynamodb.Table(TABLE_NAME)

# ──────────────────────────────────────────────────────────────
# REQUEST HEADERS
# ──────────────────────────────────────────────────────────────
COMMON_HEADERS = {
    "accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
    ),
    "accept-language": "en-US,en;q=0.9",
    "sec-ch-ua": ('"Not(A:Brand";v="99", "Opera GX";v="118", "Chromium";v="133"'),
    "sec-ch-ua-mobile": "?1",
    "sec-ch-ua-platform": '"Android"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": (
        "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Mobile Safari/537.36"
    ),
}


# ──────────────────────────────────────────────────────────────
# PARSING HELPERS
# ──────────────────────────────────────────────────────────────
def _sanitize(label: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]+", "_", label)

def parse_vehicle_page(html: str) -> Dict[str, Any]:
    """
    Scrape the IAAI detail page into:
      {
        "fields": { key: value, ... },
        "images": [ url1, url2, ... ]
      }
    """
    soup = BeautifulSoup(html, "html.parser")
    fields: Dict[str, str] = {}
    images: List[str] = []

    title = soup.select_one("h1.heading-2.heading-2-semi")
    if title:
        fields["VehicleTitle"] = title.get_text(strip=True)

    for li in soup.select("li.data-list__item"):
        lbl = li.select_one("span.data-list__label")
        val = li.select_one("span.data-list__value")
        if lbl and val:
            key = _sanitize(lbl.get_text(strip=True).rstrip(":"))
            fields[key] = val.get_text(strip=True)

    for img in soup.select("img.vehicle-image__thumb"):
        src = img.get("src")
        if src:
            images.append(src)

    return {"fields": fields, "images": images}

# ──────────────────────────────────────────────────────────────
# NETWORK HELPER
# ──────────────────────────────────────────────────────────────
def fetch_html(url: str) -> str:
    resp = requests.get(url, headers=COMMON_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text

# ──────────────────────────────────────────────────────────────
# DYNAMO HELPERS
# ──────────────────────────────────────────────────────────────
def scan_items_to_process() -> List[Dict[str, Any]]:
    """
    Return all items where newdata_passed is missing or False.
    """
    filt = Attr("newdata_passed").not_exists() | Attr("newdata_passed").eq(False)
    items: List[Dict[str, Any]] = []
    last_key = None

    while True:
        params: Dict[str, Any] = {"FilterExpression": filt}
        if last_key:
            params["ExclusiveStartKey"] = last_key
        resp = table.scan(**params)
        items.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break

    return items

def clean_vin(vin_raw: str) -> str:
    """Remove asterisks and return first 11 characters."""
    return vin_raw.replace("*", "")[:11] if vin_raw else ""

def update_item_with_newdata(pk: str, fields: Dict[str, str]) -> None:
    """
    Update the item by setting:
      - newdata
      - newdata_passed = if_not_exists(newdata_passed, :done)
      - unprocessed_vin (first 11 chars of VIN, if present)
    Logs the exact attribute values written.
    """
    update_clauses = [
        "newdata = :nd",
        "newdata_passed = if_not_exists(newdata_passed, :done)"
    ]
    expr_vals: Dict[str, Any] = {
        ":nd": fields,
        ":done": True,
    }

    vin_clean = clean_vin(fields.get("VIN", ""))
    if vin_clean:
        update_clauses.append("unprocessed_vin = :uv")
        expr_vals[":uv"] = vin_clean

    update_expr = "SET " + ", ".join(update_clauses)

    # Log exactly what we'll write
    print(f"[LOG] Updating {pk} with:")
    for placeholder, value in expr_vals.items():
        print(f"       {placeholder} = {value!r}")

    table.update_item(
        Key={"stock_number": pk},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_vals
    )

# ──────────────────────────────────────────────────────────────
# MAIN LOOP
# ──────────────────────────────────────────────────────────────
def main() -> None:
    while True:
        rows = scan_items_to_process()
        if not rows:
            print("✅ No items need newdata. Sleeping 5 minutes...")
            time.sleep(300)
            continue

        for item in rows:
            pk_full = item["stock_number"]
            url = (
                f"https://www.iaai.com/VehicleDetail/{pk_full}"
                if "~" in pk_full
                else f"https://www.iaai.com/VehicleDetail/{pk_full}~US"
            )
            try:
                html   = fetch_html(url)
                parsed = parse_vehicle_page(html)
                fields = parsed["fields"]  # type: ignore

                update_item_with_newdata(pk_full, fields)
                print(f"[✓] Updated {pk_full}\n")

            except Exception as e:
                print(f"[ERROR] {pk_full} → {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
