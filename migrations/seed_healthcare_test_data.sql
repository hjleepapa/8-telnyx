-- Healthcare Payer Test Data Seed
-- Creates a test member with claims, prior auths, and accumulations for validation

-- Note: This assumes you have a user in users_anthropic table
-- Replace the user_id below with an actual user UUID from your database

-- First, let's create a variable for the test user
-- You'll need to replace this with an actual user_id from your users_anthropic table
DO $$
DECLARE
    test_user_id UUID;
    test_plan_id UUID;
    test_member_id UUID;
    test_claim_id UUID;
    test_auth_id UUID;
BEGIN
    -- Get the first user from users_anthropic (or specify a specific user)
    SELECT id INTO test_user_id FROM users_anthropic LIMIT 1;
    
    IF test_user_id IS NULL THEN
        RAISE NOTICE 'No user found in users_anthropic table. Please create a user first.';
        RETURN;
    END IF;
    
    RAISE NOTICE 'Using user_id: %', test_user_id;
    
    -- Get the PPO Gold plan
    SELECT id INTO test_plan_id FROM healthcare_plans WHERE plan_code = 'PPO-GOLD-2025';
    
    IF test_plan_id IS NULL THEN
        RAISE NOTICE 'Plan not found. Run the main migration first.';
        RETURN;
    END IF;
    
    -- Check if member already exists
    SELECT id INTO test_member_id FROM healthcare_members WHERE user_id = test_user_id;
    
    IF test_member_id IS NULL THEN
        -- Create test member
        INSERT INTO healthcare_members (
            user_id, member_number, group_number, plan_id, plan_name,
            eligibility_status, coverage_start_date, coverage_end_date,
            date_of_birth, relationship_to_subscriber
        ) VALUES (
            test_user_id,
            'MBR' || LPAD(FLOOR(RANDOM() * 1000000)::TEXT, 6, '0'),
            'GRP-CONVONET-001',
            test_plan_id,
            'Convonet Gold PPO',
            'active',
            '2025-01-01',
            '2025-12-31',
            '1985-06-15',
            'self'
        ) RETURNING id INTO test_member_id;
        
        RAISE NOTICE 'Created member with id: %', test_member_id;
    ELSE
        RAISE NOTICE 'Member already exists with id: %', test_member_id;
    END IF;
    
    -- Create member accumulations for current year
    INSERT INTO member_accumulations (
        member_id, plan_year,
        individual_deductible_met_in_network,
        individual_deductible_met_out_of_network,
        individual_oop_met_in_network,
        individual_oop_met_out_of_network
    ) VALUES (
        test_member_id, 2025,
        750.00,  -- Met $750 of $1500 deductible
        0.00,
        1250.00, -- Met $1250 of $6500 OOP max
        0.00
    ) ON CONFLICT (member_id, plan_year) DO UPDATE SET
        individual_deductible_met_in_network = 750.00,
        individual_oop_met_in_network = 1250.00;
    
    RAISE NOTICE 'Created/updated member accumulations';
    
    -- Create sample claims
    
    -- Claim 1: Paid claim (office visit)
    INSERT INTO healthcare_claims (
        member_id, claim_number, service_date, place_of_service,
        provider_npi, provider_name, provider_network_tier,
        diagnosis_codes, procedure_codes,
        billed_amount, allowed_amount, paid_amount, member_responsibility,
        deductible_applied, copay_applied, coinsurance_applied,
        status, received_date, processed_date, paid_date
    ) VALUES (
        test_member_id,
        'CLM202501150001',
        '2025-01-15',
        'Office',
        '1234567890',
        'Dr. Sarah Johnson',
        'tier_1',
        '["Z00.00"]',
        '["99213"]',
        150.00,
        120.00,
        90.00,
        30.00,
        0.00,
        30.00,
        0.00,
        'paid',
        '2025-01-16',
        '2025-01-20',
        '2025-01-22'
    ) ON CONFLICT (claim_number) DO NOTHING;
    
    -- Claim 2: Denied claim (MRI without prior auth)
    INSERT INTO healthcare_claims (
        member_id, claim_number, service_date, place_of_service,
        provider_npi, provider_name, provider_network_tier,
        diagnosis_codes, procedure_codes,
        billed_amount, allowed_amount, paid_amount, member_responsibility,
        status, denial_reason, denial_details,
        received_date, processed_date
    ) VALUES (
        test_member_id,
        'CLM202501200002',
        '2025-01-20',
        'Outpatient Imaging Center',
        '2345678901',
        'Bay Area Imaging',
        'tier_2',
        '["M54.5"]',
        '["70553"]',
        2500.00,
        NULL,
        0.00,
        2500.00,
        'denied',
        'no_prior_auth',
        'Prior authorization was required for MRI Brain (CPT 70553) but was not obtained before the service date.',
        '2025-01-21',
        '2025-01-25'
    ) ON CONFLICT (claim_number) DO NOTHING;
    
    -- Claim 3: Processing claim (specialist visit)
    INSERT INTO healthcare_claims (
        member_id, claim_number, service_date, place_of_service,
        provider_npi, provider_name, provider_network_tier,
        diagnosis_codes, procedure_codes,
        billed_amount,
        status, received_date
    ) VALUES (
        test_member_id,
        'CLM202501220003',
        '2025-01-22',
        'Specialist Office',
        '2345678901',
        'Dr. Michael Chen',
        'tier_1',
        '["I10"]',
        '["99214"]',
        250.00,
        'processing',
        '2025-01-23'
    ) ON CONFLICT (claim_number) DO NOTHING;
    
    -- Claim 4: Partially approved claim (lab work)
    INSERT INTO healthcare_claims (
        member_id, claim_number, service_date, place_of_service,
        provider_npi, provider_name, provider_network_tier,
        diagnosis_codes, procedure_codes,
        billed_amount, allowed_amount, paid_amount, member_responsibility,
        deductible_applied, copay_applied, coinsurance_applied,
        status, received_date, processed_date
    ) VALUES (
        test_member_id,
        'CLM202501100004',
        '2025-01-10',
        'Laboratory',
        '3456789012',
        'Quest Diagnostics',
        'tier_1',
        '["Z00.00", "E11.9"]',
        '["80053", "83036"]',
        450.00,
        380.00,
        320.00,
        60.00,
        50.00,
        0.00,
        10.00,
        'partially_approved',
        '2025-01-11',
        '2025-01-15'
    ) ON CONFLICT (claim_number) DO NOTHING;
    
    RAISE NOTICE 'Created sample claims';
    
    -- Create prior authorization (approved)
    INSERT INTO prior_authorizations (
        member_id, auth_number,
        procedure_code, procedure_description, diagnosis_codes,
        requesting_provider_npi, requesting_provider_name,
        servicing_provider_npi, servicing_provider_name,
        clinical_notes, status,
        approved_units, approved_from_date, approved_to_date,
        requested_date, decision_date, expiration_date
    ) VALUES (
        test_member_id,
        'AUTH202501150001',
        '97110',
        'Physical Therapy - Therapeutic Exercises',
        '["M54.5"]',
        '1234567890',
        'Dr. Sarah Johnson',
        '4567890123',
        'Bay Area Physical Therapy',
        'Patient has chronic lower back pain. Requesting 12 PT sessions.',
        'approved',
        12,
        '2025-01-20',
        '2025-04-20',
        '2025-01-15',
        '2025-01-17',
        '2025-04-20'
    ) ON CONFLICT (auth_number) DO NOTHING;
    
    -- Create prior authorization (pending)
    INSERT INTO prior_authorizations (
        member_id, auth_number,
        procedure_code, procedure_description, diagnosis_codes,
        requesting_provider_npi, requesting_provider_name,
        clinical_notes, status, is_urgent,
        requested_date
    ) VALUES (
        test_member_id,
        'AUTH202501250002',
        '27447',
        'Total Knee Replacement',
        '["M17.11"]',
        '4567890123',
        'Dr. David Martinez',
        'Patient has severe osteoarthritis of right knee. Conservative treatment failed. Requesting TKR.',
        'pending',
        false,
        '2025-01-25'
    ) ON CONFLICT (auth_number) DO NOTHING;
    
    RAISE NOTICE 'Created prior authorizations';
    
    -- Enroll member in a care program
    INSERT INTO member_care_programs (
        member_id, program_id, is_active, completion_percentage
    ) SELECT 
        test_member_id,
        cp.id,
        true,
        25.00
    FROM care_programs cp
    WHERE cp.program_code = 'WELLNESS-360'
    ON CONFLICT (member_id, program_id) DO NOTHING;
    
    RAISE NOTICE 'Enrolled member in wellness program';
    
    RAISE NOTICE '✅ Test data seeding complete!';
    RAISE NOTICE 'Test member_id: %', test_member_id;
    
END $$;

-- Verify the data
SELECT 'Members' as table_name, COUNT(*) as count FROM healthcare_members
UNION ALL
SELECT 'Claims', COUNT(*) FROM healthcare_claims
UNION ALL
SELECT 'Prior Auths', COUNT(*) FROM prior_authorizations
UNION ALL
SELECT 'Accumulations', COUNT(*) FROM member_accumulations
UNION ALL
SELECT 'Care Program Enrollments', COUNT(*) FROM member_care_programs;
