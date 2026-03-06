import requests
import os
import logging
import time
from typing import Optional, Dict, Any, List
from urllib.parse import quote

logger = logging.getLogger(__name__)

# SuiteCRM docs say "Scopes haven't implemented yet" - omit scope to avoid invalid_scope errors
class SuiteCRMClient:
    """
    SuiteCRM 8 REST API Client (V8)
    Documentation: https://docs.suitecrm.com/developer/api/version-8/
    """
    def __init__(self, base_url: Optional[str] = None):
        # Use provided URL or default (SuiteCRM at 34.9.14.57)
        self.base_url = (base_url or os.getenv("SUITECRM_BASE_URL", "http://34.9.14.57")).rstrip('/')
        self.api_url = f"{self.base_url}/Api/V8"
        # Multiple token paths - SuiteCRM versions vary (7.10 vs 8.x)
        self.token_urls = [
            f"{self.base_url}/Api/access_token",
            f"{self.base_url}/api/oauth/access_token",
            f"{self.base_url}/legacy/Api/access_token",
        ]
        
        self.client_id = os.getenv("SUITECRM_CLIENT_ID")
        self.client_secret = os.getenv("SUITECRM_CLIENT_SECRET")
        self.username = os.getenv("SUITECRM_USERNAME")
        self.password = os.getenv("SUITECRM_PASSWORD")
        
        self.token = None
        self.token_expires_at = 0
        self._last_auth_error: Optional[str] = None

    def authenticate(self) -> bool:
        """
        Fetch OAuth2 token using client_credentials or password grant type.
        Tries JSON body (per SuiteCRM docs) and form-urlencoded (fallback).
        Requires RSA keys (private.key, public.key) in SuiteCRM lib/API/OAuth2/
        """
        if self.token and time.time() < self.token_expires_at - 60:
            return True

        if not self.client_id or not self.client_secret or self.client_id == "YOUR_CLIENT_ID":
            missing = [k for k, v in [
                ("SUITECRM_CLIENT_ID", self.client_id),
                ("SUITECRM_CLIENT_SECRET", self.client_secret),
                ("SUITECRM_USERNAME", self.username),
                ("SUITECRM_PASSWORD", self.password),
            ] if not v or v == "YOUR_CLIENT_ID"]
            self._last_auth_error = f"Missing credentials: {missing}. Set SUITECRM_* in Render Dashboard > Environment."
            logger.error(f"❌ SuiteCRM credentials not configured. Missing/empty: {missing}. Ensure these are set in Render env (or .env for local).")
            return False

        # Prefer password grant if username/password provided, else client_credentials
        if self.username and self.password:
            payload = {
                "grant_type": "password",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "username": self.username,
                "password": self.password,
            }
        else:
            payload = {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }

        auth_headers = {
            "Content-Type": "application/vnd.api+json",
            "Accept": "application/json",
        }

        last_error = None
        for token_url in self.token_urls:
            for use_json in [True, False]:  # Try JSON first (per docs), then form
                try:
                    logger.info(f"🔑 Authenticating with SuiteCRM ({payload['grant_type']}) at {token_url} (json={use_json})")
                    if use_json:
                        resp = requests.post(token_url, json=payload, headers=auth_headers, timeout=10)
                    else:
                        resp = requests.post(token_url, data=payload, timeout=10)
                    
                    if resp.status_code == 200:
                        data = resp.json()
                        self.token = data.get("access_token")
                        expires_in = data.get("expires_in", 3600)
                        self.token_expires_at = time.time() + expires_in
                        logger.info("✅ SuiteCRM authentication successful")
                        return True
                    
                    last_error = f"{resp.status_code}: {resp.text[:500]}"
                    # Log full response for 500 (often RSA key issues) and 401
                    if resp.status_code == 500:
                        logger.error(f"❌ SuiteCRM Auth 500 - Often caused by missing RSA keys. Full response: {resp.text[:800]}")
                        if "key" in resp.text.lower() or "path" in resp.text.lower():
                            logger.error("💡 RSA keys may be missing. On SuiteCRM server: cd lib/API/OAuth2 && openssl genrsa -out private.key 1024 && openssl rsa -in private.key -pubout -out public.key")
                    elif resp.status_code == 401:
                        logger.error(f"❌ SuiteCRM Auth 401: {resp.text[:300]}")
                    elif resp.status_code not in [404]:
                        logger.error(f"❌ SuiteCRM Auth Failed: {last_error}")
                        break
                except Exception as e:
                    last_error = str(e)
                    logger.warning(f"⚠️ Auth attempt failed: {e}")
                    continue

        self._last_auth_error = last_error
        logger.error(f"❌ SuiteCRM authentication failed after all attempts. Last: {last_error}")
        logger.error("💡 Check: 1) RSA keys in SuiteCRM lib/API/OAuth2/ (private.key, public.key) 2) OAuth2 client secret matches 3) Username/password valid. See docs/SUITECRM_INTEGRATION.md")
        return False

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/vnd.api+json",
            "Accept": "application/vnd.api+json"
        }

    def _make_request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Helper to make authenticated requests with auto-retry on auth failure"""
        if not self.authenticate():
            detail = getattr(self, "_last_auth_error", None) or "Check SUITECRM_* env vars in Render Dashboard"
            return {"success": False, "error": f"Authentication failed: {detail}"}

        url = f"{self.api_url}/{endpoint.lstrip('/')}"
        
        try:
            response = requests.request(method, url, headers=self._get_headers(), timeout=15, **kwargs)
            
            # If 401, token might have expired unexpectedly, try once more after re-auth
            if response.status_code == 401:
                logger.warning("⚠️ SuiteCRM token expired, retrying...")
                self.token = None
                if self.authenticate():
                    response = requests.request(method, url, headers=self._get_headers(), timeout=15, **kwargs)
            
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except Exception as e:
            logger.error(f"❌ SuiteCRM request failed ({method} {endpoint}): {e}")
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_data = e.response.json()
                    logger.error(f"❌ Error details: {error_data}")
                    return {"success": False, "error": str(e), "details": error_data}
                except:
                    pass
            return {"success": False, "error": str(e)}

    def search_patient(self, phone: str) -> Dict[str, Any]:
        """
        Search for a patient by mobile phone number in the Contacts module.
        """
        # SuiteCRM 8 filter syntax: filter[field][eq]=value (field must be array/operator format)
        endpoint = f"module/Contacts?filter[phone_mobile][eq]={quote(phone)}"
        result = self._make_request("GET", endpoint)
        
        if result["success"]:
            data = result["data"].get("data", [])
            if data:
                # Return the first match
                patient = data[0]
                return {
                    "success": True,
                    "found": True,
                    "patient_id": patient["id"],
                    "attributes": patient.get("attributes", {})
                }
            return {"success": True, "found": False}
        return result

    def create_patient(self, first_name: str, last_name: str, phone: str, **kwargs) -> Dict[str, Any]:
        """
        Create a new patient in the Contacts module.
        """
        payload = {
            "data": {
                "type": "Contacts",
                "attributes": {
                    "first_name": first_name,
                    "last_name": last_name,
                    "phone_mobile": phone,
                    "lead_source": "VoiceAI",
                    **kwargs
                }
            }
        }
        
        result = self._make_request("POST", "module", json=payload)
        if result["success"]:
            new_patient = result["data"].get("data", {})
            return {
                "success": True, 
                "patient_id": new_patient.get("id"),
                "attributes": new_patient.get("attributes", {})
            }
        return result

    def create_meeting(self, patient_id: str, subject: str, date_start: str, duration_minutes: int = 30) -> Dict[str, Any]:
        """
        Schedule an appointment (Meeting) and relate it to a Contact.
        date_start should be in ISO format (YYYY-MM-DD HH:MM:SS)
        SuiteCRM POST module only accepts attributes, id, type - not relationships.
        So we create the Meeting first, then add the Contact relationship in a separate call.
        """
        # Step 1: Create Meeting (attributes only - API rejects relationships)
        payload = {
            "data": {
                "type": "Meetings",
                "attributes": {
                    "name": subject,
                    "date_start": date_start,
                    "duration_hours": "0",
                    "duration_minutes": str(duration_minutes),
                    "status": "Planned"
                }
            }
        }
        result = self._make_request("POST", "module", json=payload)
        if not result["success"]:
            return result

        meeting = result["data"].get("data", {})
        meeting_id = meeting.get("id")
        if not meeting_id:
            return {"success": False, "error": "Meeting created but no ID returned"}

        # Step 2: Add Contact relationship (POST module/Meetings/{id}/relationships/contacts)
        rel_payload = {"data": {"type": "Contacts", "id": patient_id}}
        rel_result = self._make_request(
            "POST",
            f"module/Meetings/{meeting_id}/relationships/contacts",
            json=rel_payload
        )
        if not rel_result["success"]:
            # Meeting created but relationship failed - still return success with meeting_id
            logger.warning(f"Meeting {meeting_id} created but link to contact {patient_id} failed: {rel_result.get('error')}")
        return {
            "success": True,
            "meeting_id": meeting_id,
            "status": "booked"
        }

    def create_case(self, patient_id: str, subject: str, description: str, priority: str = "P3") -> Dict[str, Any]:
        """
        Create a Case for a medical issue or triage.
        API rejects relationships in POST - create Case first, then link Contact.
        """
        payload = {
            "data": {
                "type": "Cases",
                "attributes": {
                    "name": subject,
                    "description": description,
                    "priority": priority,
                    "status": "New"
                }
            }
        }
        result = self._make_request("POST", "module", json=payload)
        if not result["success"]:
            return result
        case = result["data"].get("data", {})
        case_id = case.get("id")
        if not case_id:
            return {"success": False, "error": "Case created but no ID returned"}
        rel_payload = {"data": {"type": "Contacts", "id": patient_id}}
        rel_result = self._make_request("POST", f"module/Cases/{case_id}/relationships/contacts", json=rel_payload)
        if not rel_result["success"]:
            logger.warning(f"Case {case_id} created but link to contact {patient_id} failed")
        return {"success": True, "case_id": case_id}

    def create_note(self, patient_id: str, subject: str, content: str) -> Dict[str, Any]:
        """
        Create a Note for call summaries or doctor notes.
        API rejects relationships in POST - create Note first, then link Contact.
        """
        payload = {
            "data": {
                "type": "Notes",
                "attributes": {
                    "name": subject,
                    "description": content
                }
            }
        }
        result = self._make_request("POST", "module", json=payload)
        if not result["success"]:
            return result
        note = result["data"].get("data", {})
        note_id = note.get("id")
        if not note_id:
            return {"success": False, "error": "Note created but no ID returned"}
        rel_payload = {"data": {"type": "Contacts", "id": patient_id}}
        rel_result = self._make_request("POST", f"module/Notes/{note_id}/relationships/contacts", json=rel_payload)
        if not rel_result["success"]:
            logger.warning(f"Note {note_id} created but link to contact {patient_id} failed")
        return {"success": True, "note_id": note_id}
