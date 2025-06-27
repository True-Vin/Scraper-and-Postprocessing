import time
import random
import json
import threading
import boto3
import os
from queue import Queue
from pyvirtualdisplay import Display
from undetected_chromedriver import Chrome, ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
    WebDriverException
)
from urllib3.exceptions import NewConnectionError
from bs4 import BeautifulSoup   # New import for BS4 processing

# AWS configuration
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')  # Changed to us-east-1

# Initialize AWS DynamoDB
dynamodb = boto3.resource(
    "dynamodb",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION
)
# Table for auction data
table = dynamodb.Table("AuctionData")
# (Opened data_ids are no longer stored in DynamoDB)

# Set up virtual display for headless environment
display = Display(visible=0, size=(1920, 1080))
display.start()

# Output directory
OUTPUT_DIR = os.environ.get('OUTPUT_DIR', '/app/data')

os.makedirs(OUTPUT_DIR, exist_ok=True)
RESULT_FILE = os.path.join(OUTPUT_DIR, 'result.json')
SNAPSHOT_DIR = os.path.join(OUTPUT_DIR, "snapshots")
os.makedirs(SNAPSHOT_DIR, exist_ok=True) # <- add this line

# Shared file for opened data_ids
opened_data_ids_file = os.path.join(OUTPUT_DIR, "opened_data_ids.txt")

# Credentials from environment or fallback
EMAIL = os.environ.get('IAAI_EMAIL')
PASSWORD = os.environ.get('IAAI_PASSWORD')

# List of user agents
user_agents = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:108.0) Gecko/20100101 Firefox/108.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.140 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.140 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edg/116.0.1938.69"
]

# Global thread counter and lock for logging all threads
active_thread_count = 0
active_thread_lock = threading.Lock()

