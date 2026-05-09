import requests
import time
import hmac
import hashlib
import asyncio
import websockets
import json
import psycopg2
import subprocess
import certifi
import base64
import uuid
import urllib.parse
from datetime import datetime, timedelta
from cryptography.fernet import Fernet
from decimal import Decimal, ROUND_DOWN
from collections import deque
from cryptography.hazmat.primitives import serialization  # Add these
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes

# Base URL for Binance API
base_url = 'https://api.binance.com'
image_path = '/path/images/click_paid_guide.jpg'

# Endpoint for retrieving chat credentials
endpoint = '/sapi/v1/c2c/chat/retrieveChatCredential'

# Store last n processed UUIDs
processed_uuids = deque(maxlen=500)

# Database connection parameters
DB_HOST = "localhost"
DB_NAME = "binance_db"
DB_USER = "username"
DB_PORT = "5432"  # Default PostgreSQL port

# Add this global variable
RSA_PRIVATE_KEY = None

PYTHON_SCRIPTS_PATH = "/path/python/"

# Function to get the current timestamp in milliseconds
def get_timestamp():
    return int(datetime.now().timestamp() * 1000)

def load_rsa_private_key():
    """Load RSA private key from file"""
    try:
        # Path to your private key
        key_path = "/path/config/private_key.pem"
        
        with open(key_path, 'rb') as key_file:
            private_key = serialization.load_pem_private_key(
                key_file.read(),
                password=None  # Add password here if your key is encrypted
            )
        return private_key
    except Exception as e:
        print(f"Error loading RSA private key: {e}")
        return None

# Function to generate a signature for the Binance API request
def generate_signature(query_string):
    """Generate RSA signature using private key"""
    global RSA_PRIVATE_KEY
    
    if RSA_PRIVATE_KEY is None:
        print("ERROR: RSA private key not loaded")
        return None
        
    try:
        # Sign the query string
        signature = RSA_PRIVATE_KEY.sign(
            query_string.encode('utf-8'),
            padding.PKCS1v15(),
            hashes.SHA256()
        )
        
        # Return base64 encoded signature
        return base64.b64encode(signature).decode('utf-8')
    except Exception as e:
        print(f"RSA signing error: {e}")
        return None

# Function to establish a connection to the database
def get_db_connection():
    conn = psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
        host=DB_HOST,
        port=DB_PORT
    )
    return conn

def get_dict_from_db(query):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(query)
    rows = cursor.fetchall()
    conn.close()
    return rows
    
def load_global_cp_limits():
    global DAILY_BUY_LIMIT, MAX_30D_LIMIT
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT daily_buy_limit_per_cp, max_cp_30d_buy_limit FROM global_limits LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    if row:
        DAILY_BUY_LIMIT = float(row[0])
        MAX_30D_LIMIT = float(row[1])

