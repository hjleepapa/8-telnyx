"""
Seed Healthcare Test Data
Creates sample claims, prior auths, and accumulations for testing
"""

import os
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import random

load_dotenv()

def seed_test_data(user_id: str = None):
    """
    Seed test healthcare data for a specific user.
    
    Args:
        user_id: UUID of user to create member for. If None, uses first user in DB.
    """
    db_uri = os.getenv("DB_URI")
    if not db_uri:
        print("❌ DB_URI not set")
        sys.exit(1)
    
    print("🔧 Connecting to database...")
    engine = create_engine(db_uri, pool_pre_ping=True)
    Session = sessionmaker(bind=engine)
    db = Session()
    
    try:
        # Get user_id if not provided
        if not user_id:
            result = db.execute(text("SELECT id FROM users_anthropic LIMIT 1"))
            row = result.fetchone()
            if not row:
                print("❌ No users found in users_anthropic table")
                print("   Please create a user first or provide a user_id")
                return
            user_id = str(row[0])
        
        print(f"📝 Using user_id: {user_id}")
        
        # Get plan
        result = db.execute(text("SELECT id FROM healthcare_plans WHERE plan_code = 'PPO-GOLD-2025'"))
        row = result.fetchone()
        if not row:
            print("❌ Plan not found. Run run_healthcare_payer_migration.py first")
            return
        plan_id = str(row[0])
        
        # Check if member exists
        result = db.execute(text(
            "SELECT id FROM healthcare_members WHERE user_id = :user_id"
        ), {"user_id": user_id})
        row = result.fetchone()
        
        if row:
            member_id = str(row[0])
            print(f"✅ Member already exists: {member_id}")
        else:
            # Create member
            member_id = str(uuid4())
            member_number = f"MBR{random.randint(100000, 999999)}"
            
            db.execute(text("""
                INSERT INTO healthcare_members (
                    id, user_id, member_number, group_number, plan_id, plan_name,
                    eligibility_status, coverage_start_date, coverage_end_date,
                    date_of_birth, relationship_to_subscriber
                ) VALUES (
                    :id, :user_id, :member_number, 'GRP-CONVONET-001', :plan_id,
                    'Convonet Gold PPO', 'active', '2025-01-01', '2025-12-31',
                    '1985-06-15', 'self'
                )
            """), {
                "id": member_id,
                "user_id": user_id,
                "member_number": member_number,
                "plan_id": plan_id
            })
            print(f"✅ Created member: {member_number}")
        
        # Create accumulations
        db.execute(text("""
            INSERT INTO member_accumulations (
                id, member_id, plan_year,
                individual_deductible_met_in_network,
                individual_oop_met_in_network
            ) VALUES (
                :id, :member_id, 2025, 750.00, 1250.00
            ) ON CONFLICT (member_id, plan_year) DO UPDATE SET
                individual_deductible_met_in_network = 750.00,
                individual_oop_met_in_network = 1250.00
        """), {"id": str(uuid4()), "member_id": member_id})
        print("✅ Created accumulations ($750 deductible met, $1250 OOP met)")
        
        # Create claims
        claims_data = [
            {
                "claim_number": "CLM202501150001",
                "service_date": "2025-01-15",
                "provider_name": "Dr. Sarah Johnson",
                "provider_npi": "1234567890",
                "billed": 150.00,
                "allowed": 120.00,
                "paid": 90.00,
                "member_resp": 30.00,
                "copay": 30.00,
                "status": "paid",
                "desc": "Office visit - Paid"
            },
            {
                "claim_number": "CLM202501200002",
                "service_date": "2025-01-20",
                "provider_name": "Bay Area Imaging",
                "provider_npi": "2345678901",
                "billed": 2500.00,
                "allowed": None,
                "paid": 0.00,
                "member_resp": 2500.00,
                "copay": 0.00,
                "status": "denied",
                "denial_reason": "no_prior_auth",
                "denial_details": "Prior authorization was required for MRI Brain but was not obtained.",
                "desc": "MRI - Denied (no prior auth)"
            },
            {
                "claim_number": "CLM202501220003",
                "service_date": "2025-01-22",
                "provider_name": "Dr. Michael Chen",
                "provider_npi": "2345678901",
                "billed": 250.00,
                "status": "processing",
                "desc": "Specialist visit - Processing"
            },
            {
                "claim_number": "CLM202501100004",
                "service_date": "2025-01-10",
                "provider_name": "Quest Diagnostics",
                "provider_npi": "3456789012",
                "billed": 450.00,
                "allowed": 380.00,
                "paid": 320.00,
                "member_resp": 60.00,
                "deductible": 50.00,
                "coinsurance": 10.00,
                "status": "partially_approved",
                "desc": "Lab work - Partially approved"
            }
        ]
        
        for claim in claims_data:
            try:
                db.execute(text("""
                    INSERT INTO healthcare_claims (
                        id, member_id, claim_number, service_date,
                        provider_name, provider_npi, provider_network_tier,
                        billed_amount, allowed_amount, paid_amount, member_responsibility,
                        deductible_applied, copay_applied, coinsurance_applied,
                        status, denial_reason, denial_details,
                        received_date
                    ) VALUES (
                        :id, :member_id, :claim_number, :service_date,
                        :provider_name, :provider_npi, 'tier_1',
                        :billed, :allowed, :paid, :member_resp,
                        :deductible, :copay, :coinsurance,
                        :status, :denial_reason, :denial_details,
                        :received_date
                    ) ON CONFLICT (claim_number) DO NOTHING
                """), {
                    "id": str(uuid4()),
                    "member_id": member_id,
                    "claim_number": claim["claim_number"],
                    "service_date": claim["service_date"],
                    "provider_name": claim["provider_name"],
                    "provider_npi": claim["provider_npi"],
                    "billed": claim["billed"],
                    "allowed": claim.get("allowed"),
                    "paid": claim.get("paid", 0),
                    "member_resp": claim.get("member_resp", 0),
                    "deductible": claim.get("deductible", 0),
                    "copay": claim.get("copay", 0),
                    "coinsurance": claim.get("coinsurance", 0),
                    "status": claim["status"],
                    "denial_reason": claim.get("denial_reason"),
                    "denial_details": claim.get("denial_details"),
                    "received_date": claim["service_date"]
                })
                print(f"✅ Created claim: {claim['desc']}")
            except Exception as e:
                print(f"⚠️  Claim {claim['claim_number']}: {e}")
        
        # Create prior authorizations
        auths_data = [
            {
                "auth_number": "AUTH202501150001",
                "procedure_code": "97110",
                "procedure_desc": "Physical Therapy - Therapeutic Exercises",
                "provider_name": "Bay Area Physical Therapy",
                "status": "approved",
                "approved_units": 12,
                "desc": "PT - Approved (12 sessions)"
            },
            {
                "auth_number": "AUTH202501250002",
                "procedure_code": "27447",
                "procedure_desc": "Total Knee Replacement",
                "provider_name": "Dr. David Martinez",
                "status": "pending",
                "desc": "Knee Replacement - Pending"
            }
        ]
        
        for auth in auths_data:
            try:
                db.execute(text("""
                    INSERT INTO prior_authorizations (
                        id, member_id, auth_number,
                        procedure_code, procedure_description,
                        requesting_provider_name, status,
                        approved_units, requested_date
                    ) VALUES (
                        :id, :member_id, :auth_number,
                        :procedure_code, :procedure_desc,
                        :provider_name, :status,
                        :approved_units, :requested_date
                    ) ON CONFLICT (auth_number) DO NOTHING
                """), {
                    "id": str(uuid4()),
                    "member_id": member_id,
                    "auth_number": auth["auth_number"],
                    "procedure_code": auth["procedure_code"],
                    "procedure_desc": auth["procedure_desc"],
                    "provider_name": auth["provider_name"],
                    "status": auth["status"],
                    "approved_units": auth.get("approved_units"),
                    "requested_date": "2025-01-15"
                })
                print(f"✅ Created prior auth: {auth['desc']}")
            except Exception as e:
                print(f"⚠️  Auth {auth['auth_number']}: {e}")
        
        db.commit()
        
        print("\n" + "=" * 50)
        print("✅ Test data seeding complete!")
        print("=" * 50)
        print(f"\nMember ID: {member_id}")
        print(f"User ID: {user_id}")
        print("\nTest scenarios ready:")
        print("  - 4 claims (paid, denied, processing, partial)")
        print("  - 2 prior auths (approved PT, pending knee surgery)")
        print("  - Accumulations ($750 deductible, $1250 OOP met)")
        print("\nTry these voice commands:")
        print('  "Check my claims"')
        print('  "Why was my MRI denied?"')
        print('  "How much of my deductible have I met?"')
        print('  "What\'s the status of my authorization?"')
        
    except Exception as e:
        db.rollback()
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    # Optionally pass user_id as command line argument
    user_id = sys.argv[1] if len(sys.argv) > 1 else None
    seed_test_data(user_id)
