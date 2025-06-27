#!/usr/bin/env python3
import json
import time
import re
from bs4 import BeautifulSoup

def extract_final_bid(container):
    """Return the final bid text if the auction is closed, else 'N/A'."""
    if container.select_one("h2.media-banner__heading[data-translate='BiddingClosed']"):
        for sel in [
            "h3#soldOthersOverLayMsg",
            "h3#soldCurrentUserOverLayMsg",
            "h3#notSoldOverLayMsg",
            "h3.media-banner__subhead",
        ]:
            el = container.select_one(sel)
            if el:
                txt = el.get_text(strip=True)
                if txt and "N/A" not in txt:
                    return txt
    return "N/A"

def process_snapshot_with_bs4(page_html):
    """
    Parses the HTML snapshot and returns a list of dicts, each containing:
      - stock_number, stock_number_href
      - VINDisplay       (unchanged logic)
      - Final_Bid        (unchanged logic)
      - Veh_detail_fields: list of {field_name: value} via regex on data-bind
      - one_image        (first carousel image URL)
      - timestamp
    Tolerant of missing elements.
    """
    soup = BeautifulSoup(page_html, "html.parser")
    containers = soup.select(".AuctionContainer.event__item")
    results = []

    for c in containers:
        # skip ended auctions
        if c.select_one(".event-empty.event-empty--ended"):
            continue

        # stock number + href
        link = c.select_one(".stock-number a")
        if not link:
            continue
        href = link.get("href", "").strip()
        stock = href.split("/")[-1] if href else ""
        if not stock:
            continue

        entry = {
            "stock_number":      stock,
            "stock_number_href": href,
            "VINDisplay":        None,
            "Final_Bid":         "N/A",
            "Veh_detail_fields": [],
            "one_image":         None,
            "timestamp":         time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        }

        # VIN (unchanged logic)
        vin_elem = c.select_one("span.data-list__value[data-bind*='VINDisplay']")
        if vin_elem:
            val = vin_elem.decode_contents().strip()
            if val:
                entry["VINDisplay"] = val

        # one_image (first in-listing image)
        img = c.select_one("div.media.js-current-vehicle img.carouselImage")
        if img:
            src = img.get("src", "").strip()
            if src:
                entry["one_image"] = src

        # dynamically extract ALL <li class="data-list__item"> fields via regex
        for li in c.select("li.data-list__item"):
            # find the <span class="data-list__value" ...> element
            value_el = li.select_one("span.data-list__value")
            if not value_el:
                continue

            bind_attr = value_el.get("data-bind", "")
            # regex to capture Attributes['FieldName']
            m = re.search(r"Attributes\['(.+?)'\]", bind_attr)
            if not m:
                continue

            field_name = m.group(1)
            field_value = value_el.get_text(strip=True)
            if field_value:
                entry["Veh_detail_fields"].append({field_name: field_value})

        # final bid (unchanged logic)
        entry["Final_Bid"] = extract_final_bid(c)

        results.append(entry)

    return results

if __name__ == "__main__":
    # 1) Read the sample HTML
    with open("samplehtml.html", "r", encoding="utf-8") as f:
        html = f.read()

    # 2) Parse it
    parsed_data = process_snapshot_with_bs4(html)

    # 3) Write out to result.json
    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(parsed_data, f, indent=2, ensure_ascii=False)

    print(f"Parsed {len(parsed_data)} auction container(s). Output → result.json")
