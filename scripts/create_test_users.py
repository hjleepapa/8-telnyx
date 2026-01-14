#!/usr/bin/env python3
"""
Script to create 10 test users in the users_anthropic table

Usage:
    python scripts/create_test_users.py

This script creates 10 test users with:
- Unique emails (user1@test.com, user2@test.com, ...)
- Unique usernames (user1, user2, ...)
- Hashed passwords (default: "password123" for all)
- Unique voice PINs (1234, 1235, 1236, ...)
- All users are active and verified
"""

import os
import sys
from datetime import datetime, timezone

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from convonet.models.user_models import User
from convonet.security.auth import jwt_auth

def init_database():
    """Initialize database connection"""
    db_uri = os.getenv('DB_URI')
    if not db_uri:
        print("❌ Error: DB_URI environment variable not set")
        print("   Please set DB_URI to your PostgreSQL connection string")
        print("   Example: export DB_URI='postgresql://user:pass@host:port/dbname'")
        sys.exit(1)
    
    try:
        engine = create_engine(db_uri, echo=False)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        return SessionLocal
    except Exception as e:
        print(f"❌ Error connecting to database: {e}")
        sys.exit(1)

def create_test_users():
    """Create 10 test users"""
    SessionLocal = init_database()
    
    # Default password for all test users
    default_password = "password123"
    password_hash = jwt_auth.hash_password(default_password)
    
    # User data template
    users_data = [
        {
            'email': f'user{i}@test.com',
            'username': f'user{i}',
            'first_name': f'Test',
            'last_name': f'User{i}',
            'voice_pin': str(1233 + i),  # 1234, 1235, 1236, ...
            'is_active': True,
            'is_verified': True
        }
        for i in range(1, 11)
    ]
    
    created_users = []
    errors = []
    
    with SessionLocal() as session:
        for user_data in users_data:
            try:
                # Check if user already exists
                existing_user = session.query(User).filter(
                    (User.email == user_data['email']) | 
                    (User.username == user_data['username']) |
                    (User.voice_pin == user_data['voice_pin'])
                ).first()
                
                if existing_user:
                    print(f"⚠️  User already exists: {user_data['email']} (skipping)")
                    continue
                
                # Create new user
                user = User(
                    email=user_data['email'],
                    username=user_data['username'],
                    password_hash=password_hash,
                    first_name=user_data['first_name'],
                    last_name=user_data['last_name'],
                    voice_pin=user_data['voice_pin'],
                    is_active=user_data['is_active'],
                    is_verified=user_data['is_verified'],
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)
                )
                
                session.add(user)
                session.flush()  # Get the ID
                
                created_users.append({
                    'id': str(user.id),
                    'email': user.email,
                    'username': user.username,
                    'voice_pin': user.voice_pin
                })
                
                print(f"✅ Created user: {user.email} (username: {user.username}, PIN: {user.voice_pin})")
                
            except Exception as e:
                error_msg = f"❌ Error creating user {user_data['email']}: {e}"
                print(error_msg)
                errors.append(error_msg)
                session.rollback()
                continue
        
        # Commit all users at once
        try:
            session.commit()
            print(f"\n✅ Successfully created {len(created_users)} users")
        except Exception as e:
            print(f"\n❌ Error committing users: {e}")
            session.rollback()
            errors.append(f"Commit error: {e}")
    
    # Print summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"✅ Created: {len(created_users)} users")
    print(f"❌ Errors: {len(errors)}")
    
    if created_users:
        print("\nCreated Users:")
        print("-" * 60)
        for user in created_users:
            print(f"  • {user['email']} (username: {user['username']}, PIN: {user['voice_pin']})")
    
    if errors:
        print("\nErrors:")
        print("-" * 60)
        for error in errors:
            print(f"  • {error}")
    
    print("\n" + "="*60)
    print("LOGIN CREDENTIALS")
    print("="*60)
    print("All users have the same password: password123")
    print("\nVoice PINs (for WebRTC assistant):")
    for user in created_users:
        print(f"  • {user['email']}: PIN {user['voice_pin']}")
    
    return len(created_users), len(errors)

if __name__ == "__main__":
    print("="*60)
    print("Creating 10 Test Users")
    print("="*60)
    print()
    
    try:
        created, errors = create_test_users()
        if created > 0:
            print(f"\n✅ Script completed successfully!")
            sys.exit(0)
        else:
            print(f"\n⚠️  No users were created (may already exist)")
            sys.exit(0)
    except KeyboardInterrupt:
        print("\n\n⚠️  Script interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
