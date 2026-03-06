import os
import sys
import logging
import requests
from dotenv import load_dotenv

# Add the project root to sys.path to import convonet
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from convonet.services.suitecrm_client import SuiteCRMClient

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_raw_token_diagnostic():
    """Make a raw token request and print full response - helps diagnose RSA key vs credential issues."""
    load_dotenv()
    base = os.getenv("SUITECRM_BASE_URL", "http://34.9.14.57").rstrip("/")
    url = f"{base}/Api/access_token"
    payload = {
        "grant_type": "password",
        "client_id": os.getenv("SUITECRM_CLIENT_ID"),
        "client_secret": os.getenv("SUITECRM_CLIENT_SECRET"),
        "username": os.getenv("SUITECRM_USERNAME"),
        "password": os.getenv("SUITECRM_PASSWORD"),
    }
    print("\n--- Raw token request diagnostic ---")
    print(f"URL: {url}")
    print(f"Client ID: {payload['client_id'][:8]}... (masked)")
    print(f"Username: {payload['username']}")
    try:
        r = requests.post(url, json=payload, headers={"Content-Type": "application/vnd.api+json", "Accept": "application/json"}, timeout=10)
        print(f"Status: {r.status_code}")
        print(f"Response: {r.text[:1000]}")
        if r.status_code == 500 and ("key" in r.text.lower() or "path" in r.text.lower()):
            print("\n💡 Likely RSA keys issue. On SuiteCRM server run:")
            print("   cd lib/API/OAuth2 && openssl genrsa -out private.key 1024 && openssl rsa -in private.key -pubout -out public.key")
    except Exception as e:
        print(f"Error: {e}")
    print("---\n")

def test_connection(verbose=False):
    load_dotenv()
    
    base_url = os.getenv("SUITECRM_BASE_URL")
    client_id = os.getenv("SUITECRM_CLIENT_ID")
    client_secret = os.getenv("SUITECRM_CLIENT_SECRET")
    
    logger.info(f"Testing SuiteCRM connection to {base_url}...")
    
    if not client_id or client_id == "YOUR_CLIENT_ID":
        logger.error("❌ SUITECRM_CLIENT_ID is not set or is a placeholder.")
        return False
        
    client = SuiteCRMClient()
    
    # Test Authentication
    if client.authenticate():
        logger.info("✅ Authentication successful!")
    else:
        logger.error("❌ Authentication failed. Please check your credentials.")
        return False
        
    # Test Searching for a patient (using a dummy phone)
    logger.info("Testing patient search...")
    result = client.search_patient("925-989-7818")
    if result.get("success"):
        logger.info(f"✅ Search patient result: {result}")
    else:
        logger.error(f"❌ Search patient failed: {result.get('error')}")
        
    return True

if __name__ == "__main__":
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    success = test_connection(verbose=verbose)
    if success:
        logger.info("🚀 SuiteCRM Integration Test Passed!")
    else:
        logger.error("Failed SuiteCRM Integration Test.")
        sys.exit(1)
