"""
MCP Server for SuiteCRM Healthcare Operations
Provides tools for patient management, appointment scheduling, and clinical record keeping.
"""

from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
from typing import Optional, Dict, Any, List
import os
import logging
from convonet.services.suitecrm_client import SuiteCRMClient

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize FastMCP server
mcp = FastMCP("SuiteCRM Healthcare MCP Server")

# Initialize SuiteCRM Client
client = SuiteCRMClient()

@mcp.tool()
def check_patient_exists(phone: str) -> Dict[str, Any]:
    """
    Check if a patient exists in SuiteCRM by their phone number.
    
    Args:
        phone: Patient's mobile phone number
        
    Returns:
        Dictionary indicating if patient was found and their details
    """
    logger.info(f"🔍 Checking if patient exists: {phone}")
    return client.search_patient(phone)

@mcp.tool()
def onboard_patient(first_name: str, last_name: str, phone: str, dob: Optional[str] = None) -> Dict[str, Any]:
    """
    Register a new patient in SuiteCRM.
    
    Args:
        first_name: Patient's first name
        last_name: Patient's last name
        phone: Patient's mobile phone number
        dob: Date of birth (optional, YYYY-MM-DD)
        
    Returns:
        Dictionary with new patient details
    """
    logger.info(f"📝 Onboarding new patient: {first_name} {last_name} ({phone})")
    extra_fields = {}
    if dob:
        extra_fields["birthdate"] = dob
        
    return client.create_patient(first_name, last_name, phone, **extra_fields)

@mcp.tool()
def book_appointment(patient_id: str, appointment_type: str, date_start: str, duration: int = 30) -> Dict[str, Any]:
    """
    Schedule a new appointment for a patient.
    
    Args:
        patient_id: SuiteCRM Contact ID
        appointment_type: Type of appointment (e.g. Checkup, Consultation, Triage)
        date_start: Start time in ISO format (YYYY-MM-DD HH:MM:SS)
        duration: Duration in minutes (default 30)
        
    Returns:
        Confirmation of booked appointment
    """
    logger.info(f"📅 Booking appointment for patient {patient_id}: {appointment_type} at {date_start}")
    subject = f"Healthcare Appointment: {appointment_type}"
    return client.create_meeting(patient_id, subject, date_start, duration)

@mcp.tool()
def log_clinical_intake(patient_id: str, symptoms: str, triage_notes: str, priority: str = "P3") -> Dict[str, Any]:
    """
    Log medical symptoms and triage results as a new Case in SuiteCRM.
    
    Args:
        patient_id: SuiteCRM Contact ID
        symptoms: Description of patient symptoms
        triage_notes: Detailed notes from the triage process
        priority: Urgency level (P1: High, P2: Medium, P3: Low)
        
    Returns:
        Confirmation of created Case
    """
    logger.info(f"🏥 Logging triage intake for patient {patient_id}")
    subject = f"Triage Intake: {symptoms[:30]}..."
    description = f"Symptoms: {symptoms}\n\nTriage Notes: {triage_notes}"
    return client.create_case(patient_id, subject, description, priority)

@mcp.tool()
def save_call_summary(patient_id: str, summary: str, call_type: str = "Voice AI Consultation") -> Dict[str, Any]:
    """
    Save a summary of the AI conversation for the healthcare staff.
    
    Args:
        patient_id: SuiteCRM Contact ID
        summary: The generated summary or SOAP note
        call_type: Label for the summary
        
    Returns:
        Confirmation of saved note
    """
    logger.info(f"📝 Saving call summary for patient {patient_id}")
    subject = f"Summary: {call_type}"
    return client.create_note(patient_id, subject, summary)

if __name__ == "__main__":
    mcp.run()
