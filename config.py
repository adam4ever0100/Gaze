"""
Configuration constants for the Zoom Attention Monitoring System.
"""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ============================================================
# Zoom OAuth Configuration
# ============================================================
ZOOM_CLIENT_ID = os.getenv("ZOOM_CLIENT_ID", "")
ZOOM_CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET", "")
ZOOM_REDIRECT_URI = os.getenv("ZOOM_REDIRECT_URI", "http://127.0.0.1:5001/oauth/callback")
ZOOM_VERIFICATION_CODE = os.getenv("ZOOM_VERIFICATION_CODE", "")

ZOOM_AUTH_URL = "https://zoom.us/oauth/authorize"
ZOOM_TOKEN_URL = "https://zoom.us/oauth/token"
ZOOM_API_BASE = "https://api.zoom.us/v2"

# ============================================================
# Server Configuration
# ============================================================
STUDENT_APP_PORT = int(os.getenv("STUDENT_APP_PORT", 5001))
BACKEND_PORT = int(os.getenv("BACKEND_PORT", 5002))
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:5002")
DEBUG = os.getenv("DEBUG", "True").lower() == "true"
SECRET_KEY = os.getenv("SECRET_KEY", "development-secret-key")

# ============================================================
# Attention Detection Configuration
# ============================================================
# Score weights (must sum to 1.0)
WEIGHT_GAZE = 0.35
WEIGHT_HEAD_POSE = 0.30
WEIGHT_EYE_OPENNESS = 0.25
WEIGHT_FACE_PRESENCE = 0.10

# Thresholds for attention classification
THRESHOLD_FOCUSED = 0.70      # >= 70% = Focused
THRESHOLD_PARTIAL = 0.40      # >= 40% = Partially Attentive
# Below 40% = Distracted

# Eye Aspect Ratio (EAR) thresholds
EAR_THRESHOLD_CLOSED = 0.2    # Below this = eyes closed
EAR_THRESHOLD_OPEN = 0.25     # Above this = eyes fully open
BLINK_CONSECUTIVE_FRAMES = 3  # Frames to confirm a blink

# Head pose thresholds (degrees)
HEAD_YAW_THRESHOLD = 30       # Max left/right rotation
HEAD_PITCH_THRESHOLD = 25     # Max up/down rotation

# Gaze thresholds
GAZE_THRESHOLD = 0.3          # Max deviation from center

# ============================================================
# Processing Configuration
# ============================================================
FRAME_PROCESS_INTERVAL = 0.1  # Process every 100ms (10 FPS)
SCORE_SUBMIT_INTERVAL = 2.0   # Submit score every 2 seconds
DASHBOARD_POLL_INTERVAL = 2   # Dashboard polls every 2 seconds

# ============================================================
# Security Headers for Zoom Embedding
# ============================================================
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://appssdk.zoom.us https://cdn.jsdelivr.net blob:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "connect-src 'self' http://127.0.0.1:* https://*.ngrok-free.dev https://cdn.jsdelivr.net ws://127.0.0.1:* blob:; "
        "worker-src 'self' blob:; "
        "frame-ancestors https://*.zoom.us https://*.zoom.com;"
    ),
    "X-Frame-Options": "ALLOW-FROM https://zoom.us",
    "X-Content-Type-Options": "nosniff",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}
