"""
Mortgage Intent Detection
Detects if user wants to use mortgage bot based on their input
"""

def detect_mortgage_intent(text: str) -> bool:
    """
    Detect if user input indicates mortgage application intent.
    
    Args:
        text: User input text
        
    Returns:
        True if mortgage intent detected, False otherwise
    """
    if not text:
        return False
    
    text_lower = text.lower()
    
    # Mortgage-related keywords
    mortgage_keywords = [
        "mortgage",
        "apply for mortgage",
        "mortgage application",
        "pre-approved",
        "pre approved",
        "home loan",
        "house loan",
        "credit score",
        "dti ratio",
        "debt to income",
        "down payment",
        "closing costs",
        "loan amount",
        "property value",
        "mortgage documents",
        "w-2",
        "pay stub",
        "tax return",
        "bank statement",
        "mortgage approval"
    ]
    
    # Check if any mortgage keyword is present
    for keyword in mortgage_keywords:
        if keyword in text_lower:
            return True
    
    return False