# Function to load configurations and sensitive information from the encrypted file
def load_configurations():
    # Load the encryption key from the file
    with open('/path/config/encryption_key.key', 'rb') as key_file:
        encryption_key = key_file.read()

    cipher_suite = Fernet(encryption_key)
    
    # Decrypt the database password
    with open('/path/config/config.bin', 'rb') as encrypted_file:
        decrypted_data = cipher_suite.decrypt(encrypted_file.read())
    
    global RSA_PRIVATE_KEY, DB_PASS
    DB_PASS = decrypted_data.decode().split('=')[1].strip("'")

    # Fetch and load the API configurations from the database
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT api_key, base_url, bot_token, chat_id, totp_secret, cp_total_trades, max_cp_buy_limit
        FROM api_config
        LIMIT 1
    """)
    row = cursor.fetchone()
    conn.close()

    if row:
        global api_key, BOT_TOKEN, BASE_URL, CHAT_ID, TOTP_SECRET_KEY, CP_TOTAL_TRADES, MAX_CP_BUY_LIMIT
        
        api_key_encrypted, BASE_URL, bot_token_encrypted, CHAT_ID, totp_encrypted, CP_TOTAL_TRADES, MAX_CP_BUY_LIMIT = row

        api_key = cipher_suite.decrypt(api_key_encrypted.encode()).decode()
        BOT_TOKEN = cipher_suite.decrypt(bot_token_encrypted.encode()).decode()
        TOTP_SECRET_KEY = cipher_suite.decrypt(totp_encrypted.encode()).decode()

        # Load RSA private key from file
        RSA_PRIVATE_KEY = load_rsa_private_key()
        if not RSA_PRIVATE_KEY:
            print("ERROR: Failed to load RSA private key!")
            sys.exit(1)  # Exit if no key loaded

def load_ads_configuration():
    """Fetch and assign values from the ads_configuration table dynamically."""
    ads_query = """
    SELECT coin, daily_buy_limit_per_cp, max_cp_30d_buy_limit
    FROM ads_configuration
    """
    ads_data = get_dict_from_db(ads_query)

    # Populate the maps with data
    for row in ads_data:
        coin, daily_buy_limit_per_cp, max_cp_30d_buy_limit = row
        
        DAILY_BUY_LIMIT_PER_CP[coin] = float(daily_buy_limit_per_cp) if isinstance(daily_buy_limit_per_cp, Decimal) else daily_buy_limit_per_cp
        MAX_CP_30D_BUY_LIMIT[coin] = float(max_cp_30d_buy_limit) if isinstance(max_cp_30d_buy_limit, Decimal) else max_cp_30d_buy_limit
    
def send_telegram_notification(message):
    """Send notification to Telegram bot"""
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    
    payload = {
        'chat_id': CHAT_ID,
        'text': message,
    }
    
    try:
        response = requests.post(url, data=payload, timeout=10)
        result = response.json()
        
        if result.get('ok'):
            print("✅ Telegram notification sent successfully!")
            return True
        else:
            print(f"❌ Telegram API error: {result}")
            return False
    except Exception as e:
        print(f"❌ Failed to send Telegram notification: {e}")
        return False

async def get_chat_credentials():
    # Create a timestamp
    timestamp = get_timestamp()

    # Create the query string with the timestamp
    query_string = f'timestamp={timestamp}'

    # Generate the HMAC SHA256 signature
    signature = generate_signature(query_string)

    # Complete URL
    url = f'{base_url}{endpoint}?{query_string}&signature={signature}'

    # Headers with your API key
    headers = {
        'X-MBX-APIKEY': api_key,
        'clientType': 'web'
    }

    # Send the GET request
    response = requests.get(url, headers=headers)
    data = response.json()

    # Extract listenToken and listenKey
    listen_token = data['data']['listenToken']
    listen_key = data['data']['listenKey']

    return listen_key, listen_token
    
async def get_sell_status_1_orders(order_no):
    page = 1
    rows_per_page = 20

    while True:
        timestamp = get_timestamp()
        query_string = f"timestamp={timestamp}"
        signature = generate_signature(query_string)
        
        url = f"{BASE_URL}/orderMatch/listOrders?{query_string}&signature={signature}"
        headers = {
            'X-MBX-APIKEY': api_key,
            'Content-Type': 'application/json'
        }
        
        body_payload = {
            "page": page,
            "rows": rows_per_page,
            "orderStatusList": [1],  # We only care about status 1 (outstanding orders)
            "tradeType": "SELL"
        }

        try:
            response = requests.post(url, headers=headers, json=body_payload, verify=certifi.where())
            response.raise_for_status()
            orders = response.json()
        except requests.RequestException as e:
            print(f"Error during API request: {e}")
            return False

        outstanding_orders = orders.get('data', [])
        
        current_time = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
        for order in outstanding_orders:
            if order.get('orderNumber') == order_no:
                return True  # Order found with status 1
        
        if not outstanding_orders or len(outstanding_orders) < rows_per_page:
            break

        page += 1

    return False  # Order not found or not matching status 1

async def send_chat_message(websocket, order_no, content, msgType="text"):
    try:
        # Generate a unique UUID for this message
        message_uuid = str(uuid.uuid4())
        # Create the message payload
        message = {
            "type": msgType,
            "uuid": message_uuid, # "170836625",  # Replace with a unique UUID for each message
            "orderNo": order_no,  # Replace with the appropriate order number if needed
            "content": content,  # The content of your message
            "self": True,
            "clientType": "web",
            "createTime": int(time.time() * 1000),  # Current timestamp in milliseconds
            "sendStatus": 0
        }

        # Send the message
        await websocket.send(json.dumps(message))

        # Optionally, receive a response (if applicable)
        response = await websocket.recv()
        # print(f"Received response: {response}")

    except websockets.exceptions.InvalidStatusCode as e:
        print(f"Failed to connect: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")
        
async def send_image(websocket, order_no, image_url):
    try:
        # Generate a unique UUID for this message
        message_uuid = str(uuid.uuid4())

        # Add image dimensions (you may need to read these from the image file if dynamic)
        width = 720  # Example width
        height = 1600  # Example height
        image_type = "jpg"  # Example image type; ensure this matches your file type

        # Create a complete message payload
        message = {
            "type": "image",
            "uuid": message_uuid,
            "orderNo": order_no,
            "imageUrl": image_url,
            "thumbnailUrl": image_url,
            "width": width,
            "height": height,
            "imageType": image_type,
            "createTime": int(time.time() * 1000)
            # Add other fields as required
        }

        # Send the image message
        await websocket.send(json.dumps(message))

        # Optionally, receive a response (if applicable)
        response = await websocket.recv()

    except websockets.exceptions.InvalidStatusCode as e:
        print(f"Failed to connect: {e}")
    except Exception as e:
        print(f"An error occurred while sending the image: {e}")
        
# Function to get the presigned URL and image URL
async def get_presigned_url():
    timestamp = get_timestamp()  # Get current timestamp
    query_string = f"timestamp={timestamp}"  # Prepare the query string
    signature = generate_signature(query_string)  # Generate the signature
    
    # Construct the full URL with query string and signature
    url = f"{BASE_URL}/chat/image/pre-signed-url?{query_string}&signature={signature}"
    
    headers = {
        "X-MBX-APIKEY": api_key,
        'clientType': 'web'
    }

    body_payload = {
        "imageName": image_path
    }

    # Send the POST request
    try:
        response = requests.post(url, headers=headers, json=body_payload, verify=certifi.where())
        orders = response.json()
        
        # Check the response status and print the results
        if response.status_code == 200:
            try:
                # Parse the JSON response
                data = response.json().get('data')
                presigned_url = data.get("uploadUrl")
                image_url = data.get("imageUrl")
                return presigned_url, image_url
            except ValueError:
                print("Error parsing JSON response.")
                return None, None
        else:
            print(f"Error: {response.status_code} - {response.text}")
            return None, None
    except requests.RequestException as e:
        print(f"Error during API request: {e}")
        return None, None
        
async def upload_file_using_presigned_url(presigned_url, file_path):
    with open(file_path, 'rb') as file:
        response = requests.put(presigned_url, data=file)
        if response.status_code == 200:
            print("File uploaded successfully.")
        else:
            print("Failed to upload file.")
            
async def verify_additional_kyc(order_no):
    """Verify the additional KYC for an order."""
    timestamp = get_timestamp()
    query_string = f"timestamp={timestamp}"
    signature = generate_signature(query_string)
    
    url = f"{BASE_URL}/orderMatch/verifiedAdditionalKyc?{query_string}&signature={signature}"
    headers = {
        'X-MBX-APIKEY': api_key,
        'Content-Type': 'application/json'
    }
    
    body_payload = {
        "orderNumber": order_no
    }

    try:
        response = requests.post(url, headers=headers, json=body_payload, verify=certifi.where())
        response.raise_for_status()
        
        # Parse the response JSON
        result = response.json()
        current_time = datetime.now().strftime("%d-%m-%Y %H:%M:%S")

        if result.get("success"):
            kyc_verified = result.get("data", {}).get("kycVerified", False)
            return kyc_verified
        else:
            error_message = result.get("message", "Unknown error")
            return False
    except requests.RequestException as e:
        print(f"Error during verification API request: {e}")
        return False
        
# Insert transaction into the database
async def insert_transaction(details):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Check if order already exists
        cursor.execute("SELECT id FROM sell_transactions WHERE order_number = %s", (details['orderNumber'],))
        if cursor.fetchone():
            print(f"Order {details['orderNumber']} already exists in the database.")
            return

        # Insert transaction
        cursor.execute("""
            INSERT INTO sell_transactions (counterparty_name, asset, order_number, amount, fiat_amount, 
                                           completed_datetime_utc, completed_datetime_plus7)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            details['counterpartyName'],  # Buyer nickname
            details['asset'],            # Asset
            details['orderNumber'],      # Order number
            float(details['amount']),    # Amount
            float(details['fiatAmount']),# Fiat amount
            details['completedDatetimeUTC'],
            details['completedDatetimePlus7']
        ))
        conn.commit()
        print(f"Inserted order {details['orderNumber']} into the database.")
    except Exception as e:
        print(f"Error during database operation: {e}")
    finally:
        if conn:
            conn.close()

# Fetch order status and process data
async def get_sell_status_and_insert(order_no):
    """Check sell status for the specified order and insert transaction details."""
    server_current_time = datetime.utcnow()  # Use server's current time in UTC
    completed_datetime_plus7 = server_current_time + timedelta(hours=7)

    # Fetch order details using getUserOrderDetail endpoint
    order_detail_response = await get_user_order_detail(order_no)

    if not order_detail_response or 'data' not in order_detail_response:
        print("Failed to retrieve order details or invalid response format.")
        return False

    order_data = order_detail_response['data']

    if order_data.get('orderStatus') != 4:  # Check if the order status is 4 (completed)
        print("Order status is not completed.")
        return False

    buyer_name = order_data.get('buyerName', "Unknown")

    order_details = {
        'counterpartyName': buyer_name,
        'asset': order_data.get('asset', "Unknown"),
        'orderNumber': order_no,
        'amount': order_data.get('amount', 0),
        'fiatAmount': order_data.get('totalPrice', 0),
        'completedDatetimeUTC': server_current_time,
        'completedDatetimePlus7': completed_datetime_plus7
    }

    await insert_transaction(order_details)
    return True
    
async def get_user_order_detail(ad_order_no):
    """Get user order details from the API."""
    
    timestamp = get_timestamp()  # Use the same timestamp method
    query_string = f"timestamp={timestamp}"
    signature = generate_signature(query_string)
    
    url = f"{BASE_URL}/orderMatch/getUserOrderDetail?{query_string}&signature={signature}"
    headers = {
        'X-MBX-APIKEY': api_key,
        'Content-Type': 'application/json'
    }
    
    # Prepare the payload for the POST request
    body_payload = {
        "adOrderNo": ad_order_no
    }
    
    try:
        response = requests.post(url, headers=headers, json=body_payload, verify=certifi.where())  # Use POST request
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Error during API request: {e}")
        return None
        
