"""
Healthcare Payer Intent Detection
Detects if user wants to interact with healthcare payer services based on their input
"""

from typing import Optional, Dict, Any


def detect_healthcare_intent(text: str) -> bool:
    """
    Detect if user input indicates healthcare payer service intent.
    
    Args:
        text: User input text
        
    Returns:
        True if healthcare intent detected, False otherwise
    """
    if not text:
        return False
    
    # Strip whitespace and convert to lowercase
    text_lower = text.strip().lower()
    
    # Healthcare-related keywords (order matters - more specific first)
    healthcare_keywords = [
        # Claims
        "claim status",
        "my claim",
        "check claim",
        "claim denied",
        "claim rejected",
        "appeal claim",
        "file appeal",
        "appeal",
        "appeal denial",
        "denial",
        "explanation of benefits",
        "eob",
        "why was my claim",
        
        # Eligibility & Coverage
        "am i covered",
        "coverage active",
        "eligibility",
        "check my eligibility",
        "is my insurance",
        "insurance active",
        "coverage dates",
        "when does my coverage",
        "health care coverage",
        "healthcare coverage",
        "check coverage",
        "my coverage",
        "coverage status",
        "verify coverage",
        
        # Benefits
        "benefits",
        "what's covered",
        "is it covered",
        "coverage for",
        "covered procedure",
        "my plan",
        "plan benefits",
        
        # Deductible and Out-of-Pocket
        "deductible",
        "met my deductible",
        "out of pocket",
        "out-of-pocket",
        "oop max",
        "maximum",
        "copay",
        "co-pay",
        "coinsurance",
        "cost sharing",
        
        # Prior Authorization
        "prior auth",
        "prior authorization",
        "pre-authorization",
        "preauth",
        "need approval",
        "authorization status",
        "auth number",
        
        # Provider Network
        "in network",
        "in-network",
        "out of network",
        "find a doctor",
        "find doctor",
        "find provider",
        "provider search",
        "is my doctor",
        "network status",
        "find specialist",
        "find a specialist",
        "cardiologist",
        "dermatologist",
        "orthopedic",
        "primary care",
        
        # Care Programs
        "care program",
        "disease management",
        "wellness program",
        "health program",
        "enroll in program",
        
        # Preventive Care
        "preventive care",
        "annual physical",
        "checkup",
        "screening",
        "mammogram",
        "colonoscopy",
        "flu shot",
        
        # Medical Procedures (commonly discussed with insurance)
        "mri",
        "ct scan",
        "cat scan",
        "x-ray",
        "xray",
        "ultrasound",
        "surgery",
        "procedure",
        "lab work",
        "blood test",
        "physical therapy",
        "pt session",
        "imaging",
        "scan",
        
        # Provider-Side / Clinical (SuiteCRM integration)
        "book an appointment",
        "schedule a visit",
        "book a doctor",
        "appointment with",
        "new patient",
        "register as a patient",
        "medical intake",
        "symptoms",
        "i have a fever",
        "i have a cough",
        "i'm feeling sick",
        "feeling unwell",
        "triage",
        "clinical intake",
        "medical record",
        "my patient record",
        "medical history",
        
        # General Healthcare
        "health insurance",
        "insurance question",
        "member services",
        "healthcare",
        "health plan",
        "medical bill",
        "medical claim",
        "insurance claim",
        "health coverage",
        "health care insurance",
        "healthcare insurance",
        "medical insurance",
        "insurance coverage",
        "check my insurance",
        "why was my",  # Common pattern for denial questions
        "was denied",
        "got denied"
    ]
    
    # Check if any healthcare keyword is present
    for keyword in healthcare_keywords:
        if keyword in text_lower:
            print(f"🏥 Healthcare intent detected: keyword '{keyword}' found in '{text_lower}'", flush=True)
            return True
    
    print(f"📝 No healthcare intent detected in: '{text_lower}'", flush=True)
    return False


def get_healthcare_sub_intent(text: str) -> Optional[str]:
    """
    Determine the specific healthcare service sub-intent.
    
    Args:
        text: User input text
        
    Returns:
        Sub-intent category or None
    """
    if not text:
        return None
    
    text_lower = text.strip().lower()
    
    # Claims-related
    claims_keywords = ["claim", "eob", "explanation of benefits", "appeal", "denied", "rejected"]
    for kw in claims_keywords:
        if kw in text_lower:
            return "claims"
    
    # Eligibility-related
    eligibility_keywords = ["eligibility", "am i covered", "coverage active", "insurance active", "coverage dates"]
    for kw in eligibility_keywords:
        if kw in text_lower:
            return "eligibility"
    
    # Benefits-related
    benefits_keywords = ["benefit", "covered", "coverage", "deductible", "copay", "coinsurance", "out of pocket", "out-of-pocket"]
    for kw in benefits_keywords:
        if kw in text_lower:
            return "benefits"
    
    # Prior auth-related
    auth_keywords = ["prior auth", "authorization", "preauth", "approval"]
    for kw in auth_keywords:
        if kw in text_lower:
            return "prior_auth"
    
    # Provider-related
    provider_keywords = ["doctor", "provider", "specialist", "network", "cardiologist", "dermatologist", "orthopedic"]
    for kw in provider_keywords:
        if kw in text_lower:
            return "provider_network"
    
    # Care programs
    care_keywords = ["care program", "wellness", "disease management"]
    for kw in care_keywords:
        if kw in text_lower:
            return "care_programs"
    
    # Preventive care
    preventive_keywords = ["preventive", "physical", "screening", "checkup"]
    for kw in preventive_keywords:
        if kw in text_lower:
            return "preventive_care"
            
    # Clinical/SuiteCRM-related
    clinical_keywords = ["book", "schedule", "register", "intake", "symptom", "triage", "sick", "record"]
    for kw in clinical_keywords:
        if kw in text_lower:
            return "clinical_suitecrm"
    
    return "general"


def get_healthcare_intent_context(text: str) -> Dict[str, Any]:
    """
    Get full context about the healthcare intent for routing.
    
    Args:
        text: User input text
        
    Returns:
        Dictionary with intent details
    """
    is_healthcare = detect_healthcare_intent(text)
    
    return {
        "is_healthcare_intent": is_healthcare,
        "sub_intent": get_healthcare_sub_intent(text) if is_healthcare else None,
        "confidence": "high" if is_healthcare else "none",
        "original_text": text
    }
