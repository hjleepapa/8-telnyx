from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
from typing import List, Optional
from sqlalchemy import ForeignKey, String, text, Column, Boolean, Text, DateTime
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, Session
from uuid import UUID, uuid4
from datetime import datetime, timedelta, timezone
import os
import sys
import logging
import json
from pydantic import BaseModel
from enum import StrEnum
import pandas as pd

# Configure logging
# Force rebuild: 2025-10-06
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
# Google Calendar integration
try:
    from google_calendar import get_calendar_service
except ImportError:
    pass  #     
    import sys
    import os
    sys.path.append(os.path.dirname(__file__))
    try:
        from google_calendar import get_calendar_service
    except ImportError:
        pass  #         
        get_calendar_service = None

# Team collaboration imports
# Lazy import to avoid circular dependency - will be imported when needed
User = Team = TeamMembership = TeamRole = None

def _lazy_import_team_models():
    """Lazy import team models to avoid circular dependency"""
    global User, Team, TeamMembership, TeamRole
    if User is None:
        try:
            from convonet.models.user_models import User as U, Team as T, TeamMembership as TM, TeamRole as TR
            User, Team, TeamMembership, TeamRole = U, T, TM, TR
        except (ImportError, RuntimeError) as e:
            # If import fails, continue without team features
            pass

# Service Account Google Calendar integration
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    import base64
    import pickle
    import json
    
    def get_service_account_calendar_service():
        """Get Google Calendar service using service account credentials"""
        try:
            pass
            # Check for base64 encoded credentials in environment
            if os.getenv('GOOGLE_CREDENTIALS_B64'):
                pass  #                 
                creds_data = base64.b64decode(os.getenv('GOOGLE_CREDENTIALS_B64'))
                creds = service_account.Credentials.from_service_account_info(
                    json.loads(creds_data.decode('utf-8')),
                    scopes=['https://www.googleapis.com/auth/calendar']
                )
            # Check for base64 encoded token in environment
            elif os.getenv('GOOGLE_TOKEN_B64'):
                pass  #                 
                token_data = base64.b64decode(os.getenv('GOOGLE_TOKEN_B64'))
                creds = pickle.loads(token_data)
                if hasattr(creds, 'refresh_token') and creds.expired:
                    from google.auth.transport.requests import Request
                    creds.refresh(Request())
            # Check for local credentials.json file
            elif os.path.exists('credentials.json'):
                pass  #                 
                creds = service_account.Credentials.from_service_account_file(
                    'credentials.json',
                    scopes=['https://www.googleapis.com/auth/calendar']
                )
            else:
                pass
                # No Google Calendar credentials found
                return None
                
            service = build('calendar', 'v3', credentials=creds)
            # Google Calendar service created successfully
            return service
            
        except Exception as e:
            
            pass  #             
            return None
            
    def get_oauth2_calendar_service():
        """Get Google Calendar service using OAuth2 client credentials"""
        try:
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request
            
            creds = None
            
            # Check for OAuth2 token in environment
            if os.getenv('GOOGLE_OAUTH2_TOKEN_B64'):
                msg = "🔧 Using GOOGLE_OAUTH2_TOKEN_B64 environment variable"
                # print(msg, flush=True)  # Removed to avoid MCP protocol issues
                logging.info(msg)
                try:
                    token_data = base64.b64decode(os.getenv('GOOGLE_OAUTH2_TOKEN_B64'))
                    msg = f"🔧 Token data decoded, length: {len(token_data)} bytes"
                    # print(msg, flush=True)  # Removed to avoid MCP protocol issues
                    logging.info(msg)
                    
                    creds = pickle.loads(token_data)
                    msg = f"🔧 Credentials loaded: valid={creds.valid}, expired={creds.expired}"
                    # print(msg, flush=True)  # Removed to avoid MCP protocol issues
                    logging.info(msg)
                    
                    if creds.expired and creds.refresh_token:
                        msg = "🔧 Token expired, attempting refresh..."
                        # print(msg, flush=True)  # Removed to avoid MCP protocol issues
                        logging.info(msg)
                        try:
                            creds.refresh(Request())
                            msg = "✅ Token refreshed successfully"
                            # print(msg, flush=True)  # Removed to avoid MCP protocol issues
                            logging.info(msg)
                        except Exception as refresh_error:
                            msg = f"❌ Token refresh failed: {refresh_error}"
                            # print(msg, flush=True)  # Removed to avoid MCP protocol issues
                            logging.info(msg)
                            return None
                    
                    if not creds.valid:
                        msg = "❌ OAuth2 token is not valid after loading/refresh"
                        # print(msg, flush=True)  # Removed to avoid MCP protocol issues
                        logging.info(msg)
                        return None
                        
                except Exception as token_error:
                    msg = f"❌ Error loading OAuth2 token: {token_error}"
                    # print(msg, flush=True)  # Removed to avoid MCP protocol issues
                    logging.info(msg)
                    return None
                    
            # Check for local token.pickle file
            elif os.path.exists('token.pickle'):
                pass  #                 
                try:
                    with open('token.pickle', 'rb') as token:
                        creds = pickle.load(token)
                    if creds.expired and creds.refresh_token:
                        creds.refresh(Request())
                except Exception as file_error:
                    pass  #                     
                    return None
            
            # If no valid credentials, try to create OAuth2 credentials from environment
            if not creds or not creds.valid:
                pass  #                 
                
                # Check for OAuth2 client credentials in environment
                client_id = os.getenv('GOOGLE_CLIENT_ID')
                client_secret = os.getenv('GOOGLE_CLIENT_SECRET')
                
                if client_id and client_secret:
                
                    pass  #                     
                    
                    # Create OAuth2 flow
                    client_config = {
                        "installed": {
                            "client_id": client_id,
                            "client_secret": client_secret,
                            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                            "token_uri": "https://oauth2.googleapis.com/token",
                            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                            "redirect_uris": ["http://localhost"]
                        }
                    }
                    
                    flow = InstalledAppFlow.from_client_config(
                        client_config, 
                        ['https://www.googleapis.com/auth/calendar']
                    )
                    
                    # This will need user interaction for first time setup
                    # OAuth2 flow requires user interaction - this won't work in server environment
                    return None
                else:
                    pass
                    # No OAuth2 credentials found
                    return None
            
            # Building Google Calendar service with OAuth2 credentials...
            service = build('calendar', 'v3', credentials=creds)
            # Google Calendar OAuth2 service created successfully
            return service
            
        except Exception as e:
            
            pass  #             
            # Error type logged
            import traceback
            # Traceback logged
            return None

    def get_calendar_service():
        """Get Google Calendar service - tries OAuth2 first, then service account"""
        msg = "🔧 get_calendar_service: Starting authentication check..."
                    # print(msg, flush=True)  # Removed to avoid MCP protocol issues
        logging.info(msg)
        
        # Debug: Check what environment variables are available
        msg = "🔧 Environment variables check:"
                    # print(msg, flush=True)  # Removed to avoid MCP protocol issues
        logging.info(msg)
        
        oauth2_status = 'SET' if os.getenv('GOOGLE_OAUTH2_TOKEN_B64') else 'NOT SET'
        credentials_status = 'SET' if os.getenv('GOOGLE_CREDENTIALS_B64') else 'NOT SET'
        token_status = 'SET' if os.getenv('GOOGLE_TOKEN_B64') else 'NOT SET'
        client_id_status = 'SET' if os.getenv('GOOGLE_CLIENT_ID') else 'NOT SET'
        client_secret_status = 'SET' if os.getenv('GOOGLE_CLIENT_SECRET') else 'NOT SET'
        
        msg = f"  • GOOGLE_OAUTH2_TOKEN_B64: {oauth2_status}"
                    # print(msg, flush=True)  # Removed to avoid MCP protocol issues
        logging.info(msg)
        
        msg = f"  • GOOGLE_CREDENTIALS_B64: {credentials_status}"
                    # print(msg, flush=True)  # Removed to avoid MCP protocol issues
        logging.info(msg)
        
        msg = f"  • GOOGLE_TOKEN_B64: {token_status}"
                    # print(msg, flush=True)  # Removed to avoid MCP protocol issues
        logging.info(msg)
        
        msg = f"  • GOOGLE_CLIENT_ID: {client_id_status}"
                    # print(msg, flush=True)  # Removed to avoid MCP protocol issues
        logging.info(msg)
        
        msg = f"  • GOOGLE_CLIENT_SECRET: {client_secret_status}"
                    # print(msg, flush=True)  # Removed to avoid MCP protocol issues
        logging.info(msg)
        
        # Try OAuth2 first (for user's personal calendar)
        msg = "🔧 Trying OAuth2 authentication..."
                    # print(msg, flush=True)  # Removed to avoid MCP protocol issues
        logging.info(msg)
        
        oauth2_service = get_oauth2_calendar_service()
        if oauth2_service:
            msg = "✅ OAuth2 authentication successful"
                    # print(msg, flush=True)  # Removed to avoid MCP protocol issues
            logging.info(msg)
            return oauth2_service
        else:
            msg = "❌ OAuth2 authentication failed"
                    # print(msg, flush=True)  # Removed to avoid MCP protocol issues
            logging.info(msg)
        
        # Fallback to service account
        msg = "🔄 Falling back to service account authentication"
                    # print(msg, flush=True)  # Removed to avoid MCP protocol issues
        logging.info(msg)
        
        service_account_service = get_service_account_calendar_service()
        if service_account_service:
            msg = "✅ Service account authentication successful"
                    # print(msg, flush=True)  # Removed to avoid MCP protocol issues
            logging.info(msg)
            return service_account_service
        else:
            msg = "❌ Service account authentication also failed"
                    # print(msg, flush=True)  # Removed to avoid MCP protocol issues
            logging.info(msg)
            return None
    