async def get_counterparty_stats(order_number):
    endpoint = "/sapi/v1/c2c/orderMatch/queryCounterPartyOrderStatistic"
    url = base_url + endpoint

    # Generate timestamp and sign it
    timestamp = get_timestamp()
    query_string = f"timestamp={timestamp}"
    signature = generate_signature(query_string)

    full_url = f"{url}?{query_string}&signature={signature}"

    # Headers
    headers = {
        "X-MBX-APIKEY": api_key,
        "Content-Type": "application/json",
        "clientType": "web"  # Required by Binance
    }

    # Request payload
    payload = {
        "orderNumber": str(order_number)
    }

    try:
        response = requests.post(full_url, headers=headers, json=payload)
        if response.status_code == 200:
            res_json = response.json()
            if res_json.get('success') and res_json.get('code') == '000000':
                return res_json['data']
            else:
                print("Error from Binance:", res_json.get('message', 'Unknown error'))
                return None
        else:
            print(f"HTTP Error {response.status_code}: {response.text}")
            return None
    except Exception as e:
        print("Exception occurred:", str(e))
        return None
        
async def check_order_limit(order_no):
    """
    Check if the total transactions for today or the last 30 days 
    exceed the set limits for the given order.

    Parameters:
        order_no (str): The order number to look up.

    Returns:
        (bool, Decimal, Decimal, bool, bool): 
            Tuple:
                - can_proceed (bool): True if both limits are okay.
                - new_total_today (Decimal)
                - new_total_30d (Decimal)
                - exceeded_daily (bool)
                - exceeded_30d (bool)
    """
    total_today = Decimal('0.0')
    total_30d = Decimal('0.0')
    total_price = Decimal('0.0')
    buyer_name = None

    # Fetch order details
    cp_detail = await get_user_order_detail(order_no)
    if cp_detail and 'data' in cp_detail and 'buyerName' in cp_detail['data']:
        buyer_name = cp_detail['data']['buyerName']
        total_price = Decimal(cp_detail['data'].get('totalPrice', '0.0'))
    else:
        return True, total_today + total_price, total_30d + total_price, False, False

    # Get limits
    asset = cp_detail['data'].get('asset', 'USDT')
    daily_limit = Decimal(DAILY_BUY_LIMIT)
    limit_30d = Decimal(MAX_30D_LIMIT)

    # DB connection
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Today's total
        query_today = """
        SELECT SUM(fiat_amount)
        FROM sell_transactions
        WHERE 
            DATE(completed_datetime_plus7) = DATE(NOW() AT TIME ZONE 'UTC' + INTERVAL '7 HOUR')
            AND counterparty_name = %s;
        """
        cursor.execute(query_today, (buyer_name,))
        result_today = cursor.fetchone()
        if result_today and result_today[0]:
            total_today = Decimal(result_today[0])

        # 30-day total
        query_30d = """
        SELECT SUM(fiat_amount)
        FROM sell_transactions
        WHERE 
            completed_datetime_plus7 >= (NOW() AT TIME ZONE 'UTC' + INTERVAL '7 HOUR' - INTERVAL '30 DAY')
            AND completed_datetime_plus7 <= (NOW() AT TIME ZONE 'UTC' + INTERVAL '7 HOUR')
            AND counterparty_name = %s;
        """
        cursor.execute(query_30d, (buyer_name,))
        result_30d = cursor.fetchone()
        if result_30d and result_30d[0]:
            total_30d = Decimal(result_30d[0])

    except Exception as e:
        print(f"Database error: {e}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    # Compute new totals
    new_total_today = total_today + total_price
    new_total_30d = total_30d + total_price

    # Determine limit violations
    exceeded_daily = new_total_today > daily_limit
    exceeded_30d = new_total_30d > limit_30d

    can_proceed = not exceeded_daily and not exceeded_30d

    return can_proceed, new_total_today, new_total_30d, exceeded_daily, exceeded_30d

async def insert_limit_reached_order(order_number, total, is_daily, is_30d):
    try:
        conn = get_db_connection()  # Use your `get_db_connection` function
        cursor = conn.cursor()

        # Get server date and server date + 7 hours
        server_date = datetime.utcnow()
        server_date_plus7 = server_date + timedelta(hours=7)

        # Check if the order_number already exists
        check_query = "SELECT 1 FROM limit_reached_orders WHERE order_number = %s"
        cursor.execute(check_query, (order_number,))
        
        # If the order exists, don't insert
        if cursor.fetchone():
            print(f"Order {order_number} already exists. Skipping insert.")
            return
        
        # Insert into the table with is_daily_reached and is_30d_reached
        insert_query = """
        INSERT INTO limit_reached_orders (
            order_number, 
            date_reached_limit, 
            date_reached_limit_plus7, 
            total_w_cur_order, 
            is_daily_reached, 
            is_30d_reached
        ) VALUES (%s, %s, %s, %s, %s, %s)
        """
        cursor.execute(insert_query, (
            order_number,
            server_date,
            server_date_plus7,
            total,
            is_daily,
            is_30d
        ))

        # Commit the transaction and close the connection
        conn.commit()
        print(f"Order limit reached {order_number} inserted successfully with total {total}.")
        
    except psycopg2.Error as e:
        print(f"Error inserting order: {e}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
            
async def is_order_reached_limit(order_no):
    """
    Check if the order number exists in the limit_reached_orders table 
    and return total_w_cur_order, is_daily_reached, and is_30d_reached.

    Returns:
        (bool, Decimal or None, bool or None, bool or None): 
        - First value: whether the order was found
        - Second value: total_w_cur_order
        - Third value: is_daily_reached
        - Fourth value: is_30d_reached
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        query = """
        SELECT total_w_cur_order, is_daily_reached, is_30d_reached 
        FROM limit_reached_orders 
        WHERE order_number = %s
        """
        cursor.execute(query, (order_no,))
        result = cursor.fetchone()

        if result:
            total_w_cur_order = result[0]
            is_daily_reached = result[1]
            is_30d_reached = result[2]
            return True, total_w_cur_order, is_daily_reached, is_30d_reached
        else:
            return False, None, None, None  # Order not found

    except psycopg2.Error as e:
        print(f"Error checking limit for order {order_no}: {e}")
        return False, None, None, None
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

async def run_script(script_name):
    process = await asyncio.create_subprocess_exec(
        "python", script_name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    
    if stdout:
        print(f"[{script_name} stdout]:\n{stdout.decode()}")
    if stderr:
        print(f"[{script_name} stderr]:\n{stderr.decode()}")
        
def convert_to_usdt_using_convert_api(asset_from, amount):
    asset_to = "USDT"

    try:
        # Step 1: Get Quote
        timestamp = get_timestamp()
        quote_params = {
            'fromAsset': asset_from,
            'toAsset': asset_to,
            'fromAmount': str(amount),
            'walletType': 'FUNDING',  # Or 'SPOT' depending on your wallet
            'timestamp': timestamp
        }
        
        query_string = urllib.parse.urlencode(quote_params)
        signature = generate_signature(query_string)
        quote_params['signature'] = signature
        
        headers = {'X-MBX-APIKEY': api_key}
        quote_response = requests.post(
            f"{base_url}/sapi/v1/convert/getQuote",
            params=quote_params,
            headers=headers
        )
        quote = quote_response.json()
        
        if 'quoteId' not in quote:
            print(f"[QUOTE FAILED] Response: {quote}")
            return False, 0.0, 0.0

        print(f"[QUOTE] ID: {quote['quoteId']} - {quote['fromAmount']} {asset_from} → {quote['toAmount']} USDT")

        # Step 2: Accept Quote
        time.sleep(1)  # Brief delay
        
        accept_params = {
            'quoteId': quote['quoteId'],
            'timestamp': get_timestamp()  # Consistent timestamp function
        }
        
        query_string = urllib.parse.urlencode(accept_params)
        signature = generate_signature(query_string)
        accept_params['signature'] = signature  # Fixed typo
        
        accept_response = requests.post(
            f"{base_url}/sapi/v1/convert/acceptQuote",
            params=accept_params,
            headers=headers
        )
        accept = accept_response.json()

        if accept.get('status') == 'SUCCESS':
            from_amt = float(quote['fromAmount'])
            to_amt = float(quote['toAmount'])
            print(f"[CONVERTED] {from_amt} {asset_from} → {to_amt} {asset_to}")
            return True, from_amt, to_amt
        else:
            print(f"[ACCEPT FAILED] Status: {accept.get('status')}")
            return False, 0.0, 0.0

    except Exception as e:
        print(f"[ERROR] {str(e)}")
        return False, 0.0, 0.0
        
def get_funding_wallet_balance(asset):
    timestamp = get_timestamp()
    query = f"timestamp={timestamp}&asset={asset}"
    signature = generate_signature(query)
    url = f"{base_url}/sapi/v1/asset/get-funding-asset?{query}&signature={signature}"

    headers = {
        "X-MBX-APIKEY": api_key
    }

    try:
        response = requests.post(url, headers=headers)
        data = response.json()
        if isinstance(data, list) and len(data) > 0:
            return float(data[0].get('free', 0.0))
        return 0.0
    except Exception as e:
        print(f"Error fetching funding wallet balance for {asset}: {e}")
        return 0.0
        
def get_lot_size_step(symbol):
    """Fetch LOT_SIZE stepSize from Binance for correct quantity rounding."""
    url = f"{base_url}/api/v3/exchangeInfo?symbol={symbol}"
    try:
        response = requests.get(url)
        data = response.json()
        filters = data["symbols"][0]["filters"]
        for f in filters:
            if f["filterType"] == "LOT_SIZE":
                return float(f["stepSize"])  # Example: 0.01
    except Exception as e:
        print(f"Error getting LOT_SIZE for {symbol}: {e}")
    return 0.01  # fallback
    
def convert_stable_to_usdt_via_spot(asset, price=None, order_no=None):
    try:
        if asset == "USDT":
            return False

        amount = get_funding_wallet_balance(asset)
        if amount <= 0:
            print(f"[SKIPPED] No {asset} balance in Funding wallet.")
            return False

        print(f"[START] Converting {amount:.6f} {asset} to USDT...")

        headers = {'X-MBX-APIKEY': api_key}

        # Step 1: Transfer from FUNDING → SPOT
        transfer_to_spot_params = {
            'type': "FUNDING_MAIN",
            'asset': asset,
            'amount': str(amount),
            'timestamp': get_timestamp()
        }
        query = urllib.parse.urlencode(transfer_to_spot_params)
        transfer_to_spot_params['signature'] = generate_signature(query)

        transfer_url = f"{base_url}/sapi/v1/asset/transfer"
        response1 = requests.post(transfer_url, headers=headers, data=transfer_to_spot_params)
        result1 = response1.json()
        print(f"[TRANSFER TO SPOT] {result1}")
        
        if not result1 or "tranId" not in result1:
            print("[FAILED] Transfer to Spot failed.")
            return False
            
        time.sleep(1)

        # Step 2: Place MARKET SELL Order
        symbol = f"{asset}USDT"
        lot_step = get_lot_size_step(symbol)
        rounded_qty = (amount // lot_step) * lot_step
        rounded_qty = float(f"{rounded_qty:.8f}")

        order_params = {
            'symbol': symbol,
            'side': 'SELL',
            'type': 'MARKET',
            'quantity': str(rounded_qty),
            'timestamp': get_timestamp()
        }
        order_query = urllib.parse.urlencode(order_params)
        order_params['signature'] = generate_signature(order_query)

        order_url = f"{base_url}/api/v3/order"
        response2 = requests.post(order_url, headers=headers, data=order_params)
        result2 = response2.json()
        print(f"[MARKET ORDER] {result2}")

        if not result2 or "fills" not in result2:
            print("[FAILED] Market order failed.")
            return False

        # Extract actual filled quantity and USDT received
        filled_qty = 0.0
        usdt_received = 0.0
        price_sum = 0.0
        
        # Track commissions in all currencies
        commissions = {}  # Dictionary to store commissions by asset
        
        if 'fills' in result2:
            for fill in result2['fills']:
                qty = float(fill['qty'])
                fill_price = float(fill['price'])
                filled_qty += qty
                usdt_received += qty * fill_price
                price_sum += fill_price * qty
                
                # Track commission in any currency
                commission_asset = fill.get('commissionAsset')
                if commission_asset:
                    commission_amount = float(fill['commission'])
                    if commission_asset in commissions:
                        commissions[commission_asset] += commission_amount
                    else:
                        commissions[commission_asset] = commission_amount

        filled_qty = round(filled_qty, 8)
        usdt_received = round(usdt_received, 8)
        avg_price = round(price_sum / filled_qty, 8) if filled_qty else 0.0
        
        # Format commission summary for display
        commission_summary = ""
        if commissions:
            commission_parts = []
            for comm_asset, comm_amount in commissions.items():
                commission_parts.append(f"{comm_amount:.8f} {comm_asset}")
            commission_summary = ", ".join(commission_parts)
            print(f"[COMMISSION] Total commissions: {commission_summary}")
        else:
            print("[COMMISSION] No commissions recorded")
            commission_summary = "None"

        # Get additional buy quota from database
        additional_buy_quota = get_additional_buy_quota()
        print(f"[ADDITIONAL BUY QUOTA] Current amount: {additional_buy_quota} USDT")

        # Step 3a: Transfer USDT back to FUNDING (only excess over buy quota)
        transfer_usdt_amount = 0.0
        
        # Calculate total USDT commission to subtract from transfer
        usdt_commission = commissions.get('USDT', 0.0)
        
        if usdt_received > additional_buy_quota:
            transfer_usdt_amount = round(float(Decimal(str(usdt_received)) - additional_buy_quota - Decimal(str(usdt_commission))), 8)

        # There is still USDT leftover after additional quota is fulfilled
        if transfer_usdt_amount > 0:
            time.sleep(1)
            transfer_back_usdt_params = {
                'type': "MAIN_FUNDING",
                'asset': 'USDT',
                'amount': f"{transfer_usdt_amount:.8f}",
                'timestamp': get_timestamp()
            }
            usdt_back_query = urllib.parse.urlencode(transfer_back_usdt_params)
            transfer_back_usdt_params['signature'] = generate_signature(usdt_back_query)

            response3 = requests.post(transfer_url, headers=headers, data=transfer_back_usdt_params)
            result3 = response3.json()
            print(f"[TRANSFER USDT TO FUNDING] {result3}")
        # All USDT amount is filled into additional quota
        else:
            if transfer_usdt_amount <= 0:
                print("[WARNING] No USDT to transfer after accounting for commission and quota.")
            
        # Step 3c: Update additional buy quota in DB
        if additional_buy_quota > 0:
            update_additional_buy_quota(usdt_received, order_no)

        # Step 3b: Transfer leftover asset back to FUNDING
        leftover_asset = round(amount - filled_qty, 8)
        if leftover_asset > 0.0000001:
            transfer_back_asset_params = {
                'type': "MAIN_FUNDING",
                'asset': asset,
                'amount': f"{leftover_asset:.8f}",
                'timestamp': get_timestamp()
            }
            asset_back_query = urllib.parse.urlencode(transfer_back_asset_params)
            transfer_back_asset_params['signature'] = generate_signature(asset_back_query)

            response4 = requests.post(transfer_url, headers=headers, data=transfer_back_asset_params)
            result4 = response4.json()
            print(f"[TRANSFER LEFTOVER {asset} TO FUNDING] {result4}")
        else:
            print(f"[NO LEFTOVER] {asset} used completely.")
            
        diff = round(usdt_received - filled_qty, 8)

        # Use passed-in price if valid, else fallback to average fill price
        reference_price = float(price) if price not in [None, False] else avg_price
        per_usdt_rate = round(diff / filled_qty * reference_price, 8) if filled_qty and reference_price else 0.0

        if asset in ['FDUSD', 'USDC']:
            # Final notification with all commissions
            msg = (
                f"✅ [AUTO-CONVERT SUCCESS]\n"
                f"🔁 {filled_qty:.6f} {asset} → {usdt_received:.6f} USDT\n"
                f"📉 Diff: {diff:.6f} USDT (Rp {per_usdt_rate:.6f} / USDT)\n"
                f"💰 Commission: {commission_summary}\n"
                f"📦 Leftover: {leftover_asset:.6f} {asset}"
            )
        else:
            # Final notification with all commissions
            msg = (
                f"✅ [AUTO-CONVERT SUCCESS]\n"
                f"🔁 {filled_qty:.6f} {asset} → {usdt_received:.6f} USDT\n"
                f"💰 Commission: {commission_summary}\n"
                f"📦 Leftover: {leftover_asset:.6f} {asset}"
            )
        send_telegram_notification(msg)

        return True

    except Exception as e:
        print(f"[ERROR - convert_stable_to_usdt_via_spot] {str(e)}")
        return False
        
def get_additional_buy_quota():
    """Get the current buy amount from additional_quota table."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT amount FROM additional_quota WHERE trade_type = 'buy'")
        row = cursor.fetchone()
        
        if row:
            amount = Decimal(str(row[0]))
            return amount
        else:
            # If no record exists, return 0
            return Decimal('0.0')
            
    except Exception as e:
        print(f"Error getting additional buy quota: {e}")
        return Decimal('0.0')
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
            
def update_additional_buy_quota(usdt_received, order_number=None):
    """
    Update additional_quota.amount for trade_type = 'buy' and log the change
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT amount FROM additional_quota WHERE trade_type = 'buy' FOR UPDATE"
        )
        row = cursor.fetchone()

        if not row:
            print("[QUOTA] No additional buy quota record found.")
            send_telegram_notification("[QUOTA] No additional buy quota record found.")
            return

        old_amount = Decimal(str(row[0]))
        usdt_received_dec = Decimal(str(usdt_received))

        if usdt_received_dec >= old_amount:
            new_amount = Decimal('0.0')
        else:
            new_amount = old_amount - usdt_received_dec

        change_amount = new_amount - old_amount  # Will be negative for deduction

        # Update the quota
        cursor.execute(
            """
            UPDATE additional_quota
            SET amount = %s
            WHERE trade_type = 'buy'
            """,
            (new_amount,)
        )

        # Log the change
        cursor.execute(
            """
            INSERT INTO additional_quota_log 
            (trade_type, old_amount, new_amount, change_amount, change_type, order_number)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            ('buy', old_amount, new_amount, change_amount, 'deduction', order_number)
        )

        conn.commit()
        send_telegram_notification(
            f"[QUOTA UPDATED] Buy quota: {old_amount} → {new_amount}"
        )

    except Exception as e:
        print(f"[ERROR] Failed to update additional buy quota: {e}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

async def mark_order_as_processed(order_no, processed_by="websocket"):
    """Mark order as processed to prevent duplicate handling"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO processed_buy_orders (order_number, processed_by) 
            VALUES (%s, %s)
            ON CONFLICT (order_number) DO NOTHING
        """, (order_no, processed_by))
        
        conn.commit()
    except Exception as e:
        print(f"Error marking order as processed: {e}")
    finally:
        cursor.close()
        conn.close()

