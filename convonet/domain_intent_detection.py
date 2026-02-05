"""
Domain Intent Detection
Unified system for detecting user intent across multiple domains
Supports: Mortgage, Healthcare Payer, Productivity (general)
"""

from typing import Optional, Dict, Any, List
from enum import Enum


class Domain(str, Enum):
    """Supported conversation domains"""
    PRODUCTIVITY = "productivity"  # General productivity assistant
    MORTGAGE = "mortgage"  # Mortgage application assistance
    HEALTHCARE = "healthcare"  # Healthcare payer member services
    

def detect_mortgage_intent(text: str) -> bool:
    """Detect mortgage-related intent"""
    if not text:
        return False
    
    text_lower = text.strip().lower()
    
    mortgage_keywords = [
        "apply for mortgage", "mortgage application", "mortgage",
        "pre-approved", "pre approved", "home loan", "house loan",
        "credit score", "monthly income", "monthly gross income",
        "monthly debt", "debt payments", "total monthly debt",
        "dti ratio", "debt to income", "debt-to-income",
        "down payment", "savings for house", "closing costs",
        "loan amount", "property value", "mortgage documents",
        "w-2", "w2", "pay stub", "paystub", "tax return for mortgage",
        "bank statement", "mortgage approval", "mortgage pre-approval",
        "buy a house", "buy a home", "home purchase", "house purchase"
    ]
    
    for keyword in mortgage_keywords:
        if keyword in text_lower:
            return True
    return False


def detect_healthcare_intent(text: str) -> bool:
    """Detect healthcare payer service intent"""
    if not text:
        return False
    
    text_lower = text.strip().lower()
    
    healthcare_keywords = [
        # Claims
        "claim status", "my claim", "check claim", "claim denied",
        "appeal claim", "file appeal", "explanation of benefits", "eob",
        
        # Eligibility & Coverage
        "am i covered", "coverage active", "eligibility",
        "is my insurance", "insurance active", "coverage dates",
        "health care coverage", "healthcare coverage", "check coverage",
        "my coverage", "coverage status", "verify coverage",
        
        # Benefits
        "benefits", "what's covered", "is it covered", "coverage for",
        "my plan", "plan benefits", "deductible", "met my deductible",
        "out of pocket", "out-of-pocket", "oop max", "copay", "co-pay",
        "coinsurance",
        
        # Prior Authorization
        "prior auth", "prior authorization", "pre-authorization",
        "preauth", "need approval", "authorization status",
        
        # Provider Network
        "in network", "in-network", "out of network", "find a doctor",
        "find doctor", "find provider", "is my doctor", "network status",
        "find specialist", "cardiologist", "dermatologist", "orthopedic",
        
        # Care Programs
        "care program", "disease management", "wellness program",
        
        # Preventive Care
        "preventive care", "annual physical", "screening",
        "mammogram", "colonoscopy", "flu shot",
        
        # General Healthcare
        "health insurance", "member services", "healthcare",
        "health plan", "medical bill", "medical claim", "insurance claim"
    ]
    
    for keyword in healthcare_keywords:
        if keyword in text_lower:
            return True
    return False


def detect_domain(text: str, current_domain: Optional[Domain] = None) -> Domain:
    """
    Detect the appropriate domain for the user's input.
    
    Args:
        text: User input text
        current_domain: The domain currently active in the conversation
        
    Returns:
        Detected domain
    """
    if not text:
        return current_domain or Domain.PRODUCTIVITY
    
    text_lower = text.strip().lower()
    
    # Check for explicit domain switches
    if "switch to mortgage" in text_lower or "mortgage bot" in text_lower:
        return Domain.MORTGAGE
    if "switch to healthcare" in text_lower or "healthcare bot" in text_lower or "insurance bot" in text_lower:
        return Domain.HEALTHCARE
    if "switch to general" in text_lower or "general assistant" in text_lower or "productivity" in text_lower:
        return Domain.PRODUCTIVITY
    
    # Detect intent for specific domains
    is_mortgage = detect_mortgage_intent(text)
    is_healthcare = detect_healthcare_intent(text)
    
    # If both detected, prioritize healthcare (more specific/urgent)
    if is_healthcare:
        print(f"🏥 Healthcare domain detected for: '{text[:50]}...'", flush=True)
        return Domain.HEALTHCARE
    
    if is_mortgage:
        print(f"🏠 Mortgage domain detected for: '{text[:50]}...'", flush=True)
        return Domain.MORTGAGE
    
    # If current domain is set and no new domain detected, stay in current
    if current_domain and current_domain != Domain.PRODUCTIVITY:
        print(f"📌 Staying in current domain: {current_domain.value}", flush=True)
        return current_domain
    
    # Default to productivity
    print(f"📝 Defaulting to productivity domain", flush=True)
    return Domain.PRODUCTIVITY