except ImportError as e:
    
    pass  #     
    get_service_account_calendar_service = None

load_dotenv()

# ----------------------------
# SQLAlchemy Models
# ----------------------------

# Import shared Base class
from convonet.models.base import Base

# Team models are imported above from convonet.models.user_models
# No need to redefine them here


class DBTodo(Base):
    __tablename__ = "todos_anthropic"
    __table_args__ = {'extend_existing': True}

    id: Mapped[UUID] = mapped_column(primary_key=True, index=True, server_default=text("gen_random_uuid()"))
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"), onupdate=datetime.now)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    completed: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    priority: Mapped[str] = mapped_column(String, nullable=False, server_default=text("medium"))
    due_date: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    google_calendar_event_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    # Team collaboration fields
    creator_id: Mapped[Optional[UUID]] = mapped_column(PostgresUUID(as_uuid=True), nullable=True, index=True)  # User who created the todo
    assignee_id: Mapped[Optional[UUID]] = mapped_column(PostgresUUID(as_uuid=True), nullable=True, index=True)  # User assigned to the todo
    team_id: Mapped[Optional[UUID]] = mapped_column(PostgresUUID(as_uuid=True), nullable=True, index=True)      # Team the todo belongs to
    is_private: Mapped[bool] = mapped_column(nullable=False, server_default=text("false")) # Whether todo is private to creator
    
    # Relationships (will be defined after we import the models)
    creator = None  # Will be set up after importing user models
    assignee = None  # Will be set up after importing user models
    team = None  # Will be set up after importing team models


class DBReminder(Base):
    __tablename__ = "reminders_anthropic"
    __table_args__ = {'extend_existing': True}

    id: Mapped[UUID] = mapped_column(primary_key=True, index=True, server_default=text("gen_random_uuid()"))
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"), onupdate=datetime.now)
    reminder_text: Mapped[str] = mapped_column(String, nullable=False)
    importance: Mapped[str] = mapped_column(String, nullable=False, server_default=text("medium"))
    reminder_date: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    google_calendar_event_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class DBCalendarEvent(Base):
    __tablename__ = "calendar_events_anthropic"
    __table_args__ = {'extend_existing': True}

    id: Mapped[UUID] = mapped_column(primary_key=True, index=True, server_default=text("gen_random_uuid()"))
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"), onupdate=datetime.now)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    event_from: Mapped[datetime] = mapped_column(nullable=False)
    event_to: Mapped[datetime] = mapped_column(nullable=False)
    google_calendar_event_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class DBCallRecording(Base):
    __tablename__ = "call_recordings_anthropic"
    __table_args__ = {'extend_existing': True}

    id: Mapped[UUID] = mapped_column(primary_key=True, index=True, server_default=text("gen_random_uuid()"))
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    call_sid: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    from_number: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    to_number: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    recording_path: Mapped[str] = mapped_column(String, nullable=False)
    duration_seconds: Mapped[Optional[int]] = mapped_column(nullable=True)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(nullable=True)
    transcription: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'completed'"))  # completed, failed, processing 

# ----------------------------
# Pydantic Models
# ----------------------------

class TodoPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class Todo(BaseModel):
    id: UUID
    created_at: datetime
    updated_at: datetime
    title: str
    description: Optional[str]
    completed: bool
    priority: TodoPriority
    due_date: Optional[datetime]
    google_calendar_event_id: Optional[str]