async def is_order_processed_in_buy_completed(order_no):
    """Check if order was processed by buy_completed handler"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check in a dedicated tracking table (create it)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS processed_buy_orders (
            order_number VARCHAR(100) PRIMARY KEY,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_by VARCHAR(50)  -- 'websocket' or 'quick_receive_checker'
        )
    """)
    
    cursor.execute(
        "SELECT 1 FROM processed_buy_orders WHERE order_number = %s",
        (order_no,)
    )
    
    result = cursor.fetchone() is not None
    cursor.close()
    conn.close()
    return result
    
async def handle_buy_completed_logic(order_no):
    """Execute the same logic as in buy_completed WebSocket handler - FOR USDT BUY QUICK-RECEIVES"""
    try:
        print(f"Processing quick-received USDT buy order: {order_no}")
        
        # Mark as processed first to avoid duplicates
        await mark_order_as_processed(order_no, "quick_receive_checker")
        
        # 1. Get order details
        order_detail = await get_user_order_detail(order_no)
        if not order_detail or 'data' not in order_detail:
            print(f"Failed to get details for order {order_no}")
            send_telegram_notification(f"❌ Failed to get details for quick-received order {order_no}")
            return
        
        order_data = order_detail['data']
        
        # Verify it's a USDT buy order (should always be true for quick receive)
        asset = order_data.get('asset', 'USDT')
        trade_type = order_data.get('tradeType', '')
        
        if asset != 'USDT' or trade_type != 'BUY':
            print(f"Unexpected: Order {order_no} is {trade_type} for {asset}, not USDT BUY")
            return
        
        # 2. Try to send "terima kasih" message via WebSocket
        try:
            # Get fresh WebSocket credentials for chat
            listen_key, listen_token = await get_chat_credentials()
            chat_wss_url = f"wss://im.binance.com:443/chat/{listen_key}?token={listen_token}&clientType=web"
            
            # Connect briefly just to send the message
            async with websockets.connect(chat_wss_url) as websocket:
                await send_chat_message(websocket, order_no, "terima kasih kak😊")
                print(f"Sent chat message for order {order_no}")
        except Exception as e:
            print(f"Could not send chat message for order {order_no}: {e}")
            # Don't fail if we can't send message, continue with processing
        
        # 3. Extract order details for notification
        amount = float(order_data.get('amount', 0))
        commission = float(order_data.get('commission', 0))
        net_amount = amount - commission  # Amount after commission
        total_price = float(order_data.get('totalPrice', 0))
        seller_name = order_data.get('sellerName', 'Unknown')
        
        # 4. Check additional buy quota and transfer if needed
        additional_quota = get_additional_buy_quota()
        quota_used = 0
        
        if additional_quota > 0 and net_amount > 0:
            print(f"Additional buy quota available: {additional_quota} USDT")
            
            # Calculate how much to transfer to SPOT
            transfer_amount = net_amount if net_amount <= additional_quota else additional_quota
            quota_used = transfer_amount
            
            # Transfer USDT from FUNDING to SPOT wallet
            timestamp = get_timestamp()
            transfer_params = {
                'type': "FUNDING_MAIN",
                'asset': 'USDT',
                'amount': str(transfer_amount),
                'timestamp': timestamp
            }
            query = urllib.parse.urlencode(transfer_params)
            transfer_params['signature'] = generate_signature(query)
            
            headers = {'X-MBX-APIKEY': api_key}
            transfer_url = f"{base_url}/sapi/v1/asset/transfer"
            
            try:
                response = requests.post(transfer_url, headers=headers, data=transfer_params)
                result = response.json()
                
                print(f"[QUICK-RECEIVE] Transfer to SPOT result: {result}")
                
                if result and "tranId" in result:
                    # Update additional quota
                    update_additional_buy_quota(net_amount, order_no)
                    print(f"Transferred {transfer_amount} USDT to SPOT wallet")
                else:
                    print(f"Transfer failed: {result}")
                    quota_used = 0
                    
            except Exception as e:
                print(f"Error transferring USDT to SPOT: {e}")
                quota_used = 0
        else:
            print("No additional buy quota to fill or zero amount")
        
        # 5. Run bin.py script (if needed for your processing)
        try:
            await run_script(f"{PYTHON_SCRIPTS_PATH}bin.py")
            print(f"Executed bin.py for order {order_no}")
        except Exception as e:
            print(f"Error running bin.py: {e}")
        
        # 6. Send success notification
        # Format total price as Indonesian Rupiah
        formatted_price = f"{total_price:,.0f}".replace(",", ".")
        
        notification_msg = (
            f"✅ Quick-receive detected & processed!\n"
            f"Order: {order_no}\n"
            f"Seller: {seller_name}\n"
            f"USDT Amount: {net_amount:.2f}\n"
            f"Total Price: Rp {formatted_price}"
        )
        
        if quota_used > 0:
            notification_msg += f"\nAdditional Quota Used: {quota_used:.2f} USDT"
        
        send_telegram_notification(notification_msg)
        
        print(f"Successfully processed quick-receive for USDT buy order {order_no}")
        
    except Exception as e:
        error_msg = f"❌ Error processing quick-receive for order {order_no}: {str(e)}"
        print(error_msg)
        send_telegram_notification(error_msg)
        