def get_domain_context(text: str, current_domain: Optional[Domain] = None) -> Dict[str, Any]:
    """
    Get comprehensive domain context for routing and processing.
    
    Args:
        text: User input text
        current_domain: Currently active domain
        
    Returns:
        Dictionary with domain detection results
    """
    detected_domain = detect_domain(text, current_domain)
    
    return {
        "detected_domain": detected_domain.value,
        "previous_domain": current_domain.value if current_domain else None,
        "domain_switched": current_domain != detected_domain if current_domain else True,
        "is_mortgage": detected_domain == Domain.MORTGAGE,
        "is_healthcare": detected_domain == Domain.HEALTHCARE,
        "is_productivity": detected_domain == Domain.PRODUCTIVITY,
        "confidence": _get_confidence(text, detected_domain),
        "original_text": text
    }


def _get_confidence(text: str, domain: Domain) -> str:
    """Determine confidence level of domain detection"""
    if not text:
        return "low"
    
    text_lower = text.strip().lower()
    
    # High confidence triggers
    high_confidence_mortgage = ["mortgage application", "apply for mortgage", "home loan"]
    high_confidence_healthcare = ["claim status", "prior authorization", "eligibility", "health insurance"]
    
    if domain == Domain.MORTGAGE:
        for kw in high_confidence_mortgage:
            if kw in text_lower:
                return "high"
        return "medium"
    
    if domain == Domain.HEALTHCARE:
        for kw in high_confidence_healthcare:
            if kw in text_lower:
                return "high"
        return "medium"
    
    return "default"


def get_domain_system_prompt(domain: Domain) -> str:
    """
    Get the appropriate system prompt for the detected domain.
    
    Args:
        domain: The detected domain
        
    Returns:
        System prompt string
    """
    if domain == Domain.MORTGAGE:
        from convonet.mortgage_prompts import MORTGAGE_SYSTEM_PROMPT
        return MORTGAGE_SYSTEM_PROMPT
    
    if domain == Domain.HEALTHCARE:
        from convonet.healthcare_payer_prompts import HEALTHCARE_PAYER_SYSTEM_PROMPT
        return HEALTHCARE_PAYER_SYSTEM_PROMPT
    
    # Default productivity prompt
    return """You are a helpful AI productivity assistant. You can help with:
- Managing todos and tasks
- Scheduling and calendar
- Setting reminders
- General questions and research
- Note-taking and organization

Be conversational and helpful. Your responses will be read aloud, so be concise and clear.

VOICE OUTPUT FORMAT (CRITICAL):
- DO NOT use markdown formatting (no **, no *, no #, no bullet points)
- DO NOT use numbered lists with periods - say "first", "second", "third" instead
- Use natural spoken language
- Keep responses concise and conversational"""


def get_domain_mcp_tools(domain: Domain) -> List[str]:
    """
    Get the list of MCP tools available for the domain.
    
    Args:
        domain: The detected domain
        
    Returns:
        List of MCP tool server names
    """
    base_tools = ["db_todo", "call_transfer"]  # Common tools
    
    if domain == Domain.MORTGAGE:
        return base_tools + ["db_mortgage"]
    
    if domain == Domain.HEALTHCARE:
        return base_tools + ["db_healthcare_payer"]
    
    return base_tools


# Export for convenience
__all__ = [
    "Domain",
    "detect_domain",
    "detect_mortgage_intent",
    "detect_healthcare_intent",
    "get_domain_context",
    "get_domain_system_prompt",
    "get_domain_mcp_tools"
]
