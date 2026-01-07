# Mortgage Application Voice Bot Implementation Guide

## Overview

This document describes the implementation of a voice bot for pre-approved mortgage application processes. The system guides users through financial review and document collection using natural voice interactions.

## Architecture Components

### 1. Database Models
**File:** `convonet/models/mortgage_models.py`

**Tables:**
- `mortgage_applications` - Main application table
- `mortgage_documents` - Uploaded documents
- `mortgage_debts` - Debt information
- `mortgage_application_notes` - Notes and comments

**Key Fields:**
- Financial data: credit_score, monthly_income, monthly_debt, dti_ratio
- Progress tracking: financial_review_completed, document_collection_completed
- Status: draft → financial_review → document_collection → pre_approved

### 2. MCP Tools
**File:** `convonet/mcps/local_servers/db_mortgage.py`

**Available Tools:**
- `create_mortgage_application` - Start new application
- `get_mortgage_application_status` - Get current status
- `update_mortgage_financial_info` - Update credit score, income, debt, savings
- `calculate_dti_ratio` - Calculate debt-to-income ratio
- `add_mortgage_debt` - Add debt information
- `get_mortgage_debts` - List all debts
- `upload_mortgage_document` - Upload documents
- `get_mortgage_documents` - List uploaded documents
- `get_required_documents` - Get list of required documents
- `get_missing_documents` - Check what's still needed

### 3. System Prompts
**File:** `convonet/mortgage_prompts.py`

**Key Prompts:**
- `MORTGAGE_SYSTEM_PROMPT` - Main system prompt for mortgage bot
- `MORTGAGE_GREETING` - Initial greeting message
- `MORTGAGE_FINANCIAL_REVIEW_PROMPT` - Financial review guidance
- `MORTGAGE_DOCUMENT_COLLECTION_PROMPT` - Document collection guidance

### 4. Dashboard UI
**File:** `templates/mortgage_dashboard.html`

**Features:**
- Real-time application statistics
- Application list with status tracking
- Detailed application view
- Document tracking
- Progress indicators

## Setup Instructions

### 1. Database Setup

Run the migration script:
```bash
psql -U username -d database_name -f migrations/create_mortgage_tables.sql
```

### 2. MCP Server Configuration

Add to your MCP configuration file (e.g., `mcp_config.json`):
```json
{
  "mcpServers": {
    "mortgage": {
      "command": "python",
      "args": ["./convonet/mcps/local_servers/db_mortgage.py"],
      "transport": "stdio",
      "env": {
        "DB_URI": "${DB_URI}"
      }
    }
  }
}
```

### 3. Mortgage Agent (Already Created)

The `MortgageAgent` class has been added to `convonet/assistant_graph_todo.py`. It inherits from `TodoAgent` and uses mortgage-specific prompts.

**Location:** `convonet/assistant_graph_todo.py` (after `TodoAgent` class)

**Usage:**
```python
from convonet.assistant_graph_todo import MortgageAgent, get_mortgage_agent

# Option 1: Create directly
mortgage_agent = MortgageAgent(tools=mortgage_tools, provider="claude")

# Option 2: Use helper function
mortgage_agent = get_mortgage_agent(tools=mortgage_tools, provider="claude")

# Get the graph
graph = mortgage_agent.build_graph()
```

### 4. Add Dashboard Route

Add to your Flask routes:
```python
@convonet_todo_bp.route('/mortgage/dashboard')
def mortgage_dashboard():
    return render_template('mortgage_dashboard.html')
```

### 5. Add API Endpoints

Create API endpoints for the dashboard:
```python
@convonet_todo_bp.route('/api/mortgage/applications', methods=['GET'])
def get_mortgage_applications():
    # Return list of applications
    pass

@convonet_todo_bp.route('/api/mortgage/applications/<application_id>', methods=['GET'])
def get_mortgage_application(application_id):
    # Return application details
    pass
```

## Usage Flow

### Step 1: Financial Review

**User:** "I want to apply for a mortgage"

**Bot:** "Great! Let's start by reviewing your financial situation. Do you know your current credit score?"

**User:** "My credit score is 720"

**Bot:** (Uses `update_mortgage_financial_info` with credit_score=720)
"Excellent! A credit score of 720 is well above the minimum requirement of 620. What's your monthly income?"

**User:** "I make $6000 per month"

**Bot:** (Uses `update_mortgage_financial_info` with monthly_income=6000)
"Thank you. What are your total monthly debt payments?"

**User:** "I have a credit card with $200 monthly payment"