def log_with_timestamp(message):
    """Log message with timestamp for better Docker logs."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    print(f"[{timestamp}] {message}")

def start_thread(target, args=(), daemon=False, name=None):
    """
    Helper function to start a thread. Logs when a thread is starting and when it terminates.
    'name' is the thread name; if not provided, it is derived from target.__name__.
    """
    global active_thread_count
    if name is None:
        try:
            name = target.__name__
        except AttributeError:
            name = "UnnamedThread"

    def wrapper(*args, **kwargs):
        try:
            log_with_timestamp(f"Thread '{name}' started: executing target {name}.")
            result = target(*args, **kwargs)
            log_with_timestamp(f"Thread '{name}' terminated normally with result: {result}.")
            return result
        except Exception as e:
            log_with_timestamp(f"Thread '{name}' terminated with exception: {e}.")
            raise
        finally:
            global active_thread_count
            with active_thread_lock:
                active_thread_count -= 1
                log_with_timestamp(f"Thread '{name}' finished. Total active threads: {active_thread_count}")

    with active_thread_lock:
        active_thread_count += 1
        log_with_timestamp(f"Thread '{name}' is starting. Total active threads: {active_thread_count}")
    t = threading.Thread(target=wrapper, args=args, daemon=daemon)
    t.start()
    return t

def get_chrome_options():
    """Return a fresh instance of ChromeOptions with desired settings."""
    options = ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-web-security")
    options.add_argument("--allow-running-insecure-content")
    options.add_argument("--incognito")
    options.add_argument(f"user-agent={random.choice(user_agents)}")
    return options

# Global in-memory set to track which auctions (by data-id) have been opened
opened_data_ids = set()

# Global counter and lock to track the number of open tabs (driver instances)
tabs_opened = 1  # initial tab opened in run_scraper
tabs_opened_lock = threading.Lock()

# Global tracking for containers already being monitored
monitored_containers = set()
monitored_containers_lock = threading.Lock()

# Global memory for BS4 data accumulation
bs4_data_memory = {}
bs4_data_lock = threading.Lock()

def wait_for_loader_to_disappear(driver, timeout=300):
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: not d.find_element(By.CLASS_NAME, "loader__shape").is_displayed()
        )
        print("Loader disappeared.")
    except (TimeoutException, NoSuchElementException):
        print("Loader does not exist or took too long to disappear.")

def slow_typing(text, element, delay=0.5):
    """Simulates human typing by typing one character at a time."""
    for char in text:
        element.send_keys(char)
        time.sleep(random.uniform(0.1, delay))

def append_to_dynamodb(data):
    def upload():
        try:
            item = {
                "stock_number": data["stock_number"],  # Primary Key
                "timestamp": data["timestamp"],
                "stock_number_href": data.get("stock_number_href", ""),
                "final_bid": data.get("Final_Bid", "N/A"),
                "vin_display": data.get("VINDisplay", ""),
                "details": json.dumps(data.get("Details", {})),
                "images": json.dumps(data.get("Images", [])),
                "htmlgen_passed": False,
                "imgserv_passed": False,
                "converter_passed": False
            }
            table.put_item(Item=item)
            print(f"✅ Appended to DynamoDB: {data['stock_number']}")
        except Exception as e:
            print(f"❌ Error appending to DynamoDB: {e}")
    start_thread(target=upload, name="append_to_dynamodb")

def check_if_data_id_opened(data_id):
    """Check the shared file to see if the data_id exists."""
    try:
        if not os.path.exists(opened_data_ids_file):
            return False
        with open(opened_data_ids_file, "r") as f:
            lines = f.read().splitlines()
        return data_id in lines
    except Exception as e:
        log_with_timestamp("Error checking opened_data_ids file: " + str(e))
        return False

def mark_data_id_opened(data_id):
    """Append the data_id to the shared file (no locks used)."""
    try:
        with open(opened_data_ids_file, "a") as f:
            f.write(data_id + "\n")
    except Exception as e:
        log_with_timestamp("Error marking data_id in opened_data_ids file: " + str(e))

def close_sidebar(driver):
    """Attempts to click the close sidebar button if it exists."""
    try:
        close_button = WebDriverWait(driver, 2).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button.main-nav__close.btn--header-close"))
        )
        close_button.click()
        log_with_timestamp("✅ Clicked the close sidebar button.")
        time.sleep(2)
    except (TimeoutException, NoSuchElementException):
        log_with_timestamp("⚠️ Sidebar close button not found or not clickable.")
    except Exception as e:
        log_with_timestamp(f"⚠️ Error clicking close sidebar button: {e}")
        screenshot_path = os.path.join(OUTPUT_DIR, 'close_sidebar_error.png')
        driver.save_screenshot(screenshot_path)

def close_ended_auctions(driver):
    """
    Continuously checks for ended auctions and attempts to close them.
    Exits the loop if the driver is no longer active.
    """
    while True:
        try:
            containers = driver.find_elements(By.CSS_SELECTOR, ".AuctionContainer.event__item")
        except Exception as e:
            log_with_timestamp("close_ended_auctions: driver appears to be closed; exiting thread: " + str(e))
            break
        for container in containers:
            try:
                container.find_element(By.CSS_SELECTOR, ".event-empty.event-empty--ended")
                log_with_timestamp("Auction has ended. Attempting to close the auction...")
                close_button = container.find_element(By.CSS_SELECTOR, "button.btn--header-close.CloseAuction")
                close_button.click()
                log_with_timestamp("Auction closed.")
            except NoSuchElementException:
                continue
            except StaleElementReferenceException as e:
                log_with_timestamp("Stale element reference encountered when closing auction. Continuing")
                continue
            except Exception as e:
                log_with_timestamp("Error closing auction: " + str(e))
                screenshot_path = os.path.join(OUTPUT_DIR, 'close_ended_auctions_error.png')
                driver.save_screenshot(screenshot_path)
                continue
        time.sleep(5)

def ensure_12_containers(driver):
    """
    Ensures there are 12 containers on the page by clicking 'Join More Auctions' and 'Add Item' buttons.
    Uses the add-item button's data-id to track which auctions have been opened.
    If the current driver has reached 12 containers, spawns a new tab for additional ones.
    Before clicking, checks both the in-memory set and the shared file.
    """
    containers = driver.find_elements(By.CSS_SELECTOR, ".AuctionContainer.event__item")
    if len(containers) < 12:
        print(f"Found {len(containers)} containers. Clicking 'Join More Auctions'...")
        try:
            join_more_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, "auctionsbutton"))
            )
            join_more_button.click()
            print("Clicked 'Join More Auctions'.")
        except Exception as e:
            print(f"⚠️ Error clicking 'Join More Auctions': {e}")
            return
        time.sleep(2)
        while len(containers) < 12:
            try:
                add_item_buttons = driver.find_elements(By.CSS_SELECTOR, "button.js-add-item")
                for button in add_item_buttons:
                    try:
                        data_id = button.get_attribute("data-id")
                        mem_opened = data_id in opened_data_ids
                        if mem_opened or check_if_data_id_opened(data_id):
                            continue
                        if len(containers) < 12:
                            button.click()
                            log_with_timestamp("✅ Clicked 'Add Item' button for data-id: " + data_id)
                            opened_data_ids.add(data_id)
                            mark_data_id_opened(data_id)
                            time.sleep(0.5)
                            containers = driver.find_elements(By.CSS_SELECTOR, ".AuctionContainer.event__item")
                            if len(containers) >= 12:
                                break
                        else:
                            log_with_timestamp("Current driver reached 12 containers. Spawning new tab for data-id: " + data_id)
                            with tabs_opened_lock:
                                log_with_timestamp(f"Tabs currently open: {tabs_opened}")
                            start_thread(target=spawn_new_tab_for_button, args=(driver.current_url, data_id), name="spawn_new_tab_for_button")
                    except ElementClickInterceptedException:
                        log_with_timestamp("⚠️ 'Add Item' button not clickable right now...")
                        screenshot_path = os.path.join(OUTPUT_DIR, 'add_items_error.png')
                        driver.save_screenshot(screenshot_path)
                        join_more_button = WebDriverWait(driver, 10).until(
                            EC.element_to_be_clickable((By.ID, "auctionsbutton"))
                        )
                        join_more_button.click()
                        continue
                    except Exception as e:
                        log_with_timestamp("⚠️ Error clicking 'Add Item' button: " + str(e))
                        screenshot_path = os.path.join(OUTPUT_DIR, 'add_items_error.png')
                        driver.save_screenshot(screenshot_path)
                        join_more_button = WebDriverWait(driver, 10).until(
                            EC.element_to_be_clickable((By.ID, "auctionsbutton"))
                        )
                        join_more_button.click()
                        continue
            except Exception as e:
                log_with_timestamp("⚠️ Error finding 'Add Item' buttons: " + str(e))
                break
            containers = driver.find_elements(By.CSS_SELECTOR, ".AuctionContainer.event__item")
        print(f"✅ Now have {len(containers)} containers in this tab.")

def spawn_new_tab_for_button(join_url, target_data_id):
    """
    Spawns a new browser instance (tab/window) to open an auction container corresponding to target_data_id.
    The new driver logs in, navigates to the same join page, clicks the add-item button (if not already clicked),
    then ensures 12 containers and processes them using snapshot-based BS4 extraction along with dedicated container polling.
    Also checks the shared file for the data_id before clicking.
    """
    global tabs_opened
    driver_new = Chrome(options=get_chrome_options())
    try:
        with tabs_opened_lock:
            tabs_opened += 1
            log_with_timestamp(f"New tab spawned. Total tabs opened: {tabs_opened}")
        login_to_iaai(driver_new)
        log_with_timestamp("New tab: Navigating to join page: " + join_url)
        driver_new.get(join_url)
        wait_for_loader_to_disappear(driver_new)
        time.sleep(2)
        buttons = driver_new.find_elements(By.CSS_SELECTOR, "button.js-add-item")
        for button in buttons:
            data_id = button.get_attribute("data-id")
            if data_id == target_data_id:
                if data_id in opened_data_ids or check_if_data_id_opened(data_id):
                    break
                button.click()
                log_with_timestamp("New tab: Clicked 'Add Item' button for data-id: " + data_id)
                opened_data_ids.add(data_id)
                mark_data_id_opened(data_id)
                break
        ensure_12_containers(driver_new)
        
        # Start snapshot-based processing in the new tab using the pool mechanism
        start_snapshot_pool(driver_new)
        # In the new tab loop, keep ensuring containers and spawn dedicated monitoring threads
        while True:
            try:
                ensure_12_containers(driver_new)
                process_new_containers(driver_new)
                time.sleep(5)
            except Exception as loop_error:
                log_with_timestamp("Error during ensure_12_containers loop in new tab: " + str(loop_error))
                time.sleep(5)
    except Exception as e:
        log_with_timestamp("Error in spawn_new_tab_for_button: " + str(e))
    finally:
        try:
            driver_new.quit()
        except Exception as close_error:
            log_with_timestamp("Error closing new tab driver: " + str(close_error))
        with tabs_opened_lock:
            tabs_opened -= 1
            log_with_timestamp(f"Tab closed. Total tabs remaining: {tabs_opened}")

def process_container(driver, container):
    """
    Processes a single auction container.
    """
    try:
        try:
            container.find_element(By.CSS_SELECTOR, ".event-empty.event-empty--ended")
            log_with_timestamp("Auction has ended; skipping (handled by thread).")
            return
        except NoSuchElementException:
            pass
        try:
            leave_auction_msg = container.find_element(By.ID, "LeaveAuctionConfirmationMsg")
            if (leave_auction_msg.is_displayed() and
                "You are already logged into this auction.Press OK to continue" in leave_auction_msg.text):
                cancel_button = container.find_element(
                    By.CSS_SELECTOR,
                    "button.btn.btn--xs.btn--cancel.Action[data-actionname='LeaveAuctionConfirmationCancel'][data-dismiss='modal']"
                )
                cancel_button.click()
                log_with_timestamp("Clicked the 'Cancel' button in the 'Leave Auction Confirmation' dialog.")
                return
        except NoSuchElementException:
            pass

        # Attempt to extract VINDisplay with retries for stale element issues.
        vin_display = ""
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                # Changed CSS selector to match any data-bind containing 'VINDisplay'
                vin_display_element = container.find_element(
                    By.CSS_SELECTOR, "span.data-list__value[data-bind*='VINDisplay']"
                )
                vin_display = vin_display_element.get_attribute("innerHTML").strip()
                break
            except StaleElementReferenceException:
                log_with_timestamp(f"⚠️ Attempt {attempt + 1} for VINDisplay: Stale element reference, retrying...")
                time.sleep(1)
            except Exception as e:
                log_with_timestamp("Error extracting VINDisplay: " + str(e))
                screenshot_path = os.path.join(OUTPUT_DIR, 'vin_display_error.png')
                driver.save_screenshot(screenshot_path)
                break

        try:
            stock_number_element = container.find_element(By.CLASS_NAME, "stock-number")
            try:
                stock_number_link = stock_number_element.find_element(By.TAG_NAME, "a")
            except NoSuchElementException:
                log_with_timestamp("No <a> tag found during polling.")
                return
            stock_number_href = stock_number_link.get_attribute("href")
            stock_number = stock_number_href.split("/")[-1]
            log_with_timestamp(f"Extracted stock number: {stock_number}")
        except StaleElementReferenceException:
            log_with_timestamp("Stale element encountered extracting stock number; skipping container.")
            return

        log_with_timestamp("Polling for 'Bidding Closed' indicator...")
        try:
            WebDriverWait(container, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "h2.media-banner__heading[data-translate='BiddingClosed']"))
            )
        except TimeoutException:
            log_with_timestamp("Bidding Closed indicator did not appear; skipping container.")
            return

        final_bid = "N/A"
        bid_selectors = [
            "h3#soldOthersOverLayMsg",
            "h3#soldCurrentUserOverLayMsg",
            "h3#notSoldOverLayMsg",
            "h3.media-banner__subhead"
        ]
        # --- Modified monitor_container_bid function below now contains a timeout ---
        def monitor_container_bid(driver, container):
            """
            Dedicated polling function for a single container.
            Continuously polls the container until the "Bidding Closed" indicator appears and
            one of the final bid selectors returns a non-"N/A" value.
            If 3.5 seconds pass without a valid capture, the thread will terminate.
            """
            try:
                stock_number_element = container.find_element(By.CLASS_NAME, "stock-number")
                stock_number_link = stock_number_element.find_element(By.TAG_NAME, "a")
                stock_number_href = stock_number_link.get_attribute("href")
                stock_number = stock_number_href.split("/")[-1]
                bid_selectors = [
                    "h3#soldOthersOverLayMsg",
                    "h3#soldCurrentUserOverLayMsg",
                    "h3#notSoldOverLayMsg",
                    "h3.media-banner__subhead"
                ]
                log_with_timestamp(f"Started polling container: {stock_number}")
                start_time = time.time()  # Start time for timeout
                while True:
                    if time.time() - start_time > 3.5:
                        log_with_timestamp(f"Timeout reached for container {stock_number}, terminating polling.")
                        return
                    try:
                        try:
                            container.find_element(By.CSS_SELECTOR, "h2.media-banner__heading[data-translate='BiddingClosed']")
                        except NoSuchElementException:
                            time.sleep(0.01)
                            continue
                        for selector in bid_selectors:
                            try:
                                final_bid_element = container.find_element(By.CSS_SELECTOR, selector)
                                final_bid_text = final_bid_element.text.strip()
                                if final_bid_text and "N/A" not in final_bid_text:
                                    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
                                    data = {
                                        "timestamp": timestamp,
                                        "stock_number_href": stock_number_href,
                                        "stock_number": stock_number,
                                        "Final_Bid": final_bid_text,
                                    }
                                    append_to_dynamodb(data)
                                    log_with_timestamp(f"Final bid captured for container {stock_number}: {final_bid_text}")
                                    return
                            except NoSuchElementException:
                                continue
                        time.sleep(0.01)
                    except StaleElementReferenceException:
                        log_with_timestamp(f"Stale element encountered while polling container {stock_number}. Exiting thread.")
                        return
            except Exception as e:
                log_with_timestamp(f"Error in monitor_container_bid for container {stock_number if 'stock_number' in locals() else ''}: {e}")

        # Start the dedicated polling thread for this container
        start_thread(target=monitor_container_bid, args=(driver, container), daemon=True, name="monitor_container_bid")
        log_with_timestamp("Started dedicated polling thread for container " + stock_number)
    
    except StaleElementReferenceException:
        log_with_timestamp("Stale element reference encountered during container processing. Continuing.")
        return
    except NoSuchElementException:
        log_with_timestamp("Container might be loading, waiting for it to load.")
        return
    except Exception as container_error:
        log_with_timestamp("Error during container processing: " + str(container_error))
        screenshot_path = os.path.join(OUTPUT_DIR, 'error.png')
        driver.save_screenshot(screenshot_path)

def monitor_element_text(driver, element, timeout=30, poll_frequency=0.05):
    """Monitors the text of an element and captures it when it changes."""
    initial_text = element.text
    start_time = time.time()
    while time.time() - start_time < timeout:
        current_text = element.text
        if current_text != initial_text:
            print(f"Text changed to: {current_text}")
            return current_text
        time.sleep(poll_frequency)
    print("Text did not change within the timeout period.")
    return None

def login_to_iaai(driver):
    log_with_timestamp("Opening Google...")
    driver.get("https://www.google.com")
    time.sleep(random.uniform(2, 4))
    log_with_timestamp("Navigating to IAAI...")
    driver.get("https://iaai.com")
    WebDriverWait(driver, 40).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    log_with_timestamp("Clicking on Login...")
    log_in_button = WebDriverWait(driver, 20).until(
        EC.element_to_be_clickable((By.XPATH, "//*[@id='loginRow']/div[1]/a[2]"))
    )
    log_in_button.click()
    time.sleep(random.uniform(1, 2))
    log_with_timestamp("Waiting for login fields...")
    email_field = WebDriverWait(driver, 20).until(EC.visibility_of_element_located((By.ID, "Email")))
    password_field = WebDriverWait(driver, 20).until(EC.visibility_of_element_located((By.ID, "Password")))
    log_with_timestamp("Entering email and password...")
    slow_typing(EMAIL, email_field)
    time.sleep(random.uniform(1, 2))
    slow_typing(PASSWORD, password_field)
    log_with_timestamp("Logging in...")
    login_button = WebDriverWait(driver, 20).until(
        EC.element_to_be_clickable((By.XPATH, "//button[@type='submit' and contains(@class, 'btn-primary')]"))
    )
    login_button.click()
    time.sleep(random.uniform(2, 4))
    log_with_timestamp("Logged in...")
    time.sleep(15)

def process_snapshot_with_bs4(page_html):
    """
    Uses BeautifulSoup to process the provided snapshot (HTML source) and extract auction data.
    Instead of skipping containers with final bid "N/A", it accumulates data per container in memory
    and waits for a subsequent poll to capture the final bid. Once both VINDisplay and a valid Final_Bid
    are available, the data is appended to DynamoDB.
    """
    try:
        soup = BeautifulSoup(page_html, "html.parser")
        containers_bs = soup.select(".AuctionContainer.event__item")
        log_with_timestamp(f"BS4: Found {len(containers_bs)} auction container(s) in snapshot.")
        for container in containers_bs:
            # Skip containers that have ended
            if container.select_one(".event-empty.event-empty--ended"):
                log_with_timestamp("BS4: Auction ended; skipping container.")
                continue

            stock_number_link = container.select_one(".stock-number a")
            if not stock_number_link:
                continue
            stock_number_href = stock_number_link.get("href", "")
            stock_number = stock_number_href.split("/")[-1] if stock_number_href else ""
            if not stock_number:
                continue

            with bs4_data_lock:
                entry = bs4_data_memory.get(stock_number, {
                    "stock_number_href": stock_number_href,
                    "stock_number": stock_number,
                    "VINDisplay": None,
                    "Final_Bid": "N/A",
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
                })

                # Changed selector to match any occurrence of 'VINDisplay'
                vin_display_element = container.select_one("span.data-list__value[data-bind*='VINDisplay']")
               
                if vin_display_element:
                    vin_value = vin_display_element.decode_contents().strip()
                    if vin_value and not entry.get("VINDisplay"):
                        entry["VINDisplay"] = vin_value
                      
                if container.select_one("h2.media-banner__heading[data-translate='BiddingClosed']"):
                    for selector in ["h3#soldOthersOverLayMsg", "h3#soldCurrentUserOverLayMsg", "h3#notSoldOverLayMsg", "h3.media-banner__subhead"]:
                        final_bid_elem = container.select_one(selector)
                        if final_bid_elem:
                            final_bid_text = final_bid_elem.get_text(strip=True)
                            if final_bid_text and "N/A" not in final_bid_text:
                                entry["Final_Bid"] = final_bid_text
                                log_with_timestamp(f"BS4: Updated final bid for {stock_number} using {selector}: {final_bid_text}")
                                break
                    else:
                        log_with_timestamp(f"BS4: Final bid still N/A for {stock_number}; waiting for next poll.")
                else:
                    log_with_timestamp(f"BS4: Bidding Closed indicator not present for {stock_number}; not updating final bid.")

                bs4_data_memory[stock_number] = entry
                ready_to_append = (entry.get("VINDisplay") and entry.get("Final_Bid") != "N/A")
                if ready_to_append:
                    del bs4_data_memory[stock_number]

            if ready_to_append:
                append_to_dynamodb(entry)
                log_with_timestamp("BS4: Data appended for container: " + json.dumps(entry, indent=2))
    except Exception as e:
        log_with_timestamp("BS4: Error processing snapshot: " + str(e))

def monitor_container_bid(driver, container):
    """
    Dedicated polling function for a single container.
    Continuously polls the container until the "Bidding Closed" indicator appears and
    one of the final bid selectors returns a non-"N/A" value.
    """
    try:
        stock_number_element = container.find_element(By.CLASS_NAME, "stock-number")
        stock_number_link = stock_number_element.find_element(By.TAG_NAME, "a")
        stock_number_href = stock_number_link.get_attribute("href")
        stock_number = stock_number_href.split("/")[-1]
        bid_selectors = [
            "h3#soldOthersOverLayMsg",
            "h3#soldCurrentUserOverLayMsg",
            "h3#notSoldOverLayMsg",
            "h3.media-banner__subhead"
        ]
        log_with_timestamp(f"Started polling container: {stock_number}")
        start_time = time.time()  # Start time for timeout
        while True:
            if time.time() - start_time > 3.5:
                log_with_timestamp(f"Timeout reached for container {stock_number}, terminating polling.")
                return
            try:
                try:
                    container.find_element(By.CSS_SELECTOR, "h2.media-banner__heading[data-translate='BiddingClosed']")
                except NoSuchElementException:
                    time.sleep(0.01)
                    continue
                for selector in bid_selectors:
                    try:
                        final_bid_element = container.find_element(By.CSS_SELECTOR, selector)
                        final_bid_text = final_bid_element.text.strip()
                        if final_bid_text and "N/A" not in final_bid_text:
                            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
                            data = {
                                "timestamp": timestamp,
                                "stock_number_href": stock_number_href,
                                "stock_number": stock_number,
                                "Final_Bid": final_bid_text,
                            }
                            append_to_dynamodb(data)
                            log_with_timestamp(f"Final bid captured for container {stock_number}: {final_bid_text}")
                            return
                    except NoSuchElementException:
                        continue
                time.sleep(0.01)
            except StaleElementReferenceException:
                log_with_timestamp(f"Stale element encountered while polling container {stock_number}. Exiting thread.")
                return
    except Exception as e:
        log_with_timestamp(f"Error in monitor_container_bid for container {stock_number if 'stock_number' in locals() else ''}: {e}")

def process_new_containers(driver):
    """
    Scans the page for auction containers and spawns a dedicated polling thread for each container
    that is not already monitored.
    """
    try:
        containers = driver.find_elements(By.CSS_SELECTOR, ".AuctionContainer.event__item")
        for container in containers:
            try:
                stock_number_element = container.find_element(By.CLASS_NAME, "stock-number")
                stock_number_link = stock_number_element.find_element(By.TAG_NAME, "a")
                stock_number_href = stock_number_link.get_attribute("href")
                stock_number = stock_number_href.split("/")[-1]
            except Exception:
                continue
            with monitored_containers_lock:
                if stock_number in monitored_containers:
                    continue
                monitored_containers.add(stock_number)
            # Uncomment the following lines if you want to start dedicated monitoring threads per container:
            # start_thread(target=monitor_container_bid, args=(driver, container), daemon=True, name="monitor_container_bid")
            # log_with_timestamp(f"Started dedicated monitoring for container {stock_number}")
    except Exception as e:
        log_with_timestamp("Error in process_new_containers: " + str(e))

def start_snapshot_pool(driver):
    """
    Producer    : every second dumps driver.page_source to SNAPSHOT_DIR/… .html,
                  then queues the file-path (string << full HTML) → RAM stays tiny.
    Consumers   : open file → BeautifulSoup → process → remove file → done.
    """
    snapshot_queue: Queue[str] = Queue(maxsize=50000)   # queue holds only *paths*
    file_counter = {"val": 0}                          # mutable counter in closure

    def snapshot_producer():
        while True:
            try:
                html     = driver.page_source
                fname    = f"snapshot_{int(time.time()*1000)}_{file_counter['val']:06d}.html"
                fpath    = os.path.join(SNAPSHOT_DIR, fname)
                file_counter["val"] += 1
                with open(fpath, "w", encoding="utf-8") as fp:
                    fp.write(html)
                snapshot_queue.put(fpath)              # paths are tiny → low RAM
                log_with_timestamp(f"[QUEUE] size={snapshot_queue.qsize():4}  wrote {fname}")
            except Exception as e:
                log_with_timestamp(f"Producer error: {e}")
            time.sleep(1)

    def snapshot_consumer():
        while True:
            try:
                fpath = snapshot_queue.get()
                try:
                    with open(fpath, "r", encoding="utf-8") as fp:
                        page_html = fp.read()
                    process_snapshot_with_bs4(page_html)
                finally:
                    # always remove the snapshot to free disk
                    try:
                        os.remove(fpath)
                    except FileNotFoundError:
                        pass
                snapshot_queue.task_done()
            except Exception as e:
                log_with_timestamp(f"Consumer error: {e}")

    start_thread(snapshot_producer, name="snapshot_producer", daemon=True)
    for i in range(6):
        start_thread(snapshot_consumer, name=f"snapshot_consumer_{i+1}", daemon=True)
    
    # start_thread(target=snapshot_producer, name="snapshot_producer")
    # for i in range(6):
    #     start_thread(target=snapshot_consumer, name=f"snapshot_consumer_{i+1}")

def scrape_join_page(join_url):
    driver = Chrome(options=get_chrome_options())
    try:
        login_to_iaai(driver)
        log_with_timestamp("Navigating to join page: " + join_url)
        driver.get(join_url)
        log_with_timestamp("Waiting for the loader to disappear on join page...")
        wait_for_loader_to_disappear(driver)
        start_thread(target=close_ended_auctions, args=(driver,), daemon=True, name="close_ended_auctions")
        # Start the snapshot pool for continuous page capture and BS4 processing
        start_snapshot_pool(driver)
        while True:
            try:
                ensure_12_containers(driver)
                process_new_containers(driver)
                time.sleep(5)
            except Exception as loop_error:
                log_with_timestamp("Error during ensure_12_containers loop on join page: " + str(loop_error))
                screenshot_path = os.path.join(OUTPUT_DIR, 'error.png')
                driver.save_screenshot(screenshot_path)
                time.sleep(5)
    except Exception as e:
        log_with_timestamp("Error in scrape_join_page: " + str(e))
    finally:
        try:
            log_with_timestamp("Closing join page driver for URL: " + join_url)
            driver.quit()
        except Exception as close_error:
            log_with_timestamp("Error closing driver in scrape_join_page: " + str(close_error))

def run_scraper():
    log_with_timestamp("Starting the IAAI scraper...")
    driver = Chrome(options=get_chrome_options())
    try:
        login_to_iaai(driver)
        log_with_timestamp("Navigating to Live Auctions page...")
        driver.get("https://www.iaai.com/LiveAuctionsCalendar")
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        try:
            consent_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//div[@id='truste-consent-content']//button[text()='Accept']"))
            )
            consent_button.click()
            log_with_timestamp("Consent banner accepted.")
            WebDriverWait(driver, 10).until(EC.invisibility_of_element((By.ID, "truste-consent-content")))
        except TimeoutException:
            log_with_timestamp("No consent banner found. Proceeding.")
        log_with_timestamp("Locating the first 'Bid Live' button...")
        first_bid_live_button = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, "//a[@class='btn btn-lg btn-primary btn-block' and .//span[text()='Join Auction']]"))
        )
        if first_bid_live_button:
            href = first_bid_live_button.get_attribute("href")
            log_with_timestamp(f"'Bid Live' button href: {href}")
            auction_schedule_id = href.split("AuctionScheduleId=")[1].split("&")[0]
            log_with_timestamp(f"Extracted AuctionScheduleId: {auction_schedule_id}")
            parent_div = first_bid_live_button.find_element(By.XPATH, "./ancestor::div[@class='table-cell table-cell--status']")
            spans_in_parent_div = parent_div.find_elements(By.XPATH, ".//span")
            span_count = len(spans_in_parent_div)
            log_with_timestamp(f"Number of spans: {span_count}")
            event_ids = ",".join([f"{auction_schedule_id}_{i}" for i in range(1, span_count + 1)])
            new_link = f"https://www.iaai.com/JoinSale?EventId={event_ids}&IsMobile=False&Tenant=US"
            log_with_timestamp(f"Constructed link: {new_link}")
            log_with_timestamp("Navigating to the new link...")
            driver.get(new_link)
            log_with_timestamp("Navigation complete.")
            log_with_timestamp("Waiting for the loader to disappear or not exist...")
            wait_for_loader_to_disappear(driver)
            log_with_timestamp("Loader is gone or not present.")
            start_thread(target=close_ended_auctions, args=(driver,), daemon=True, name="close_ended_auctions")
            # Start the snapshot pool instead of starting a new thread per snapshot
            start_snapshot_pool(driver)
            while True:
                try:
                    ensure_12_containers(driver)
                    process_new_containers(driver)
                    time.sleep(5)
                except Exception as loop_error:
                    log_with_timestamp("Error during ensure_12_containers loop execution: " + str(loop_error))
                    screenshot_path = os.path.join(OUTPUT_DIR, 'error.png')
                    driver.save_screenshot(screenshot_path)
                    time.sleep(5)
        else:
            log_with_timestamp("No 'Bid Live' button found.")
    except Exception as e:
        log_with_timestamp("Error during execution: " + str(e))
        screenshot_path = os.path.join(OUTPUT_DIR, 'error.png')
        driver.save_screenshot(screenshot_path)
    finally:
        try:
            log_with_timestamp("Closing browser...")
            driver.quit()
        except Exception as close_error:
            log_with_timestamp("Error while closing the browser: " + str(close_error))
        display.stop()

if __name__ == "__main__":
    run_scraper()
