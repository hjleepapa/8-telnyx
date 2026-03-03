import os
import sys
from fastapi import FastAPI
from a2wsgi import WSGIApp

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the existing Flask app factory
from app import create_app

# 1. Initialize the Flask application
# Note: In the hybrid setup, we don't start the Flask-SocketIO eventlet loop directly here.
# Flask will handle its standard HTTP routes via the a2wsgi bridge.
flask_app = create_app()

# 2. Initialize FastAPI (The Async Master)
api = FastAPI(
    title="Convonet Hybrid API",
    description="FastAPI gateway with integrated Flask monolith",
    version="1.0.0"
)

# 3. Mount the new FastAPI Voice Gateway
from convonet.fastapi_voice_gateway import router as voice_router
api.include_router(voice_router, prefix="/fastapi", tags=["Voice Gateway"])

# 4. Mount the entire Flask application at the root
# a2wsgi wraps the WSGI (Flask) application so it can run inside the ASGI (FastAPI) loop.
api.mount("/", WSGIApp(flask_app))

@api.on_event("startup")
async def startup_event():
    print("🚀 Convonet Hybrid Monolith is starting up...")
    print("✅ FastAPI Gateway initialized")
    print("✅ Flask Monolith mounted via a2wsgi")

if __name__ == "__main__":
    import uvicorn
    # This is for local testing. In production, Render uses the startCommand.
    uvicorn.run("asgi_main:api", host="0.0.0.0", port=8000, reload=True)
