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
    
    # Strip whitespace and convert to lowercase
    text_lower = text.strip().lower()
    
    # Mortgage-related keywords (order matters - more specific first)
    mortgage_keywords = [
        "apply for mortgage",
        "mortgage application",
        "want to apply for a mortgage",
        "want to apply for mortgage",
        "apply for a mortgage",
        "apply for a mortgage",
        "mortgage",
        "pre-approved",
        "pre approved",
        "home loan",
        "house loan",
        "credit score",
        "dti ratio",
        "debt to income",
        "debt-to-income",
        "down payment",
        "closing costs",
        "loan amount",
        "property value",
        "mortgage documents",
        "w-2",
        "w2",
        "pay stub",
        "paystub",
        "tax return",
        "bank statement",
        "mortgage approval",
        "mortgage pre-approval"
    ]
    
    # Check if any mortgage keyword is present
    for keyword in mortgage_keywords:
        if keyword in text_lower:
            print(f"🏠 Mortgage intent detected: keyword '{keyword}' found in '{text_lower}'", flush=True)
            return True
    
    print(f"📝 No mortgage intent detected in: '{text_lower}'", flush=True)
    return False
