#!/usr/bin/env python3
"""
Assign a sequential atomic number (`sr_key`) to every item in <TABLE_NAME>.

• Finds the current maximum sr_key first, so it can resume safely.
• Walks the table with a paginated Scan (≈30 k items is small enough).
• Adds sr_key with a conditional UpdateItem (attribute_not_exists) so
  concurrent runs never overwrite each other.
• Prints a live progress log every 100 successful writes and a summary
  at the end.

⚠️  Put your real credentials in environment variables instead of hard‑
coding them in production.
"""

import os
import datetime
import boto3
from boto3.dynamodb.conditions import Attr

# ─────────────────────────────────────────────────────────
#  CONFIG – edit to taste (Environment Variables)
# ─────────────────────────────────────────────────────────
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
TABLE_NAME = os.getenv("DYNAMODB_TABLE_NAME", "AuctionData")
SR_ATTR_NAME = "sr_key"         # → N (Number) attribute
LOG_EVERY = 100              # print a heartbeat every N writes

# Validate required environment variables
if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
    raise ValueError("AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set as environment variables")

# ─────────────────────────────────────────────────────────
#  DynamoDB client & helpers
# ─────────────────────────────────────────────────────────
dynamodb = boto3.resource(
    "dynamodb",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION,
)
table = dynamodb.Table(TABLE_NAME)


def iso_to_unix(iso_str: str) -> int:
    """Optional helper — kept from the original script."""
    if iso_str.endswith("Z"):
        iso_str = iso_str[:-1] + "+00:00"
    return int(datetime.datetime.fromisoformat(iso_str).timestamp())


def get_max_sr_key() -> int:
    """
    Scan the table (projection only the sr_key) to find the current
    highest sequence value so we can continue counting from there.
    """
    max_seen = 0
    last_evaluated = None
    proj = f"#S"
    names = {"#S": SR_ATTR_NAME}

    while True:
        scan_kwargs = {"ProjectionExpression": proj,
                       "ExpressionAttributeNames": names}
        if last_evaluated:
            scan_kwargs["ExclusiveStartKey"] = last_evaluated

        resp = table.scan(**scan_kwargs)
        for itm in resp.get("Items", []):
            try:
                val = int(itm[SR_ATTR_NAME])
                if val > max_seen:
                    max_seen = val
            except (KeyError, ValueError, TypeError):
                pass

        last_evaluated = resp.get("LastEvaluatedKey")
        if not last_evaluated:
            break

    return max_seen


def main() -> None:
    start_value = get_max_sr_key()
    current_val = start_value
    total_processed = 0
    total_written = 0
    last_evaluated = None

    print(f"[INFO] Highest existing {SR_ATTR_NAME}: {start_value}")
    print("[INFO] Beginning table walk…")

    proj = "stock_number, #T, query_key, #S"
    names = {"#T": "timestamp", "#S": SR_ATTR_NAME}

    while True:
        scan_kwargs = {
            "ProjectionExpression": proj,
            "ExpressionAttributeNames": names
        }
        if last_evaluated:
            scan_kwargs["ExclusiveStartKey"] = last_evaluated

        resp = table.scan(**scan_kwargs)
        for item in resp.get("Items", []):
            total_processed += 1
            if SR_ATTR_NAME in item:
                # already has a value – skip
                continue

            stock_num = item["stock_number"]
            current_val += 1

            try:
                table.update_item(
                    Key={"stock_number": stock_num},
                    UpdateExpression=f"SET #S = :v",
                    ExpressionAttributeNames={"#S": SR_ATTR_NAME},
                    ExpressionAttributeValues={":v": current_val},
                    ConditionExpression="attribute_not_exists(#S)",  # atomic safety
                )
                total_written += 1
                if total_written % LOG_EVERY == 0:
                    print(
                        f"[PROGRESS] {total_written} items updated "
                        f"(last {SR_ATTR_NAME}={current_val})"
                    )
            except Exception as exc:
                print(f"[ERROR] {stock_num}: {exc}")

        last_evaluated = resp.get("LastEvaluatedKey")
        if not last_evaluated:
            break

    print(
        f"✅ Finished.\n"
        f"• Scanned      : {total_processed:,}\n"
        f"• New {SR_ATTR_NAME}s: {total_written:,}\n"
        f"• Highest value: {current_val}"
    )


if __name__ == "__main__":
    main()