async def check_quick_received_buy_orders():
    """More efficient checker with time tracking"""
    print("Efficient quick-receive checker for USDT buy orders started...")
    
    # Track the last time we checked (initialize to 7 days ago)
    # last_check_time = int((datetime.now() - timedelta(days=7)).timestamp() * 1000)
    
    while True:
        try:
            # Check every 90 seconds
            await asyncio.sleep(90)
            
            current_time = int(datetime.now().timestamp() * 1000)
            check_from_time = int((datetime.now() - timedelta(days=3)).timestamp() * 1000)
            
            # # For the first run, check last 7 days
            # # After that, check since last check (plus 1 minute buffer)
            # if last_check_time == int((datetime.now() - timedelta(days=7)).timestamp() * 1000):
                # # First run: query last 7 days
                # check_from_time = last_check_time
                # print(f"First run: Checking USDT buy orders from last 7 days ({datetime.fromtimestamp(check_from_time/1000)})")
            # else:
                # # Subsequent runs: check since last check (plus 1 minute buffer)
                # check_from_time = last_check_time - 60000  # 1 minute buffer
                # print(f"Checking USDT buy orders since {datetime.fromtimestamp(check_from_time/1000)}")
            
            all_orders = []
            page = 1
            
            while True:
                # Get recent completed buy orders
                timestamp = get_timestamp()
                query_string = f"timestamp={timestamp}"
                signature = generate_signature(query_string)
                
                url = f"{BASE_URL}/orderMatch/listOrders?{query_string}&signature={signature}"
                headers = {
                    'X-MBX-APIKEY': api_key,
                    'Content-Type': 'application/json'
                }
                
                # Updated body_payload according to the endpoint specification
                body_payload = {
                    "tradeType": "BUY",
                    "orderStatusList": [4],  # Assuming 4 is completed status
                    "page": page,
                    "rows": 20,
                    "startDate": check_from_time,  # Changed from startTime to startDate
                    "endDate": current_time,       # Changed from endTime to endDate
                    "asset": "USDT"                # Added asset filter for USDT
                }
                
                # Optional: Remove None/empty values
                body_payload = {k: v for k, v in body_payload.items() if v is not None}
                
                response = requests.post(url, headers=headers, json=body_payload)
                data = response.json()
                
                if not data.get('success'):
                    print(f"API error: {data.get('message')}")
                    break
                
                page_orders = data.get('data', [])
                
                if not page_orders:
                    break
                
                # Filter for USDT orders (though we already filtered in request)
                usdt_orders = [order for order in page_orders if order.get('asset') == 'USDT']
                all_orders.extend(usdt_orders)
                
                if len(page_orders) < 20:
                    break
                
                page += 1
                await asyncio.sleep(0.5)
            
            # Update last check time for next iteration
            last_check_time = current_time
            
            # if all_orders:
                # print(f"Found {len(all_orders)} new USDT buy orders since last check")
            
            # Process unprocessed orders
            processed_count = 0
            for order in all_orders:
                order_no = order.get('orderNumber')
                
                if not await is_order_processed_in_buy_completed(order_no):
                    print(f"Found unprocessed USDT buy order: {order_no}")
                    await handle_buy_completed_logic(order_no)
                    processed_count += 1
                    
                    await asyncio.sleep(0.5)
            
            if processed_count > 0:
                send_telegram_notification(
                    f"🔄 Quick-receive checker processed {processed_count} order(s)"
                )
            
        except Exception as e:
            print(f"Error in quick-receive checker: {e}")
            # Send error notification
            send_telegram_notification(f"❌ Error in quick-receive checker: {str(e)}")
            await asyncio.sleep(180)
            
