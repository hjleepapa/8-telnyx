# Healthcare Payer Agent - Test Scenarios

## Setup

### 1. Run the main migration (if not done)
```bash
python migrations/run_healthcare_payer_migration.py
```

### 2. Seed test data for your user
```bash
# Connect to your database and run:
psql $DB_URI -f migrations/seed_healthcare_test_data.sql
```

Or via Render Shell:
```bash
python -c "
from sqlalchemy import create_engine, text
import os
engine = create_engine(os.getenv('DB_URI'))
with open('migrations/seed_healthcare_test_data.sql') as f:
    sql = f.read()
with engine.connect() as conn:
    conn.execute(text(sql))
    conn.commit()
print('Done!')
"
```

---

## Test Scenarios

### Scenario 1: Check Eligibility
**Voice Input**: "Am I covered?" or "Is my insurance active?"

**Expected Response**: 
- Confirms coverage is active
- Mentions member number and plan name
- States coverage dates

---

### Scenario 2: Check Deductible Status
**Voice Input**: "Have I met my deductible?" or "How much is left on my deductible?"

**Expected Response**:
- Shows $750 of $1,500 deductible has been met
- States $750 remaining
- Explains what deductible means

---

### Scenario 3: Check Out-of-Pocket Status
**Voice Input**: "What's my out-of-pocket maximum?" or "How close am I to my max?"

**Expected Response**:
- Shows $1,250 of $6,500 OOP max met
- Explains that after $6,500, insurance pays 100%

---

### Scenario 4: Search Claims
**Voice Input**: "Show me my recent claims" or "Check my claim status"

**Expected Response**:
- Lists 4 claims:
  1. Office visit - Paid ($30 member responsibility)
  2. MRI - Denied (no prior auth)
  3. Specialist visit - Processing
  4. Lab work - Partially approved

---

### Scenario 5: Get Denied Claim Details (Advanced Reasoning!)
**Voice Input**: "Why was my MRI claim denied?"

**Expected Response**:
- Finds the denied MRI claim (CLM202501200002)
- Explains denial reason: "Prior authorization was required but not obtained"
- Offers option to file an appeal
- This demonstrates multi-step reasoning:
  1. Search claims to find denied ones
  2. Get details of the MRI claim
  3. Explain the denial reason in plain language
  4. Offer next steps

---

### Scenario 6: File an Appeal
**Voice Input**: "I want to appeal my denied claim" or "File an appeal for the MRI"

**Expected Response**:
- Files appeal for the denied claim
- Provides appeal reference number
- Explains 30-60 day timeline for decision
- Confirms appeal was submitted

---

### Scenario 7: Check Benefits Coverage
**Voice Input**: "Is physical therapy covered?" or "What's my copay for a specialist?"

**Expected Response**:
- Confirms coverage
- States copay amount ($40 for PT, $50 for specialist)
- Mentions if prior auth is required
- Notes any visit limits (30 PT visits/year)

---

### Scenario 8: Prior Authorization Status
**Voice Input**: "What's the status of my authorization?" or "Check my prior auth"

**Expected Response**:
- Lists authorizations:
  1. Physical Therapy - APPROVED (12 sessions, valid until April)
  2. Knee Replacement - PENDING (submitted Jan 25)

---

### Scenario 9: Request Prior Authorization
**Voice Input**: "I need approval for an MRI" or "Do I need prior authorization for surgery?"

**Expected Response**:
- Confirms MRI/surgery requires prior auth
- Asks for provider information
- Submits request if info provided
- Gives reference number and expected timeline

---

### Scenario 10: Find a Doctor
**Voice Input**: "Find me a cardiologist" or "I need a heart doctor"

**Expected Response**:
- Lists in-network cardiologists
- Shows Dr. Michael Chen (Tier 1 - lowest cost)
- Provides address and phone
- Mentions quality rating

---

### Scenario 11: Check if Provider is In-Network
**Voice Input**: "Is Dr. Chen in my network?"

**Expected Response**:
- Confirms Dr. Chen is in-network
- States network tier (Tier 1 - Preferred)
- Explains cost implications

---

### Scenario 12: Get EOB (Explanation of Benefits)
**Voice Input**: "Explain my bill" or "Show me the EOB for my office visit"

**Expected Response**:
- Shows what provider charged ($150)
- Shows what plan allows ($120)
- Shows what plan paid ($90)
- Shows member responsibility ($30 copay)
- Explains the breakdown

---

### Scenario 13: Care Programs
**Voice Input**: "What wellness programs are available?"

**Expected Response**:
- Lists available programs:
  - Diabetes Management
  - Heart Health
  - Wellness 360 (already enrolled - 25% complete)
  - Maternity Care
- Mentions features (coaching, monitoring, rewards)

---

### Scenario 14: Preventive Care
**Voice Input**: "What preventive care do I get?" or "When should I get a checkup?"

**Expected Response**:
- Lists covered preventive services (100% covered)
- Annual physical
- Flu shot
- Age-appropriate screenings (mammogram, colonoscopy)

---

## Complex Multi-Step Scenarios (Hackathon Demo!)

### Scenario A: Full Claim Investigation Flow
**Voice Input**: "My claim was denied and I don't understand why"

**Expected Agent Flow**:
1. `search_claims()` - Find denied claims
2. `get_claim_details()` - Get full details
3. Explain denial reason in plain language
4. `check_prior_auth_required()` - Verify if auth was needed
5. Offer to `file_claim_appeal()` if appropriate
6. Provide appeal reference and timeline

---

### Scenario B: Cost Estimation
**Voice Input**: "How much will knee surgery cost me?"

**Expected Agent Flow**:
1. `check_benefit_coverage('surgery')` - Verify coverage
2. `get_deductible_status()` - Check remaining deductible
3. `get_out_of_pocket_status()` - Check OOP progress
4. `check_prior_auth_required('knee replacement')` - Confirm auth needed
5. Calculate estimated member responsibility
6. Explain: "After your remaining $750 deductible, you'd pay 20% coinsurance up to your $6,500 max"

---

### Scenario C: New Patient Setup
**Voice Input**: "I'm new to this plan, what do I need to know?"

**Expected Agent Flow**:
1. `check_eligibility()` - Confirm active coverage
2. `get_benefits_summary()` - Overview of plan
3. `get_deductible_status()` - Starting point
4. `get_preventive_care()` - What's covered at 100%
5. `get_care_programs()` - Available programs
6. Summarize key info conversationally

---

## Voice Output Validation

Make sure the agent:
- ✅ Does NOT use markdown (no **, no *, no #)
- ✅ Does NOT use numbered lists with periods
- ✅ Uses natural speech ("first, second, third" instead of "1. 2. 3.")
- ✅ Spells out abbreviations ("E O B" not "EOB")
- ✅ Is concise and conversational
- ✅ Shows empathy for healthcare concerns

---

## Troubleshooting

### Agent not recognizing healthcare intent?
Check these trigger keywords:
- "claim", "insurance", "coverage", "eligibility"
- "deductible", "copay", "out of pocket"
- "prior auth", "authorization", "approval"
- "find a doctor", "in network", "provider"
- "benefits", "what's covered"

### Tools not found?
1. Check MCP config includes "healthcare" server
2. Verify `db_healthcare_payer.py` is in `convonet/mcps/local_servers/`
3. Restart the application to reload MCP tools

### No data returned?
1. Verify migration ran successfully
2. Check if test data was seeded
3. Confirm user is linked to a healthcare_member record