**Bot:** (Uses `add_mortgage_debt` with debt_type="credit_card", monthly_payment=200)
"Got it. Any other debts?"

**Bot:** (Uses `calculate_dti_ratio`)
"Your debt-to-income ratio is 3.33%, which is excellent! You're well within the preferred range of 43%."

### Step 2: Document Collection

**Bot:** "Now let's gather the required documents. You'll need identification, income verification, assets, and down payment source documentation."

**User:** "I uploaded my pay stub"

**Bot:** (Uses `upload_mortgage_document` with document_type="income_paystub")
"Thank you! I've recorded your pay stub. Next, we'll need your W-2 forms from the last two years."

**User:** "What documents do I still need?"

**Bot:** (Uses `get_missing_documents`)
"You still need: W-2 forms, tax returns, bank statements, and identification documents."

## Dashboard Features

### Statistics Cards
- Draft Applications
- Under Review
- Document Collection
- Pre-Approved
- Approved

### Application List
- Application ID
- User ID
- Status badge
- Credit Score
- DTI Ratio
- Progress bar
- Document count
- Created date

### Detail View
- Status & Progress
- Financial Information
- Documents list with status
- Debts list
- Action buttons

## Validation Rules

### Credit Score
- Minimum: 620 for conventional loans
- Warning if below 620

### DTI Ratio
- Preferred: Below 43%
- Acceptable: 43-50%
- Warning if above 50%

### Down Payment
- Conventional: Typically 20%
- FHA: 3.5%

### Closing Costs
- Typically 2-5% of loan amount

## Document Types

### Identification
- `identification` - Government-issued ID
- Social Security number (stored in metadata)

### Income & Employment
- `income_paystub` - Pay stubs (last 30 days)
- `income_w2` - W-2 forms (last 2 years)
- `income_tax_return` - Tax returns (last 2 years)
- `income_pnl` - Profit & loss (self-employed)
- `income_1099` - 1099 forms (self-employed)

### Assets
- `asset_bank_statement` - Bank statements (2-3 months)
- `asset_investment` - Investment statements
- `asset_retirement` - Retirement accounts (401k, IRA)

### Debts
- `debt_credit_card` - Credit card statements
- `debt_student_loan` - Student loan statements
- `debt_auto_loan` - Auto loan statements

### Down Payment
- `down_payment_source` - Source documentation
- `down_payment_gift_letter` - Gift letters

## Status Flow

```
draft
  ↓
financial_review (when credit score, income, debt collected)
  ↓
document_collection (when financial review completed)
  ↓
document_verification (when documents uploaded)
  ↓
under_review (manual review)
  ↓
pre_approved / approved / rejected
```

## API Integration

The dashboard expects these API endpoints:

### GET `/api/mortgage/applications`
Returns:
```json
{
  "success": true,
  "applications": [
    {
      "application_id": "uuid",
      "user_id": "uuid",
      "status": "financial_review",
      "credit_score": 720,
      "dti_ratio": 3.33,
      "completion_percentage": 33.33,
      "documents_count": 2,
      "created_at": "2025-01-15T10:00:00Z"
    }
  ]
}
```

### GET `/api/mortgage/applications/<application_id>`
Returns:
```json
{
  "success": true,
  "application": {
    "application_id": "uuid",
    "status": "financial_review",
    "credit_score": 720,
    "monthly_income": 6000,
    "monthly_debt": 200,
    "dti_ratio": 3.33,
    "documents": [...],
    "debts": [...],
    "completion_percentage": 33.33
  }
}
```

## Next Steps

1. **Integrate with Voice Bot**: Add mortgage agent to voice server
2. **File Upload**: Implement actual file upload functionality
3. **Document Verification**: Add verification workflow
4. **Notifications**: Add email/SMS notifications for status changes
5. **Reporting**: Add analytics and reporting features

## Testing

### Test Financial Review
```python
# Create application
create_mortgage_application(user_id="...")

# Update financial info
update_mortgage_financial_info(
    user_id="...",
    credit_score=720,
    monthly_income=6000,
    monthly_debt=200
)

# Calculate DTI
calculate_dti_ratio(user_id="...")
```

### Test Document Upload
```python
upload_mortgage_document(
    user_id="...",
    document_type="income_paystub",
    document_name="pay_stub_jan_2025.pdf"
)
```

## Troubleshooting

### MCP Tools Not Loading
- Check MCP configuration file
- Verify database connection
- Check environment variables

### Dashboard Not Loading
- Verify API endpoints are registered
- Check browser console for errors
- Ensure database tables exist

### Documents Not Saving
- Check file upload permissions
- Verify document_type enum values
- Check database constraints