class ReminderImportance(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class Reminder(BaseModel):
    id: UUID
    created_at: datetime
    updated_at: datetime
    reminder_text: str
    importance: ReminderImportance
    reminder_date: Optional[datetime]
    google_calendar_event_id: Optional[str]


class CalendarEvent(BaseModel):
    id: UUID
    created_at: datetime
    updated_at: datetime
    title: str
    description: Optional[str]
    event_from: datetime
    event_to: datetime
    google_calendar_event_id: Optional[str]


class CallRecording(BaseModel):
    id: UUID
    created_at: datetime
    call_sid: str
    from_number: Optional[str]
    to_number: Optional[str]
    recording_path: str
    duration_seconds: Optional[int]
    file_size_bytes: Optional[int]
    transcription: Optional[str]
    status: str

# ----------------------------
# DB Session
# ----------------------------

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Lazy database connection - don't connect at import time
db_uri = os.getenv("DB_URI")
engine = None
SessionLocal = None
_db_initialized = False

def _init_database():
    """Initialize database connection (lazy loading)."""
    global engine, SessionLocal, _db_initialized
    
    if _db_initialized:
        return
    
    _db_initialized = True
    
    if not db_uri:
    
        pass  #         
        return
    
    try:
        pass
        # print(...) # Removed to avoid MCP protocol issues
        
        # Configure engine based on database type
        if db_uri.startswith('sqlite'):
            # SQLite configuration (no connect_timeout)
            engine = create_engine(
                url=db_uri,
                pool_pre_ping=False,
                pool_size=1,
                max_overflow=0,
                pool_timeout=1,
                pool_recycle=1800
            )
        else:
            # PostgreSQL configuration (with connect_timeout)
            engine = create_engine(
                url=db_uri,
                pool_pre_ping=False,  # Skip pre-ping to avoid hanging
                pool_size=1,  # Minimal pool for MCP server
                max_overflow=0,  # No overflow
                pool_timeout=1,  # 1 second timeout
                pool_recycle=1800,  # Recycle connections every 30 mins
                connect_args={"connect_timeout": 1}  # 1 second connection timeout
            )
        
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        # Database connection configured successfully (no test)
    except Exception as e:
        pass  #         
        engine = None
        SessionLocal = None

# ----------------------------
# Helper Functions
# ----------------------------

def check_database_available():
    """Check if database is available."""
    try:
        _init_database()  # Lazy init on first use
    except Exception as e:
        pass  #         
        raise Exception(f"Database initialization failed: {str(e)}")
    
    if SessionLocal is None:
        raise Exception("Database not available - DB_URI not configured")

# ----------------------------
# MCP Server
# ----------------------------

mcp = FastMCP("db_todo")

@mcp.tool()
async def test_connection() -> str:
    """Test if the MCP server is working."""
    # print(...) # Removed to avoid MCP protocol issues
    return "MCP server is working!"

@mcp.tool()
async def test_env_vars() -> str:
    """Test environment variables loading."""
    # print(...) # Removed to avoid MCP protocol issues
    
    result = "🔧 Environment Variables Test:\n\n"
    result += f"• GOOGLE_OAUTH2_TOKEN_B64: {'✅ SET' if os.getenv('GOOGLE_OAUTH2_TOKEN_B64') else '❌ NOT SET'}\n"
    result += f"• GOOGLE_CREDENTIALS_B64: {'✅ SET' if os.getenv('GOOGLE_CREDENTIALS_B64') else '❌ NOT SET'}\n"
    result += f"• GOOGLE_TOKEN_B64: {'✅ SET' if os.getenv('GOOGLE_TOKEN_B64') else '❌ NOT SET'}\n"
    result += f"• GOOGLE_CLIENT_ID: {'✅ SET' if os.getenv('GOOGLE_CLIENT_ID') else '❌ NOT SET'}\n"
    result += f"• GOOGLE_CLIENT_SECRET: {'✅ SET' if os.getenv('GOOGLE_CLIENT_SECRET') else '❌ NOT SET'}\n"
    result += f"• DB_URI: {'✅ SET' if os.getenv('DB_URI') else '❌ NOT SET'}\n"
    
    # Test get_calendar_service function
    result += f"\n🔧 Function Tests:\n"
    result += f"• get_calendar_service: {'✅ Available' if get_calendar_service else '❌ None'}\n"
    
    if get_calendar_service:
        try:
            result += f"\n🔧 Testing get_calendar_service()...\n"
            calendar_service = get_calendar_service()
            if calendar_service:
                result += f"✅ Calendar service created successfully\n"
            else:
                result += f"❌ Calendar service returned None\n"
        except Exception as e:
            result += f"❌ Calendar service error: {e}\n"
    
    # print(...) # Removed to avoid MCP protocol issues
    return result

@mcp.tool()
async def simple_test() -> str:
    """Simple test without database."""
    # print(...) # Removed to avoid MCP protocol issues
    return "Simple test successful!"

@mcp.tool()
async def test_google_calendar() -> str:
    """Test Google Calendar service availability and credentials."""
    try:
        pass
        # print(...) # Removed to avoid MCP protocol issues
        
        # Check if get_calendar_service is available
        if not get_calendar_service:
            return "❌ Google Calendar service not available - get_calendar_service is None"
        
        # print(...) # Removed to avoid MCP protocol issues
        
        # Try to get the calendar service
        try:
            calendar_service = get_calendar_service()
            # print(...) # Removed to avoid MCP protocol issues
            
            # First, let's check what calendars are available
            try:
                calendar_list = calendar_service.calendarList().list().execute()
                calendars = calendar_list.get('items', [])
                # Available calendars
                for cal in calendars:
                    pass  # Calendar info logged elsewhere
            except Exception as list_error:
                pass  # Calendar list error handled elsewhere
            
            # Try to create a test event using proper Google Calendar API
            test_event_body = {
                'summary': "Test Event - Convonet Integration",
                'description': "This is a test event to verify Google Calendar integration. If you can see this, the integration is working!",
                'start': {
                    'dateTime': datetime.now(timezone.utc).isoformat(),
                    'timeZone': 'UTC',
                },
                'end': {
                    'dateTime': (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
                    'timeZone': 'UTC',
                },
            }
            
            created_event = calendar_service.events().insert(
                calendarId='primary', 
                body=test_event_body
            ).execute()
            
            test_event_id = created_event.get('id')
            organizer_email = created_event.get('organizer', {}).get('email', 'Unknown')
            # print(...) # Removed to avoid MCP protocol issues
            
            if test_event_id:
            
                pass  #                 
                
                # Try to delete the test event
                try:
                    calendar_service.events().delete(
                        calendarId='primary', 
                        eventId=test_event_id
                    ).execute()
                    success = True
                    if success:
                        pass  #                         
                        return f"✅ Google Calendar service is working correctly!\nTest event created and deleted: {test_event_id}\nEvent created by: {organizer_email}\n\n📋 To see events in your personal calendar, you need to share your calendar with: google-calendar-api2@dark-window-206618.iam.gserviceaccount.com"
                    else:
                        return f"⚠️  Google Calendar service created event but failed to delete it: {test_event_id}"
                except Exception as delete_error:
                    return f"⚠️  Google Calendar service created event but delete failed: {delete_error}"
            else:
                return "❌ Google Calendar service failed to create test event - returned None"
                
        except Exception as service_error:
                
            pass  #             
            return f"❌ Google Calendar service error: {service_error}"
            
    except Exception as e:
        error_msg = f"Error testing Google Calendar: {str(e)}"
        # print(...) # Removed to avoid MCP protocol issues
        return error_msg

@mcp.tool()
async def test_database() -> str:
    """Test database connection only."""
    try:
        pass
        # print(...) # Removed to avoid MCP protocol issues
        check_database_available()
        # print(...) # Removed to avoid MCP protocol issues
        
        with SessionLocal() as session:
            # print(...) # Removed to avoid MCP protocol issues
            result = session.execute(text("SELECT 1 as test")).fetchone()
            # print(...) # Removed to avoid MCP protocol issues
            return f"Database test successful: {result[0]}"
    except Exception as e:
        pass  #         
        return f"Database test failed: {str(e)}"

@mcp.tool()
async def create_todo(
    title: str,
    description: Optional[str] = None,
    priority: TodoPriority = TodoPriority.MEDIUM,
    due_date: Optional[datetime] = None,
    team_id: Optional[str] = None,
    assignee_id: Optional[str] = None,
    ) -> str:
    """Create a new todo item.
    
    Args:
        title: The title of the todo item.
        description: An optional description of the todo item.
        priority: The priority level of the todo. Options are: low, medium, high, urgent
        due_date: The due date for the todo item. If not specified, will automatically default to today's date.
        team_id: Optional team ID to assign the todo to a team.
        assignee_id: Optional user ID to assign the todo to a specific team member.

    Returns:
        The created todo item.
    """
    try:
        pass
        # print(...) # Removed to avoid MCP protocol issues
        # print(...) # Removed to avoid MCP protocol issues
        check_database_available()
        # print(...) # Removed to avoid MCP protocol issues
        
        with SessionLocal() as session:
            # print(...) # Removed to avoid MCP protocol issues
            # Set default due date to today if not provided
            if due_date is None:
                due_date = datetime.now(timezone.utc)
                
            # print(...) # Removed to avoid MCP protocol issues
            # print(...) # Removed to avoid MCP protocol issues
            
            new_todo = DBTodo(
                title=title,
                description=description,
                priority=priority.value,
                due_date=due_date,
                team_id=team_id,
                assignee_id=assignee_id,
                )
            # print(...) # Removed to avoid MCP protocol issues
            session.add(new_todo)
            # print(...) # Removed to avoid MCP protocol issues
            session.commit()
            # print(...) # Removed to avoid MCP protocol issues
            session.refresh(new_todo)
            # print(...) # Removed to avoid MCP protocol issues
            
            # Create corresponding Google Calendar event
            google_event_id = None
            # print(...) # Removed to avoid MCP protocol issues
            # print(...) # Removed to avoid MCP protocol issues
            # get_calendar_service type info
            
            # Check environment variables
            msg = "🔧 MCP create_todo: Environment variables check:"
                    # print(msg, flush=True)  # Removed to avoid MCP protocol issues
            logging.info(msg)
            
            oauth2_status = 'SET' if os.getenv('GOOGLE_OAUTH2_TOKEN_B64') else 'NOT SET'
            credentials_status = 'SET' if os.getenv('GOOGLE_CREDENTIALS_B64') else 'NOT SET'
            token_status = 'SET' if os.getenv('GOOGLE_TOKEN_B64') else 'NOT SET'
            client_id_status = 'SET' if os.getenv('GOOGLE_CLIENT_ID') else 'NOT SET'
            client_secret_status = 'SET' if os.getenv('GOOGLE_CLIENT_SECRET') else 'NOT SET'
            
            msg = f"  • GOOGLE_CREDENTIALS_B64 = {credentials_status}"
                    # print(msg, flush=True)  # Removed to avoid MCP protocol issues
            logging.info(msg)
            
            msg = f"  • GOOGLE_TOKEN_B64 = {token_status}"
                    # print(msg, flush=True)  # Removed to avoid MCP protocol issues
            logging.info(msg)
            
            msg = f"  • GOOGLE_OAUTH2_TOKEN_B64 = {oauth2_status}"
                    # print(msg, flush=True)  # Removed to avoid MCP protocol issues
            logging.info(msg)
            
            msg = f"  • GOOGLE_CLIENT_ID = {client_id_status}"
                    # print(msg, flush=True)  # Removed to avoid MCP protocol issues
            logging.info(msg)
            
            msg = f"  • GOOGLE_CLIENT_SECRET = {client_secret_status}"
                    # print(msg, flush=True)  # Removed to avoid MCP protocol issues
            logging.info(msg)
            
            # Skip Google Calendar sync for voice calls to avoid timeout
            # Database todo is created successfully - return immediately
            
            # Send Slack notification
            try:
                import sys
                # Add the convonet directory to the path
                convonet_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
                if convonet_path not in sys.path:
                    sys.path.append(convonet_path)
                
                from convonet.composio_tools import composio_manager
                slack_message = f"📝 New todo created: '{title}' with {priority.value} priority{due_str}"
                composio_manager.send_slack_message("#productivity", slack_message)
            except Exception as e:
                # Don't fail todo creation if Slack notification fails
                logging.warning(f"⚠️ Failed to send Slack notification: {e}")
            
            # Return simple success message
            due_str = f" due {new_todo.due_date.strftime('%b %d')}" if new_todo.due_date else ""
            return f"Todo '{title}' created successfully with {priority.value} priority{due_str}."
        
    except Exception as e:
        error_msg = f"Error executing tool create_todo: {str(e)}"
        # print(...) # Removed to avoid MCP protocol issues
        return error_msg

@mcp.tool()
async def get_todos() -> str:
    """Get all todo items.
    
    Returns:
        A list of all todo items.
    """
    with SessionLocal() as session:
        todos = session.query(DBTodo).all()
        todos_list = [Todo.model_validate(todo.__dict__).model_dump_json(indent=2) for todo in todos]
    sep = ",\n"
    return f"[{sep.join(todos_list)}]"

@mcp.tool()
async def complete_todo(id: UUID) -> str:
    """Mark a todo item as completed.
    
    Args:
        id: The id of the todo item to complete.

    Returns:
        The updated todo item.
    """
    with SessionLocal() as session:
        todo = session.query(DBTodo).filter(DBTodo.id == id).first()
        if not todo:
            return "Todo not found"
        
        todo.completed = True
        session.commit()
        
        # Google Calendar sync disabled
        # print(...) # Removed to avoid MCP protocol issues
        
        session.refresh(todo)
    
    return Todo.model_validate(todo.__dict__).model_dump_json(indent=2)

@mcp.tool()
async def update_todo(
    id: UUID,
    title: Optional[str] = None,
    description: Optional[str] = None,
    priority: Optional[TodoPriority] = None,
    due_date: Optional[datetime] = None,
    completed: Optional[bool] = None,
    ) -> str:
    """Update a todo item by id.
    
    Args:
        id: The id of the todo item to update.
        title: The new title of the todo item.
        description: The new description of the todo item.
        priority: The new priority level of the todo. Options are: low, medium, high, urgent
        due_date: The new due date for the todo item.
        completed: The new completion status of the todo item.

    Returns:
        The updated todo item.
    """
    with SessionLocal() as session:
        todo = session.query(DBTodo).filter(DBTodo.id == id).first()
        if not todo:
            return "Todo not found"
        
        if title:
            todo.title = title
        if description is not None:
            todo.description = description
        if priority:
            todo.priority = priority.value
        if due_date is not None:
            todo.due_date = due_date
        if completed is not None:
            todo.completed = completed

        session.commit()
        session.refresh(todo)
        
        # Update Google Calendar event if it exists
        if todo.google_calendar_event_id and get_calendar_service:
            try:
                pass
                # print(...) # Removed to avoid MCP protocol issues
                calendar_service = get_calendar_service()
                
                # Prepare update data
                update_data = {}
                if title:
                    update_data['title'] = f"Todo: {title}"
                if description is not None:
                    update_data['description'] = description or ""
                if due_date is not None:
                    update_data['start_time'] = due_date
                    update_data['end_time'] = due_date + timedelta(hours=1)
                
                if update_data:
                    success = calendar_service.update_event(
                        event_id=todo.google_calendar_event_id,
                        **update_data
                    )
                    if success:
                        pass  #                         
                    else:
                        pass
                        # print(...) # Removed to avoid MCP protocol issues
                else:
                    pass
                    # print(...) # Removed to avoid MCP protocol issues
            except Exception as calendar_error:
                pass  #                 
    
    return Todo.model_validate(todo.__dict__).model_dump_json(indent=2)

@mcp.tool()
async def delete_todo(id: UUID) -> str:
    """Delete a todo item by id.
    
    Args:
        id: The id of the todo item to delete.

    Returns:
        The deleted todo item.
    """
    with SessionLocal() as session:
        todo = session.query(DBTodo).filter(DBTodo.id == id).first()
        if not todo:
            return "Todo not found"
        
        # print(...) # Removed to avoid MCP protocol issues
        
        # Delete from Google Calendar if event exists
        if todo.google_calendar_event_id and get_calendar_service:
            try:
                pass
                # print(...) # Removed to avoid MCP protocol issues
                calendar_service = get_calendar_service()
                calendar_service.events().delete(
                    calendarId='primary', 
                    eventId=todo.google_calendar_event_id
                ).execute()
                success = True
                if success:
                    pass  #                     
                else:
                    pass
                    # print(...) # Removed to avoid MCP protocol issues
            except Exception as calendar_error:
                pass  #                 
        
        session.delete(todo)
        session.commit()
    
    return Todo.model_validate(todo.__dict__).model_dump_json(indent=2)

@mcp.tool()
async def create_reminder(
    reminder_text: str,
    importance: ReminderImportance = ReminderImportance.MEDIUM,
    reminder_date: Optional[datetime] = None,
    ) -> str:
    """Create a new reminder.
    
    Args:
        reminder_text: The text content of the reminder.
        importance: The importance level of the reminder. Options are: low, medium, high, urgent
        reminder_date: An optional date/time for the reminder.

    Returns:
        The created reminder.
    """
    try:
        pass
        # print(...) # Removed to avoid MCP protocol issues
        check_database_available()
        
        with SessionLocal() as session:
            # Handle both string and enum inputs for importance
            importance_value = importance.value if hasattr(importance, 'value') else importance
            
            new_reminder = DBReminder(
                reminder_text=reminder_text,
                importance=importance_value,
                reminder_date=reminder_date,
                )
            session.add(new_reminder)
            session.commit()
            session.refresh(new_reminder)
            # print(...) # Removed to avoid MCP protocol issues
            
            # Create corresponding Google Calendar event
            google_event_id = None
            if get_calendar_service:
                try:
                    pass
                    # print(...) # Removed to avoid MCP protocol issues
                    calendar_service = get_calendar_service()
                    
                    # Use reminder_date as the event start time, with 30 minutes duration
                    event_start = reminder_date or datetime.now(timezone.utc)
                    event_end = event_start + timedelta(minutes=30)
                    
                    # Create Google Calendar event using the proper API format
                    event_body = {
                        'summary': f"Reminder: {reminder_text}",
                        'description': f"Reminder - Importance: {importance_value}",
                        'start': {
                            'dateTime': event_start.isoformat(),
                            'timeZone': 'UTC',
                        },
                        'end': {
                            'dateTime': event_end.isoformat(),
                            'timeZone': 'UTC',
                        },
                    }
                    
                    created_event = calendar_service.events().insert(
                        calendarId='primary', 
                        body=event_body
                    ).execute()
                    
                    google_event_id = created_event.get('id')
                    
                    if google_event_id:
                    
                        pass  #                         
                        # Update the reminder with the Google Calendar event ID
                        new_reminder.google_calendar_event_id = google_event_id
                        session.commit()
                        session.refresh(new_reminder)
                    else:
                        pass
                        # print(...) # Removed to avoid MCP protocol issues
                except Exception as calendar_error:
                    pass  #                     
            else:
                pass
                # print(...) # Removed to avoid MCP protocol issues
    
        # Convert SQLAlchemy object to dict properly
        reminder_dict = {
            "id": str(new_reminder.id),
            "created_at": new_reminder.created_at.isoformat(),
            "updated_at": new_reminder.updated_at.isoformat(),
            "reminder_text": new_reminder.reminder_text,
            "importance": new_reminder.importance,
            "reminder_date": new_reminder.reminder_date.isoformat() if new_reminder.reminder_date else None,
            "google_calendar_event_id": new_reminder.google_calendar_event_id
        }
        result = Reminder.model_validate(reminder_dict).model_dump_json(indent=2)
        # print(...) # Removed to avoid MCP protocol issues
        return result
        
    except Exception as e:
        error_msg = f"Error executing tool create_reminder: {str(e)}"
        # print(...) # Removed to avoid MCP protocol issues
        return error_msg

@mcp.tool()
async def get_reminders() -> str:
    """Get all reminders.
    
    Returns:
        A list of all reminders.
    """
    with SessionLocal() as session:
        reminders = session.query(DBReminder).all()
        reminders_list = [Reminder.model_validate(reminder.__dict__).model_dump_json(indent=2) for reminder in reminders]
    sep = ",\n"
    return f"[{sep.join(reminders_list)}]"

@mcp.tool()
async def update_reminder(
    id: UUID,
    reminder_text: Optional[str] = None,
    importance: Optional[ReminderImportance] = None,
    reminder_date: Optional[datetime] = None,
    ) -> str:
    """Update a reminder by id.
    
    Args:
        id: The id of the reminder to update.
        reminder_text: The new text content of the reminder.
        importance: The new importance level of the reminder. Options are: low, medium, high, urgent
        reminder_date: The new date/time for the reminder.

    Returns:
        The updated reminder.
    """
    with SessionLocal() as session:
        reminder = session.query(DBReminder).filter(DBReminder.id == id).first()
        if not reminder:
            return "Reminder not found"
        
        if reminder_text:
            reminder.reminder_text = reminder_text
        if importance:
            reminder.importance = importance.value
        if reminder_date is not None:
            reminder.reminder_date = reminder_date

        session.commit()
        session.refresh(reminder)
        
        # Update Google Calendar event if it exists
        if reminder.google_calendar_event_id and get_calendar_service:
            try:
                pass
                # print(...) # Removed to avoid MCP protocol issues
                calendar_service = get_calendar_service()
                
                # Prepare update data
                update_data = {}
                if reminder_text:
                    update_data['title'] = f"Reminder: {reminder_text}"
                if importance:
                    update_data['description'] = f"Reminder - Importance: {importance.value}"
                if reminder_date is not None:
                    update_data['start_time'] = reminder_date
                    update_data['end_time'] = reminder_date + timedelta(minutes=30)
                
                if update_data:
                    success = calendar_service.update_event(
                        event_id=reminder.google_calendar_event_id,
                        **update_data
                    )
                    if success:
                        pass  #                         
                    else:
                        pass
                        # print(...) # Removed to avoid MCP protocol issues
                else:
                    pass
                    # print(...) # Removed to avoid MCP protocol issues
            except Exception as calendar_error:
                pass  #                 
    
    return Reminder.model_validate(reminder.__dict__).model_dump_json(indent=2)

@mcp.tool()
async def delete_reminder(id: UUID) -> str:
    """Delete a reminder by id.
    
    Args:
        id: The id of the reminder to delete.

    Returns:
        The deleted reminder.
    """
    with SessionLocal() as session:
        reminder = session.query(DBReminder).filter(DBReminder.id == id).first()
        if not reminder:
            return "Reminder not found"
        
        # print(...) # Removed to avoid MCP protocol issues
        
        # Delete from Google Calendar if event exists
        if reminder.google_calendar_event_id and get_calendar_service:
            try:
                pass
                # print(...) # Removed to avoid MCP protocol issues
                calendar_service = get_calendar_service()
                calendar_service.events().delete(
                    calendarId='primary', 
                    eventId=reminder.google_calendar_event_id
                ).execute()
                success = True
                if success:
                    pass  #                     
                else:
                    pass
                    # print(...) # Removed to avoid MCP protocol issues
            except Exception as calendar_error:
                pass  #                 
        
        session.delete(reminder)
        session.commit()
    
    return Reminder.model_validate(reminder.__dict__).model_dump_json(indent=2)

@mcp.tool()
async def create_calendar_event(
    title: str,
    event_from: datetime,
    event_to: datetime,
    description: Optional[str] = None,
    ) -> str:
    """Create a new calendar event.
    
    Args:
        title: The title of the calendar event.
        event_from: The start date and time of the event.
        event_to: The end date and time of the event.
        description: An optional description of the event.

    Returns:
        The created calendar event.
    """
    try:
        pass
        # print(...) # Removed to avoid MCP protocol issues
        check_database_available()
        
        with SessionLocal() as session:
            new_event = DBCalendarEvent(
                title=title,
                description=description,
                event_from=event_from,
                event_to=event_to,
                )
            session.add(new_event)
            session.commit()
            session.refresh(new_event)
            # print(...) # Removed to avoid MCP protocol issues
            
            # Skip Google Calendar sync for voice calls to avoid timeout
            # Database event is created successfully - return immediately
            
            # Return simple success message - don't wait for slow Google Calendar API
            # Format dates for natural speech response
            from_str = new_event.event_from.strftime('%b %d at %I:%M %p') if new_event.event_from else "unknown time"
            to_str = new_event.event_to.strftime('%I:%M %p') if new_event.event_to else "unknown time"
            
            return f"Calendar event '{title}' created successfully from {from_str} to {to_str}."
        
    except Exception as e:
        error_msg = f"Error executing tool create_calendar_event: {str(e)}"
        logging.error(error_msg)
        return json.dumps({"error": error_msg, "status": "failed"})

@mcp.tool()
async def get_calendar_events() -> str:
    """Get all calendar events.
    
    Returns:
        A list of all calendar events.
    """
    check_database_available()
    with SessionLocal() as session:
        events = session.query(DBCalendarEvent).all()
        events_list = [CalendarEvent.model_validate(event.__dict__).model_dump_json(indent=2) for event in events]
    sep = ",\n"
    return f"[{sep.join(events_list)}]"

@mcp.tool()
async def update_calendar_event(
    id: UUID,
    title: Optional[str] = None,
    event_from: Optional[datetime] = None,
    event_to: Optional[datetime] = None,
    description: Optional[str] = None,
    ) -> str:
    """Update a calendar event by id.
    
    Args:
        id: The id of the calendar event to update.
        title: The new title of the calendar event.
        event_from: The new start date and time of the event.
        event_to: The new end date and time of the event.
        description: The new description of the event.

    Returns:
        The updated calendar event.
    """
    with SessionLocal() as session:
        event = session.query(DBCalendarEvent).filter(DBCalendarEvent.id == id).first()
        if not event:
            return "Calendar event not found"
        
        if title:
            event.title = title
        if event_from is not None:
            event.event_from = event_from
        if event_to is not None:
            event.event_to = event_to
        if description is not None:
            event.description = description

        session.commit()
        session.refresh(event)
        
        # Update Google Calendar event if it exists
        if event.google_calendar_event_id and get_calendar_service:
            try:
                pass
                # print(...) # Removed to avoid MCP protocol issues
                calendar_service = get_calendar_service()
                
                # Prepare update data
                update_data = {}
                if title:
                    update_data['title'] = title
                if description is not None:
                    update_data['description'] = description or ""
                if event_from is not None:
                    update_data['start_time'] = event_from
                if event_to is not None:
                    update_data['end_time'] = event_to
                
                if update_data:
                    success = calendar_service.update_event(
                        event_id=event.google_calendar_event_id,
                        **update_data
                    )
                    if success:
                        pass  #                         
                    else:
                        pass
                        # print(...) # Removed to avoid MCP protocol issues
                else:
                    pass
                    # print(...) # Removed to avoid MCP protocol issues
            except Exception as calendar_error:
                pass  #                 
    
    return CalendarEvent.model_validate(event.__dict__).model_dump_json(indent=2)

@mcp.tool()
async def delete_calendar_event(id: UUID) -> str:
    """Delete a calendar event by id.
    
    Args:
        id: The id of the calendar event to delete.

    Returns:
        The deleted calendar event.
    """
    with SessionLocal() as session:
        event = session.query(DBCalendarEvent).filter(DBCalendarEvent.id == id).first()
        if not event:
            return "Calendar event not found"
        
        # print(...) # Removed to avoid MCP protocol issues
        
        # Delete from Google Calendar if event exists
        if event.google_calendar_event_id and get_calendar_service:
            try:
                pass
                # print(...) # Removed to avoid MCP protocol issues
                calendar_service = get_calendar_service()
                calendar_service.events().delete(
                    calendarId='primary', 
                    eventId=event.google_calendar_event_id
                ).execute()
                success = True
                if success:
                    pass  #                     
                else:
                    pass
                    # print(...) # Removed to avoid MCP protocol issues
            except Exception as calendar_error:
                pass  #                 
        
        session.delete(event)
        session.commit()
    
    return CalendarEvent.model_validate(event.__dict__).model_dump_json(indent=2)

@mcp.tool()
async def create_call_recording(
    call_sid: str,
    recording_path: str,
    from_number: Optional[str] = None,
    to_number: Optional[str] = None,
    duration_seconds: Optional[int] = None,
    file_size_bytes: Optional[int] = None,
    transcription: Optional[str] = None,
    status: str = "completed"
) -> str:
    """Create a new call recording record.
    
    Args:
        call_sid: Twilio Call SID (unique identifier)
        recording_path: Path to the audio recording file
        from_number: Caller's phone number
        to_number: Called phone number
        duration_seconds: Duration of the recording in seconds
        file_size_bytes: Size of the recording file in bytes
        transcription: Text transcription of the call
        status: Recording status (completed, failed, processing)

    Returns:
        The created call recording record
    """
    with SessionLocal() as session:
        recording = DBCallRecording(
            call_sid=call_sid,
            recording_path=recording_path,
            from_number=from_number,
            to_number=to_number,
            duration_seconds=duration_seconds,
            file_size_bytes=file_size_bytes,
            transcription=transcription,
            status=status
        )
        session.add(recording)
        session.commit()
        session.refresh(recording)
    
    return CallRecording.model_validate(recording.__dict__).model_dump_json(indent=2)

@mcp.tool()
async def get_call_recordings() -> str:
    """Get all call recordings.
    
    Returns:
        A list of all call recordings
    """
    with SessionLocal() as session:
        recordings = session.query(DBCallRecording).order_by(DBCallRecording.created_at.desc()).all()
    
    return [CallRecording.model_validate(recording.__dict__).model_dump() for recording in recordings]

@mcp.tool()
async def get_call_recording_by_sid(call_sid: str) -> str:
    """Get a call recording by Call SID.
    
    Args:
        call_sid: Twilio Call SID to search for

    Returns:
        The call recording record or error message
    """
    with SessionLocal() as session:
        recording = session.query(DBCallRecording).filter(DBCallRecording.call_sid == call_sid).first()
        
        if not recording:
            return f"Call recording with SID {call_sid} not found"
    
    return CallRecording.model_validate(recording.__dict__).model_dump_json(indent=2)

@mcp.tool()
async def update_call_recording(
    call_sid: str,
    transcription: Optional[str] = None,
    status: Optional[str] = None,
    duration_seconds: Optional[int] = None,
    file_size_bytes: Optional[int] = None
) -> str:
    """Update a call recording record.
    
    Args:
        call_sid: Twilio Call SID to update
        transcription: Updated transcription text
        status: Updated recording status
        duration_seconds: Updated duration in seconds
        file_size_bytes: Updated file size in bytes

    Returns:
        The updated call recording record
    """
    with SessionLocal() as session:
        recording = session.query(DBCallRecording).filter(DBCallRecording.call_sid == call_sid).first()
        
        if not recording:
            return f"Call recording with SID {call_sid} not found"
        
        if transcription is not None:
            recording.transcription = transcription
        if status is not None:
            recording.status = status
        if duration_seconds is not None:
            recording.duration_seconds = duration_seconds
        if file_size_bytes is not None:
            recording.file_size_bytes = file_size_bytes
            
        session.commit()
        session.refresh(recording)
    
    return CallRecording.model_validate(recording.__dict__).model_dump_json(indent=2)

@mcp.tool()
async def delete_call_recording(call_sid: str) -> str:
    """Delete a call recording by Call SID.
    
    Args:
        call_sid: Twilio Call SID to delete

    Returns:
        Success message or error
    """
    with SessionLocal() as session:
        recording = session.query(DBCallRecording).filter(DBCallRecording.call_sid == call_sid).first()
        
        if not recording:
            return f"Call recording with SID {call_sid} not found"
        
        # Delete the actual file if it exists
        try:
            if os.path.exists(recording.recording_path):
                os.remove(recording.recording_path)
        except Exception as e:
            return f"Error deleting file: {str(e)}"
        
        session.delete(recording)
        session.commit()
    
    return f"Call recording {call_sid} deleted successfully"

@mcp.tool()
async def query_db(query: str) -> str:
    """Query the database using SQL.
    
    Args:
        query: A valid PostgreSQL query to run.

    Returns:
        The query results
    """
    with SessionLocal() as session:
        result = session.execute(text(query))
        
    return pd.DataFrame(result.all(), columns=result.keys()).to_json(orient="records", indent=2)

@mcp.tool()
async def test_authentication() -> str:
    """Test Google Calendar authentication status and show what's available."""
    try:
        result = "🔧 Authentication Status Check:\n\n"
        
        # Check environment variables
        result += "📋 Environment Variables:\n"
        result += f"• GOOGLE_CREDENTIALS_B64: {'✅ SET' if os.getenv('GOOGLE_CREDENTIALS_B64') else '❌ NOT SET'}\n"
        result += f"• GOOGLE_TOKEN_B64: {'✅ SET' if os.getenv('GOOGLE_TOKEN_B64') else '❌ NOT SET'}\n"
        result += f"• GOOGLE_OAUTH2_TOKEN_B64: {'✅ SET' if os.getenv('GOOGLE_OAUTH2_TOKEN_B64') else '❌ NOT SET'}\n"
        result += f"• GOOGLE_CLIENT_ID: {'✅ SET' if os.getenv('GOOGLE_CLIENT_ID') else '❌ NOT SET'}\n"
        result += f"• GOOGLE_CLIENT_SECRET: {'✅ SET' if os.getenv('GOOGLE_CLIENT_SECRET') else '❌ NOT SET'}\n\n"
        
        # Check function availability
        result += "🔧 Function Availability:\n"
        result += f"• get_calendar_service: {'✅ Available' if get_calendar_service else '❌ None'}\n"
        result += f"• get_service_account_calendar_service: {'✅ Available' if 'get_service_account_calendar_service' in globals() else '❌ Not Available'}\n"
        result += f"• get_oauth2_calendar_service: {'✅ Available' if 'get_oauth2_calendar_service' in globals() else '❌ Not Available'}\n\n"
        
        # Test service account authentication
        if os.getenv('GOOGLE_CREDENTIALS_B64'):
            try:
                result += "🧪 Testing Service Account Authentication:\n"
                creds_data = base64.b64decode(os.getenv('GOOGLE_CREDENTIALS_B64'))
                creds_info = json.loads(creds_data.decode('utf-8'))
                result += f"• Service Account Email: {creds_info.get('client_email', 'Unknown')}\n"
                result += f"• Project ID: {creds_info.get('project_id', 'Unknown')}\n"
                result += "✅ Service Account credentials are valid\n\n"
            except Exception as e:
                result += f"❌ Service Account credentials error: {e}\n\n"
        
        # Test OAuth2 authentication
        if os.getenv('GOOGLE_OAUTH2_TOKEN_B64'):
            try:
                result += "🧪 Testing OAuth2 Authentication:\n"
                token_data = base64.b64decode(os.getenv('GOOGLE_OAUTH2_TOKEN_B64'))
                token_info = pickle.loads(token_data)
                result += f"• Token Valid: {token_info.valid if hasattr(token_info, 'valid') else 'Unknown'}\n"
                result += f"• Token Expired: {token_info.expired if hasattr(token_info, 'expired') else 'Unknown'}\n"
                result += f"• Has Refresh Token: {bool(token_info.refresh_token) if hasattr(token_info, 'refresh_token') else 'Unknown'}\n"
                result += "✅ OAuth2 token is available\n\n"
            except Exception as e:
                result += f"❌ OAuth2 token error: {e}\n\n"
        
        # Test get_calendar_service function
        if get_calendar_service:
            try:
                result += "🧪 Testing get_calendar_service function:\n"
                calendar_service = get_calendar_service()
                if calendar_service:
                    result += "✅ Calendar service created successfully\n"
                else:
                    result += "❌ Calendar service returned None\n"
            except Exception as e:
                result += f"❌ Calendar service error: {e}\n"
        else:
            result += "❌ get_calendar_service function is None\n"
        
        return result
        
    except Exception as e:
        return f"❌ Authentication test error: {str(e)}"

@mcp.tool()
async def check_calendar_visibility() -> str:
    """Check which calendar events are being created in and provide visibility instructions.
    
    This function will show you exactly which calendar your events are being created in
    and provide instructions on how to make them visible in your personal calendar.
    
    Returns:
        Detailed information about calendar visibility and sharing instructions.
    """
    try:
        pass
        # print(...) # Removed to avoid MCP protocol issues
        check_database_available()
        
        if not get_calendar_service:
            return "❌ Google Calendar service not available. Please check your Google Calendar configuration."
        
        calendar_service = get_calendar_service()
        
        # Get list of available calendars
        try:
            calendar_list = calendar_service.calendarList().list().execute()
            calendars = calendar_list.get('items', [])
            
            result = "📅 Available Calendars:\n\n"
            primary_calendar = None
            
            for cal in calendars:
                cal_id = cal.get('id', 'Unknown')
                cal_name = cal.get('summary', 'Unknown')
                is_primary = cal.get('primary', False)
                access_role = cal.get('accessRole', 'Unknown')
                
                result += f"• {cal_name}\n"
                result += f"  ID: {cal_id}\n"
                result += f"  Primary: {is_primary}\n"
                result += f"  Access Role: {access_role}\n\n"
                
                if is_primary:
                    primary_calendar = cal
            
            # Create a test event to see where it goes
            test_event_body = {
                'summary': "Calendar Visibility Test",
                'description': "This event is created to test which calendar it appears in",
                'start': {
                    'dateTime': (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
                    'timeZone': 'UTC',
                },
                'end': {
                    'dateTime': (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
                    'timeZone': 'UTC',
                },
            }
            
            created_event = calendar_service.events().insert(
                calendarId='primary', 
                body=test_event_body
            ).execute()
            
            test_event_id = created_event.get('id')
            organizer_email = created_event.get('organizer', {}).get('email', 'Unknown')
            calendar_id = created_event.get('organizer', {}).get('email', 'Unknown')
            
            result += f"🧪 Test Event Created:\n"
            result += f"• Event ID: {test_event_id}\n"
            result += f"• Organizer: {organizer_email}\n"
            result += f"• Calendar ID: {calendar_id}\n\n"
            
            # Try to delete the test event
            try:
                calendar_service.events().delete(
                    calendarId='primary', 
                    eventId=test_event_id
                ).execute()
                result += "✅ Test event deleted successfully\n\n"
            except Exception as delete_error:
                result += f"⚠️  Could not delete test event: {delete_error}\n\n"
            
            # Provide instructions
            result += "📋 To see events in your personal calendar:\n\n"
            result += "1. Go to Google Calendar (calendar.google.com)\n"
            result += "2. Find your main calendar in the left sidebar\n"
            result += "3. Click the 3 dots (⋮) next to your calendar name\n"
            result += "4. Select 'Settings and sharing'\n"
            result += "5. Scroll to 'Share with specific people'\n"
            result += "6. Click 'Add people'\n"
            result += f"7. Add this email: google-calendar-api2@dark-window-206618.iam.gserviceaccount.com\n"
            result += "8. Set permission to 'Make changes to events'\n"
            result += "9. Click 'Send'\n\n"
            
            if organizer_email and 'gserviceaccount.com' in organizer_email:
                result += "⚠️  Events are currently being created in the service account's calendar.\n"
                result += "You need to share your personal calendar with the service account to see the events.\n"
            else:
                result += "✅ Events appear to be created in your personal calendar.\n"
            
            return result
            
        except Exception as list_error:
            return f"❌ Error checking calendars: {list_error}"
            
    except Exception as e:
        error_msg = f"Error checking calendar visibility: {str(e)}"
        # print(...) # Removed to avoid MCP protocol issues
        return error_msg

@mcp.tool()
async def sync_google_calendar_events() -> str:
    """Sync all existing todos, reminders, and calendar events with Google Calendar.
    
    This function will create Google Calendar events for any items that don't already have
    a google_calendar_event_id. Useful for syncing existing data.
    
    Returns:
        Summary of sync operations performed.
    """
    try:
        pass
        # print(...) # Removed to avoid MCP protocol issues
        check_database_available()
        
        if not get_calendar_service:
            return "Google Calendar service not available. Please check your Google Calendar configuration."
        
        calendar_service = get_calendar_service()
        sync_summary = {
            "todos_processed": 0,
            "todos_created": 0,
            "reminders_processed": 0,
            "reminders_created": 0,
            "events_processed": 0,
            "events_created": 0,
            "errors": []
        }
        
        with SessionLocal() as session:
            # Sync todos
            # print(...) # Removed to avoid MCP protocol issues
            todos = session.query(DBTodo).filter(DBTodo.google_calendar_event_id.is_(None)).all()
            for todo in todos:
                sync_summary["todos_processed"] += 1
                try:
                    pass
                    # Use due_date as the event start time, with 1 hour duration
                    event_start = todo.due_date or datetime.now(timezone.utc)
                    event_end = event_start + timedelta(hours=1)
                    
                    # Create Google Calendar event using the proper API format
                    event_body = {
                        'summary': f"Todo: {todo.title}",
                        'description': todo.description or "",
                        'start': {
                            'dateTime': event_start.isoformat(),
                            'timeZone': 'UTC',
                        },
                        'end': {
                            'dateTime': event_end.isoformat(),
                            'timeZone': 'UTC',
                        },
                    }
                    
                    created_event = calendar_service.events().insert(
                        calendarId='primary', 
                        body=event_body
                    ).execute()
                    
                    google_event_id = created_event.get('id')
                    
                    if google_event_id:
                        todo.google_calendar_event_id = google_event_id
                        sync_summary["todos_created"] += 1
                        # print(...) # Removed to avoid MCP protocol issues
                    else:
                        sync_summary["errors"].append(f"Failed to create calendar event for todo: {todo.title}")
                        # print(...) # Removed to avoid MCP protocol issues
                except Exception as e:
                    error_msg = f"Error syncing todo '{todo.title}': {str(e)}"
                    sync_summary["errors"].append(error_msg)
                    # print(...) # Removed to avoid MCP protocol issues
            
            # Sync reminders
            # print(...) # Removed to avoid MCP protocol issues
            reminders = session.query(DBReminder).filter(DBReminder.google_calendar_event_id.is_(None)).all()
            for reminder in reminders:
                sync_summary["reminders_processed"] += 1
                try:
                    pass
                    # Use reminder_date as the event start time, with 30 minutes duration
                    event_start = reminder.reminder_date or datetime.now(timezone.utc)
                    event_end = event_start + timedelta(minutes=30)
                    
                    # Create Google Calendar event using the proper API format
                    event_body = {
                        'summary': f"Reminder: {reminder.reminder_text}",
                        'description': f"Reminder - Importance: {reminder.importance}",
                        'start': {
                            'dateTime': event_start.isoformat(),
                            'timeZone': 'UTC',
                        },
                        'end': {
                            'dateTime': event_end.isoformat(),
                            'timeZone': 'UTC',
                        },
                    }
                    
                    created_event = calendar_service.events().insert(
                        calendarId='primary', 
                        body=event_body
                    ).execute()
                    
                    google_event_id = created_event.get('id')
                    
                    if google_event_id:
                        reminder.google_calendar_event_id = google_event_id
                        sync_summary["reminders_created"] += 1
                        # print(...) # Removed to avoid MCP protocol issues
                    else:
                        sync_summary["errors"].append(f"Failed to create calendar event for reminder: {reminder.reminder_text}")
                        # print(...) # Removed to avoid MCP protocol issues
                except Exception as e:
                    error_msg = f"Error syncing reminder '{reminder.reminder_text}': {str(e)}"
                    sync_summary["errors"].append(error_msg)
                    # print(...) # Removed to avoid MCP protocol issues
            
            # Sync calendar events
            # print(...) # Removed to avoid MCP protocol issues
            events = session.query(DBCalendarEvent).filter(DBCalendarEvent.google_calendar_event_id.is_(None)).all()
            for event in events:
                sync_summary["events_processed"] += 1
                try:
                    pass
                    # Create Google Calendar event using the proper API format
                    event_body = {
                        'summary': event.title,
                        'description': event.description or "",
                        'start': {
                            'dateTime': event.event_from.isoformat(),
                            'timeZone': 'UTC',
                        },
                        'end': {
                            'dateTime': event.event_to.isoformat(),
                            'timeZone': 'UTC',
                        },
                    }
                    
                    created_event = calendar_service.events().insert(
                        calendarId='primary', 
                        body=event_body
                    ).execute()
                    
                    google_event_id = created_event.get('id')
                    
                    if google_event_id:
                        event.google_calendar_event_id = google_event_id
                        sync_summary["events_created"] += 1
                        # print(...) # Removed to avoid MCP protocol issues
                    else:
                        sync_summary["errors"].append(f"Failed to create calendar event for: {event.title}")
                        # print(...) # Removed to avoid MCP protocol issues
                except Exception as e:
                    error_msg = f"Error syncing calendar event '{event.title}': {str(e)}"
                    sync_summary["errors"].append(error_msg)
                    # print(...) # Removed to avoid MCP protocol issues
            
            # Commit all changes
            session.commit()
            # print(...) # Removed to avoid MCP protocol issues
        
        # Generate summary
        summary = f"""Google Calendar Sync Complete!

📊 Summary:
- Todos processed: {sync_summary['todos_processed']}, created: {sync_summary['todos_created']}
- Reminders processed: {sync_summary['reminders_processed']}, created: {sync_summary['reminders_created']}
- Calendar events processed: {sync_summary['events_processed']}, created: {sync_summary['events_created']}
- Errors: {len(sync_summary['errors'])}

✅ Total Google Calendar events created: {sync_summary['todos_created'] + sync_summary['reminders_created'] + sync_summary['events_created']}"""

        if sync_summary['errors']:
            summary += f"\n\n❌ Errors encountered:\n" + "\n".join(sync_summary['errors'])
        
        # print(...) # Removed to avoid MCP protocol issues
        return summary
        
    except Exception as e:
        error_msg = f"Error during Google Calendar sync: {str(e)}"
        # print(...) # Removed to avoid MCP protocol issues
        return error_msg


@mcp.tool()
async def get_teams() -> str:
    """Get all available teams for the current user.
    
    Returns:
        List of teams with their details.
    """
    try:
        _lazy_import_team_models()
        check_database_available()
        
        with SessionLocal() as session:
            # Get all teams
            teams = session.query(Team).filter(Team.is_active == True).all()
            
            if not teams:
                return "No teams found. Create a team first using the team dashboard."
            
            result = "Available teams:\n"
            for team in teams:
                result += f"• {team.name} (ID: {team.id})\n"
                result += f"  Description: {team.description or 'No description'}\n"
                result += f"  Created: {team.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
            
            return result
            
    except Exception as e:
        return f"Error getting teams: {str(e)}"

@mcp.tool()
async def get_team_members(team_id: str) -> str:
    """Get all members of a specific team.
    
    Args:
        team_id: The ID of the team to get members for.
        
    Returns:
        List of team members with their roles.
    """
    try:
        _lazy_import_team_models()
        check_database_available()
        
        with SessionLocal() as session:
            # Get team
            team = session.query(Team).filter(Team.id == team_id).first()
            if not team:
                return f"Team with ID {team_id} not found."
            
            # Get team members using join
            member_results = session.query(TeamMembership, User).join(
                User, TeamMembership.user_id == User.id
            ).filter(TeamMembership.team_id == team_id).all()
            
            if not member_results:
                return f"No members found for team '{team.name}'."
            
            result = f"Members of '{team.name}':\n"
            for membership, user in member_results:
                result += f"• {user.full_name} ({user.email})\n"
                result += f"  Role: {membership.role.value}\n"
                result += f"  Joined: {membership.joined_at.strftime('%Y-%m-%d')}\n\n"
            
            return result
            
    except Exception as e:
        return f"Error getting team members: {str(e)}"

@mcp.tool()
async def create_team_todo(
    title: str,
    team_id: str,
    description: Optional[str] = None,
    priority: TodoPriority = TodoPriority.MEDIUM,
    assignee_id: Optional[str] = None,
    due_date: Optional[datetime] = None,
    ) -> str:
    """Create a todo item for a specific team.
    
    Args:
        title: The title of the todo item.
        team_id: The ID of the team to assign the todo to.
        description: An optional description of the todo item.
        priority: The priority level of the todo. Options are: low, medium, high, urgent
        assignee_id: Optional user ID to assign the todo to a specific team member.
        due_date: The due date for the todo item.

    Returns:
        The created todo item details.
    """
    try:
        _lazy_import_team_models()
        pass
        # print(...) # Removed to avoid MCP protocol issues
        check_database_available()
        
        with SessionLocal() as session:
            # Verify team exists
            team = session.query(Team).filter(Team.id == team_id).first()
            if not team:
                return f"Team with ID {team_id} not found."
            
            # Set default due date if not provided
            if due_date is None:
                due_date = datetime.now(timezone.utc)
            
            # Verify assignee is a team member if specified
            if assignee_id:
                membership = session.query(TeamMembership).filter(
                    TeamMembership.team_id == team_id,
                    TeamMembership.user_id == assignee_id
                ).first()
                if not membership:
                    return f"User {assignee_id} is not a member of team '{team.name}'."
            
            # print(...) # Removed to avoid MCP protocol issues
            new_todo = DBTodo(
                title=title,
                description=description,
                priority=priority.value,
                due_date=due_date,
                team_id=team_id,
                assignee_id=assignee_id,
            )
            
            session.add(new_todo)
            session.commit()
            session.refresh(new_todo)
            
            # Create Google Calendar event
            google_event_id = None
            if get_calendar_service:
                try:
                    calendar_service = get_calendar_service()
                    if calendar_service:
                        pass  #                         
                        event = {
                            'summary': f"[Team] {title}",
                            'description': f"Team: {team.name}\n{description or ''}",
                            'start': {
                                'dateTime': due_date.isoformat(),
                                'timeZone': 'UTC',
                            },
                            'end': {
                                'dateTime': (due_date + timedelta(hours=1)).isoformat(),
                                'timeZone': 'UTC',
                            },
                        }
                        
                        created_event = calendar_service.events().insert(
                            calendarId='primary',
                            body=event
                        ).execute()
                        
                        google_event_id = created_event.get('id')
                        
                        if google_event_id:
                            new_todo.google_calendar_event_id = google_event_id
                            session.commit()
                            # print(...) # Removed to avoid MCP protocol issues
                except Exception as e:
                    pass  #                     
            
            # Send Slack notification for team todo
            try:
                import sys
                # Add the convonet directory to the path
                convonet_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
                if convonet_path not in sys.path:
                    sys.path.append(convonet_path)
                
                from convonet.composio_tools import composio_manager
                assignee_info = ""
                if assignee_id:
                    assignee = session.query(User).filter(User.id == assignee_id).first()
                    assignee_info = f" (assigned to {assignee.full_name})"
                slack_message = f"📝 New team todo: '{title}' for {team.name}{assignee_info} with {priority.value} priority"
                composio_manager.send_slack_message("#productivity", slack_message)
            except Exception as e:
                # Don't fail todo creation if Slack notification fails
                logging.warning(f"⚠️ Failed to send Slack notification: {e}")
            
            # Build response
            result = f"✅ Team todo created successfully!\n\n"
            result += f"📋 **{title}**\n"
            result += f"🏢 Team: {team.name}\n"
            if assignee_id:
                assignee = session.query(User).filter(User.id == assignee_id).first()
                result += f"👤 Assigned to: {assignee.full_name} ({assignee.email})\n"
            result += f"⚡ Priority: {priority.value}\n"
            result += f"📅 Due: {due_date.strftime('%Y-%m-%d %H:%M UTC')}\n"
            result += f"🆔 Todo ID: {new_todo.id}\n"
            if google_event_id:
                result += f"📅 Google Calendar Event ID: {google_event_id}\n"
            
            return result
            
    except Exception as e:
        return f"Error creating team todo: {str(e)}"

@mcp.tool()
async def create_team(name: str, description: str = "") -> str:
    """Create a new team via voice command.
    
    Args:
        name: The name of the team to create.
        description: Optional description of the team.
        
    Returns:
        Confirmation message with team details.
    """
    try:
        _lazy_import_team_models()
        check_database_available()
        
        with SessionLocal() as session:
            # Create new team
            team = Team(
                name=name,
                description=description,
                is_active=True
            )
            session.add(team)
            session.commit()
            session.refresh(team)
            
            result = f"✅ Team created successfully!\n\n"
            result += f"🏢 **{team.name}**\n"
            result += f"📝 Description: {team.description or 'No description'}\n"
            result += f"🆔 Team ID: {team.id}\n"
            result += f"📅 Created: {team.created_at.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            result += f"💡 Note: You can now add members to this team using the team dashboard or by saying 'Add [email] to {name} team as [role]'"
            
            return result
            
    except Exception as e:
        return f"Error creating team: {str(e)}"

@mcp.tool()
async def add_team_member(team_name: str, email: str, role: str = "member") -> str:
    """Add a member to a team by email address.
    
    Args:
        team_name: The name of the team to add the member to.
        email: The email address of the user to add.
        role: The role to assign (owner, admin, member, viewer). Defaults to 'member'.
        
    Returns:
        Confirmation message with member details.
    """
    try:
        _lazy_import_team_models()
        check_database_available()
        
        with SessionLocal() as session:
            # Find team by name (case-insensitive)
            team = session.query(Team).filter(
                Team.name.ilike(f"%{team_name}%"),
                Team.is_active == True
            ).first()
            
            if not team:
                return f"❌ Team '{team_name}' not found. Available teams: " + ", ".join([
                    t.name for t in session.query(Team).filter(Team.is_active == True).all()
                ])
            
            # Find user by email
            user = session.query(User).filter(User.email == email).first()
            
            if not user:
                return f"❌ User with email '{email}' not found. The user needs to register first at /register"
            
            # Check if user is already a member
            existing_membership = session.query(TeamMembership).filter(
                TeamMembership.team_id == team.id,
                TeamMembership.user_id == user.id
            ).first()
            
            if existing_membership:
                return f"⚠️  {user.full_name} is already a member of '{team.name}' with role: {existing_membership.role.value}"
            
            # Validate role
            valid_roles = ["owner", "admin", "member", "viewer"]
            role_lower = role.lower()
            if role_lower not in valid_roles:
                return f"❌ Invalid role '{role}'. Valid roles are: {', '.join(valid_roles)}"
            
            # Map string to TeamRole enum
            role_enum = TeamRole[role_lower.upper()]
            
            # Create membership
            membership = TeamMembership(
                team_id=team.id,
                user_id=user.id,
                role=role_enum
            )
            session.add(membership)
            session.commit()
            
            result = f"✅ Team member added successfully!\n\n"
            result += f"👤 **{user.full_name}** ({user.email})\n"
            result += f"🏢 Team: {team.name}\n"
            result += f"🎭 Role: {role_enum.value}\n"
            result += f"📅 Joined: {membership.joined_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
            
            return result
            
    except Exception as e:
        return f"Error adding team member: {str(e)}"

@mcp.tool()
async def verify_user_pin(pin: str) -> str:
    """Verify user identity by PIN for voice authentication.
    
    Args:
        pin: 4-6 digit PIN code.
        
    Returns:
        User information if PIN is valid, error message otherwise.
    """
    try:
        _lazy_import_team_models()
        check_database_available()
        
        with SessionLocal() as session:
            # Find user by PIN
            user = session.query(User).filter(
                User.voice_pin == pin,
                User.is_active == True
            ).first()
            
            if not user:
                return "AUTHENTICATION_FAILED: Invalid PIN. Please try again."
            
            # Get user's teams
            team_memberships = session.query(TeamMembership, Team).join(
                Team, TeamMembership.team_id == Team.id
            ).filter(TeamMembership.user_id == user.id).all()
            
            result = f"AUTHENTICATED:{user.id}|{user.full_name}|{user.email}\n\n"
            result += f"Welcome back, {user.first_name}! 👋\n\n"
            
            if team_memberships:
                result += f"Your teams:\n"
                for membership, team in team_memberships:
                    result += f"• {team.name} ({membership.role.value})\n"
            else:
                result += "You're not currently a member of any teams.\n"
            
            return result
            
    except Exception as e:
        return f"AUTHENTICATION_ERROR: {str(e)}"

@mcp.tool()
async def search_users(search_term: str) -> str:
    """Search for users by name or email.
    
    Args:
        search_term: Name or email to search for.
        
    Returns:
        List of matching users.
    """
    try:
        _lazy_import_team_models()
        check_database_available()
        
        with SessionLocal() as session:
            # Search by email, username, first_name, or last_name
            users = session.query(User).filter(
                (User.email.ilike(f"%{search_term}%")) |
                (User.username.ilike(f"%{search_term}%")) |
                (User.first_name.ilike(f"%{search_term}%")) |
                (User.last_name.ilike(f"%{search_term}%"))
            ).filter(User.is_active == True).limit(10).all()
            
            if not users:
                return f"No users found matching '{search_term}'"
            
            result = f"Found {len(users)} user(s) matching '{search_term}':\n\n"
            for user in users:
                result += f"• {user.full_name} ({user.email})\n"
                result += f"  Username: {user.username}\n"
                result += f"  User ID: {user.id}\n\n"
            
            return result
            
    except Exception as e:
        return f"Error searching users: {str(e)}"

@mcp.tool()
async def remove_team_member(team_name: str, email: str) -> str:
    """Remove a member from a team.
    
    Args:
        team_name: The name of the team.
        email: The email address of the user to remove.
        
    Returns:
        Confirmation message.
    """
    try:
        _lazy_import_team_models()
        check_database_available()
        
        with SessionLocal() as session:
            # Find team
            team = session.query(Team).filter(
                Team.name.ilike(f"%{team_name}%"),
                Team.is_active == True
            ).first()
            
            if not team:
                return f"❌ Team '{team_name}' not found."
            
            # Find user
            user = session.query(User).filter(User.email == email).first()
            
            if not user:
                return f"❌ User with email '{email}' not found."
            
            # Find membership
            membership = session.query(TeamMembership).filter(
                TeamMembership.team_id == team.id,
                TeamMembership.user_id == user.id
            ).first()
            
            if not membership:
                return f"❌ {user.full_name} is not a member of '{team.name}'"
            
            # Don't allow removing the last owner
            if membership.role == TeamRole.OWNER:
                owner_count = session.query(TeamMembership).filter(
                    TeamMembership.team_id == team.id,
                    TeamMembership.role == TeamRole.OWNER
                ).count()
                
                if owner_count <= 1:
                    return f"❌ Cannot remove the last owner from the team. Assign another owner first."
            
            # Remove membership
            session.delete(membership)
            session.commit()
            
            result = f"✅ Team member removed successfully!\n\n"
            result += f"👤 {user.full_name} ({user.email})\n"
            result += f"🏢 Removed from: {team.name}\n"
            
            return result
            
    except Exception as e:
        return f"Error removing team member: {str(e)}"

@mcp.tool()
async def change_member_role(team_name: str, email: str, new_role: str) -> str:
    """Change a team member's role.
    
    Args:
        team_name: The name of the team.
        email: The email address of the user.
        new_role: The new role to assign (owner, admin, member, viewer).
        
    Returns:
        Confirmation message.
    """
    try:
        _lazy_import_team_models()
        check_database_available()
        
        with SessionLocal() as session:
            # Find team
            team = session.query(Team).filter(
                Team.name.ilike(f"%{team_name}%"),
                Team.is_active == True
            ).first()
            
            if not team:
                return f"❌ Team '{team_name}' not found."
            
            # Find user
            user = session.query(User).filter(User.email == email).first()
            
            if not user:
                return f"❌ User with email '{email}' not found."
            
            # Find membership
            membership = session.query(TeamMembership).filter(
                TeamMembership.team_id == team.id,
                TeamMembership.user_id == user.id
            ).first()
            
            if not membership:
                return f"❌ {user.full_name} is not a member of '{team.name}'"
            
            # Validate new role
            valid_roles = ["owner", "admin", "member", "viewer"]
            new_role_lower = new_role.lower()
            if new_role_lower not in valid_roles:
                return f"❌ Invalid role '{new_role}'. Valid roles are: {', '.join(valid_roles)}"
            
            # Map string to TeamRole enum
            new_role_enum = TeamRole[new_role_lower.upper()]
            old_role = membership.role
            
            # Update role
            membership.role = new_role_enum
            session.commit()
            
            result = f"✅ Member role updated successfully!\n\n"
            result += f"👤 {user.full_name} ({user.email})\n"
            result += f"🏢 Team: {team.name}\n"
            result += f"🎭 Role changed: {old_role.value} → {new_role_enum.value}\n"
            
            return result
            
    except Exception as e:
        return f"Error changing member role: {str(e)}"

@mcp.tool()
async def search_knowledge_base(query: str, top_k: int = 5) -> str:
    """Search the knowledge base using RAG (Retrieval-Augmented Generation) for unstructured documents.
    
    This tool uses vector embeddings and semantic search to find relevant information from
    the knowledge base, documentation, and other unstructured sources.
    
    Args:
        query: The search query or question.
        top_k: Number of results to return (default: 5).
        
    Returns:
        Relevant documents and information from the knowledge base.
    """
    try:
        # Import RAG service (lazy import to avoid circular dependencies)
        import sys
        import os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
        
        from convonet.rag_service import get_rag_service
        
        rag_service = get_rag_service()
        if not rag_service:
            return "⚠️ RAG service not available. Knowledge base search is not configured."
        
        # Perform retrieval
        results = rag_service.retrieve(query, top_k=top_k)
        
        if not results:
            return f"❌ No relevant documents found for query: '{query}'"
        
        # Format results
        response = f"📚 Found {len(results)} relevant document(s) for: '{query}'\n\n"
        
        for i, result in enumerate(results, 1):
            doc = result.document
            title = doc.metadata.get('title', 'Untitled Document')
            source = doc.metadata.get('source', 'unknown')
            score = result.score
            
            response += f"### {i}. {title} (Relevance: {score:.2f})\n"
            response += f"**Source**: {source}\n"
            response += f"**Content**: {doc.content[:300]}...\n\n"
        
        return response
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"❌ Error searching knowledge base: {str(e)}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