async def listen_for_messages():
    while True:  # Keep reconnecting in case of connection closure
        try:
            # Get chat credentials
            listen_key, listen_token = await get_chat_credentials()
            chat_wss_url = f"wss://im.binance.com:443/chat/{listen_key}?token={listen_token}&clientType=web"
                          
            async with websockets.connect(chat_wss_url) as websocket:
                print("WebSocket connection established.")

                while True:
                    try:
                        message = await websocket.recv()
                        
                        try:
                            message_data = json.loads(message)  # Parse the incoming JSON message
                        except json.JSONDecodeError:
                            print("Failed to decode JSON message.")
                            continue

                        # Skip statistics messages
                        if message_data.get('type') == 'statistics':
                            continue

                        print(f"Received message: {message_data}")
                        uuid = message_data.get('uuid')

                        if message_data.get('type') == 'image' and not message_data.get('self', True):
                            if uuid in processed_uuids:
                                print(f"Duplicate image uuid {uuid}, skipping.")
                                continue  # Already processed this image

                            processed_uuids.append(uuid)  # Mark this uuid as processed
                            
                            order_no = message_data.get('orderNo')
                            
                            if order_no:
                                # Fetch order details using getUserOrderDetail endpoint
                                order_detail_response = await get_user_order_detail(order_no)
                                additionalKycVerify = -1
                                order_status = -1

                                if order_detail_response and 'data' in order_detail_response:
                                    additionalKycVerify = order_detail_response['data'].get('additionalKycVerify')
                                    order_status = order_detail_response['data'].get('orderStatus')
                                
                                if additionalKycVerify != 1 and order_status == 1:
                                    # Assuming you have a function to send an image:
                                    # await send_image(websocket, image_path, order_no)
                                    
                                    # print ("image sent.hehe")
                                    # Send the image if order status is 1
                                    
                                    presigned_url, image_url = await get_presigned_url()

                                    # if presigned_url and image_url:
                                        # print("Pre-signed URL:", presigned_url)
                                        # print("Image URL:", image_url)
                                    # else:
                                        # print("Failed to get pre-signed URL or image URL.")

                                    await upload_file_using_presigned_url(presigned_url, image_path)
                                    await send_image(websocket, order_no, image_url)
                                    
                                    await send_chat_message(websocket, order_no, "tkn sdh byr kak kl udah trf😊")
                        elif 'content' in message_data:
                            content = message_data['content']
                            if isinstance(content, str):
                                try:
                                    # Check if the content is JSON or just a string
                                    content_data = json.loads(content)
                                    order_no = message_data['orderNo']
                                    stats = await get_counterparty_stats(order_no)
                                    # Check completedOrderNum
                                    total_trades = stats.get("completedOrderNum", 0)
                                    
                                    # Handle liveness_check_complete_maker
                                    if content_data.get('type') == 'liveness_check_complete_maker':
                                        is_limit_reached, total, is_daily_reached, is_30d_reached = await is_order_reached_limit(order_no)
                                        cp_detail = await get_user_order_detail(order_no)
                                        if not is_limit_reached:
                                            load_configurations()
                                            total_price = Decimal(cp_detail['data'].get('totalPrice', '0.0'))
                                            if total_trades <= CP_TOTAL_TRADES and total_price >= MAX_CP_BUY_LIMIT:
                                                await send_chat_message(websocket, order_no, "Halo kak, \nMohon maaf, sesuai kebijakan keamanan kami, order ini belum bisa kami proses. \nMohon bantuannya untuk cancel order dari sisi pembeli ya. \nTerima kasih 🙏")
                                            else:
                                                kyc_verified = await verify_additional_kyc(order_no)
                                                if kyc_verified:
                                                    await send_chat_message(websocket, order_no, "norek sdh ada ya kak, bs liat di halaman order😊\n\nTIDAK menerima transfer dari GO-PAY / Tabungan By JAGO / SHOPEEPAY / OVO / FLIP (refund kena biaya admin)")
                                                    await send_chat_message(websocket, order_no, "readyy kakak, dirilis BOT ON 24 jam😊")
                                        else:
                                            limit = 0.00
                                            if cp_detail and 'data' in cp_detail and 'buyerName' in cp_detail['data']:
                                                asset = cp_detail['data'].get('asset', 'USDT')  # Default to USDT if asset is not provided
                                                
                                            # Ensure total is not None before formatting
                                            total = total or 0.00
                                                
                                            if is_daily_reached:
                                                limit = DAILY_BUY_LIMIT
                                                    
                                                formatted_limit = f"{limit:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                                                formatted_total = f"{total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                                                await send_chat_message(websocket, order_no, f"mohon maaf kak, limit perhari beli nya hanya Rp {formatted_limit}, kk hr ini ud beli Rp {formatted_total}, tlg d cancel ya kak 🙏")
                                            elif is_30d_reached:
                                                limit = MAX_30D_LIMIT
                                                    
                                                formatted_limit = f"{limit:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                                                formatted_total = f"{total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                                                await send_chat_message(websocket, order_no, f"mohon maaf kak, limit per 30 hari beli nya hanya Rp {formatted_limit}, kk ud beli Rp {formatted_total}, tlg d cancel ya kak 🙏")
            
                                    # Check if 'addKycVrfInfo' exists and process it
                                    add_kyc_vrf_info = content_data.get('addKycVrfInfo', [])
                                    if add_kyc_vrf_info and any(info.get('livenessCheckStatus') == 0 for info in add_kyc_vrf_info):
                                        # Get order details ONCE
                                        cp_detail = await get_user_order_detail(order_no)
                                        
                                        if not cp_detail or 'data' not in cp_detail:
                                            return
                                        
                                        data = cp_detail['data']
                                        trade_type = data.get('tradeType', '')
                                        
                                        # If you're BUYING
                                        if trade_type == 'BUY':
                                            taker_amount = float(data.get('takerAmount', 0))
                                            asset = data.get('asset', '')
                                            
                                            # Check if you're a taker buying USDT
                                            if taker_amount > 0:
                                                # You're buying USDT as taker - send confirmation
                                                await send_chat_message(websocket, order_no, "bntr kak sy verify dl😊")
                                        else:
                                            # await asyncio.sleep(4)
                                            # load_ads_configuration()
                                            load_global_cp_limits()
                                            
                                            can_proceed, new_total_today, new_total_30d, exceeded_daily, exceeded_30d = await check_order_limit(order_no)
                                            if can_proceed:
                                                load_configurations()
                                                total_price = Decimal(cp_detail['data'].get('totalPrice', '0.0'))
                                                if total_trades <= CP_TOTAL_TRADES and total_price >= MAX_CP_BUY_LIMIT:
                                                    await send_chat_message(websocket, order_no, "Halo kak, \nMohon maaf, sesuai kebijakan keamanan kami, order ini belum bisa kami proses. \nMohon bantuannya untuk cancel order dari sisi pembeli ya. \nTerima kasih 🙏")
                                                else:
                                                    await send_chat_message(websocket, order_no, "ready kak, dirilis BOT ON 24 jam..\nverify wajah dl ya, lihat chat yg di atas😊")
                                            else:
                                                if cp_detail and 'data' in cp_detail and 'buyerName' in cp_detail['data']:
                                                    asset = cp_detail['data'].get('asset', 'USDT')  # Default to USDT if asset is not provided
                                                    
                                                if exceeded_daily:
                                                    limit = DAILY_BUY_LIMIT
                                                    formatted_limit = f"{limit:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                                                    
                                                    new_total_today = new_total_today or 0.00  
                                                    formatted_total = f"{new_total_today:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                                                    send_telegram_notification(f"Order {order_no} daily limit reached, incl cur: Rp {formatted_total}")
                                                    await send_chat_message(websocket, order_no, f"mohon maaf kak, limit perhari beli nya hanya Rp {formatted_limit}, kk hr ini ud beli Rp {formatted_total}, tlg d cancel ya kak🙏")
                                                    await insert_limit_reached_order(order_no, new_total_today, exceeded_daily, exceeded_30d)
                                                elif exceeded_30d:
                                                    limit = MAX_30D_LIMIT
                                                    formatted_limit = f"{limit:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                                                    
                                                    new_total_30d = new_total_30d or 0.00  
                                                    formatted_total = f"{new_total_30d:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                                                    send_telegram_notification(f"Order {order_no} 30d limit reached, incl cur: Rp {formatted_total}")
                                                    await send_chat_message(websocket, order_no, f"mohon maaf kak, limit per 30 hari beli nya hanya Rp {formatted_limit}, kk ud beli Rp {formatted_total}, tlg d cancel ya kak🙏")
                                                    await insert_limit_reached_order(order_no, new_total_30d, exceeded_daily, exceeded_30d)
                                    elif content_data.get('type') == 'seller_user_trading':
                                        await send_chat_message(websocket, order_no, "ready kk😊")
                                    elif content_data.get('type') == 'buyer_payed':
                                        await send_chat_message(websocket, order_no, "Done kak, tlg cek dan rilis ya kak😊")
                                    elif content_data.get('type') == 'seller_payed':
                                        await send_chat_message(websocket, order_no, "Wait kak, sy cek dl😊")
                                        
                                        await asyncio.sleep(10)
    
                                        await run_script(f"{PYTHON_SCRIPTS_PATH}extract_gmail.py")
                                        
                                        # cp_detail = await get_user_order_detail(order_no)
                                        # if not cp_detail or 'data' not in cp_detail:
                                            # print("Failed to retrieve order details or invalid response format.")
                                        # else:
                                            # if cp_detail['data'].get('orderStatus') == 2:
                                                # asyncio.create_task(run_script("permata_scrapping.py"))
                                    elif content_data.get('type') == 'seller_completed':
                                        await send_chat_message(websocket, order_no, "sdh rilis kak, makasih..\nsalam cuan😊")
                                        await get_sell_status_and_insert(order_no)
                                    elif content_data.get('type') == 'buyer_completed':
                                        # First mark as processed
                                        await mark_order_as_processed(order_no, "websocket")
                                        
                                        await send_chat_message(websocket, order_no, "terima kasih kak😊")
                                        
                                        order_detail = await get_user_order_detail(order_no)
                                        if order_detail and 'data' in order_detail:
                                            asset = order_detail['data'].get('asset', 'USDT') 
                                            price = order_detail['data'].get('price', None)
                                            amount_value = float(order_detail['data'].get('amount', 0)) - float(order_detail['data'].get('commission', 0))
                                            amount = Decimal(str(amount_value))

                                            # Handle USDT transfer if additional buy quota exists
                                            if asset == 'USDT':
                                                additional_quota = get_additional_buy_quota()
                                                if additional_quota > 0:
                                                    transfer_amount = amount if amount <= additional_quota else additional_quota
                                                    
                                                    # Transfer USDT to SPOT wallet
                                                    timestamp = get_timestamp()
                                                    transfer_params = {
                                                        'type': "FUNDING_MAIN",
                                                        'asset': 'USDT',
                                                        'amount': str(transfer_amount),
                                                        'timestamp': timestamp
                                                    }
                                                    query = urllib.parse.urlencode(transfer_params)
                                                    transfer_params['signature'] = generate_signature(query)
                                                    
                                                    headers = {'X-MBX-APIKEY': api_key}
                                                    transfer_url = f"{base_url}/sapi/v1/asset/transfer"
                                                    response = requests.post(transfer_url, headers=headers, data=transfer_params)
                                                    
                                                    result = response.json()
                                                    print(f"[TRANSFER TO SPOT] {result}")
                                                    
                                                    if result and "tranId" in result:
                                                        # Update additional quota
                                                        new_quota = additional_quota - transfer_amount
                                                        if new_quota < 0:
                                                            new_quota = 0.0
                                                        update_additional_buy_quota(amount, order_no)  # This will deduct the amount
                                                        print(f"Transferred {transfer_amount} USDT to SPOT, quota updated to {new_quota}")
                                                        send_telegram_notification(f"Transferred {transfer_amount} USDT to SPOT, quota updated to {new_quota}")
                                            # if asset != 'USDT':
                                            else:
                                                # Synchronously call bnb_fill_to_spot.py
                                                try:
                                                    result = subprocess.run(
                                                        ['python3', f'{PYTHON_SCRIPTS_PATH}bnb_fill_to_spot.py'],
                                                        check=True,
                                                        capture_output=True,
                                                        text=True
                                                    )
                                                    print(f"bnb_fill_to_spot.py output:\n{result.stdout}")
                                                except subprocess.CalledProcessError as e:
                                                    print(f"Error running bnb_fill_to_spot.py:\n{e.stderr}")

                                                # Convert if needed
                                                convert_stable_to_usdt_via_spot(asset, price, order_no)

                                        # # Get asset from order details
                                        # order_detail = await get_user_order_detail(order_no)
                                        # asset = order_detail['data'].get('asset', 'USDT') if order_detail and 'data' in order_detail else 'USDT'

                                        # if asset in ['FDUSD', 'USDC']:
                                            # amount_before = get_funding_wallet_balance(asset)
                                            # if amount_before > 0:
                                                # success, usdt_received = await convert_to_usdt(asset, amount_before)
                                                # if success:
                                                    # msg = (
                                                        # f"{asset} successfully converted to USDT\n"
                                                        # f"Converted: {amount_before:.8f} {asset}\n"
                                                        # f"Received: {usdt_received:.8f} USDT\n"
                                                        # f"Rate: {usdt_received / amount_before:.6f} USDT per {asset}"
                                                    # )
                                                    # send_telegram_notification(msg)
                                                    
                                        asyncio.create_task(run_script(f"{PYTHON_SCRIPTS_PATH}bin.py"))
                                    elif content_data.get('type') == 'buyer_merchant_trading':
                                        asyncio.create_task(run_script(f"{PYTHON_SCRIPTS_PATH}bin.py"))
                                except json.JSONDecodeError:
                                    # If content is not JSON, handle it as a plain string
                                    pass
                                    # print(f"Received plain string content: {content}")
                                    # Optionally, handle plain strings if needed
                            else:
                                print(f"Content is not a string: {content}")
                    except websockets.exceptions.ConnectionClosed as e:
                        print(f"Connection closed: {e}")
                        break  # Break the inner loop to trigger a reconnection
                    except Exception as e:
                        print(f"An error occurred while receiving the message: {e}")

        except websockets.exceptions.ConnectionClosed as e:
            print(f"Connection closed: {e}")
            break
        except Exception as e:
            print(f"An error occurred while receiving the message2: {e}")
        
        print("Attempting to reconnect in 3 seconds...")
        await asyncio.sleep(3)

async def main():
    print(f"=== Starting wss_chat.py at {datetime.now()} ===")
    
    try:
        # Load configurations
        load_configurations()
        
        # load_ads_configuration()  # Commented out in your code
        load_global_cp_limits()
        
        # Start both tasks concurrently
        await asyncio.gather(
            listen_for_messages(),           # Your existing WebSocket listener
            check_quick_received_buy_orders()  # The new quick-receive checker
        )
    except Exception as e:
        print(f"Fatal error in main(): {e}")
        import traceback
        traceback.print_exc()
        send_telegram_notification(f"❌ wss_chat.py crashed: {str(e)}")

if __name__ == "__main__":
    asyncio.run(main())
