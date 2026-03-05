import requests
import os
import logging
import time
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

class SuiteCRMClient:
    """
    SuiteCRM 8 REST API Client (V8)
    Documentation: https://docs.suitecrm.com/developer/api/version-8/
    """
    def __init__(self, base_url: Optional[str] = None):
        # Use provided URL or default from user request
        self.base_url = (base_url or os.getenv("SUITECRM_BASE_URL", "http://34.9.14.57")).rstrip('/')
        self.api_url = f"{self.base_url}/Api/V8"
        self.token_url = f"{self.base_url}/Api/access_token"
        
        self.client_id = os.getenv("SUITECRM_CLIENT_ID")
        self.client_secret = os.getenv("SUITECRM_CLIENT_SECRET")
        
        self.token = None
        self.token_expires_at = 0

    def authenticate(self) -> bool:
        """
        Fetch OAuth2 token using client_credentials grant type.
        """
        if self.token and time.time() < self.token_expires_at - 60:
            return True

        if not self.client_id or not self.client_secret:
            logger.error("❌ SuiteCRM credentials not configured (SUITECRM_CLIENT_ID/SECRET)")
            return False

        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }

        try:
            logger.info(f"🔑 Authenticating with SuiteCRM at {self.token_url}")
            response = requests.post(self.token_url, json=payload, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            self.token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)
            self.token_expires_at = time.time() + expires_in
            
            logger.info("✅ SuiteCRM authentication successful")
            return True
        except Exception as e:
            logger.error(f"❌ SuiteCRM authentication failed: {e}")
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
            return {"success": False, "error": "Authentication failed"}

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
        # SuiteCRM 8 filter syntax: filter[<field>]=<value>
        endpoint = f"module/Contacts?filter[phone_mobile]={phone}"
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
        """
        payload = {
            "data": {
                "type": "Meetings",
                "attributes": {
                    "name": subject,
                    "date_start": date_start,
                    "duration_minutes": duration_minutes,
                    "status": "Planned"
                },
                "relationships": {
                    "contacts": {
                        "data": {
                            "type": "Contacts",
                            "id": patient_id
                        }
                    }
                }
            }
        }
        
        result = self._make_request("POST", "module", json=payload)
        if result["success"]:
            meeting = result["data"].get("data", {})
            return {
                "success": True,
                "meeting_id": meeting.get("id"),
                "status": "booked"
            }
        return result

    def create_case(self, patient_id: str, subject: str, description: str, priority: str = "P3") -> Dict[str, Any]:
        """
        Create a Case for a medical issue or triage.
        """
        payload = {
            "data": {
                "type": "Cases",
                "attributes": {
                    "name": subject,
                    "description": description,
                    "priority": priority,
                    "status": "New"
                },
                "relationships": {
                    "contacts": {
                        "data": {
                            "type": "Contacts",
                            "id": patient_id
                        }
                    }
                }
            }
        }
        
        result = self._make_request("POST", "module", json=payload)
        if result["success"]:
            case = result["data"].get("data", {})
            return {
                "success": True,
                "case_id": case.get("id")
            }
        return result

    def create_note(self, patient_id: str, subject: str, content: str) -> Dict[str, Any]:
        """
        Create a Note for call summaries or doctor notes.
        """
        payload = {
            "data": {
                "type": "Notes",
                "attributes": {
                    "name": subject,
                    "description": content
                },
                "relationships": {
                    "contacts": {
                        "data": {
                            "type": "Contacts",
                            "id": patient_id
                        }
                    }
                }
            }
        }
        
        result = self._make_request("POST", "module", json=payload)
        if result["success"]:
            note = result["data"].get("data", {})
            return {
                "success": True,
                "note_id": note.get("id")
            }
        return result
