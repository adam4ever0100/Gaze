"""
Configuration constants for the Gaze Attention Monitoring System.
"""
import os
import sys
import warnings
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


# ============================================================
# Environment Validation
# ============================================================

def validate_config():
    """Validate configuration at startup. Warns about insecure defaults."""
    errors = []
    warns = []

    # Check required port ranges
    for name, val in [("STUDENT_APP_PORT", STUDENT_APP_PORT), ("BACKEND_PORT", BACKEND_PORT)]:
        if not (1024 <= val <= 65535):
            errors.append(f"{name}={val} is out of valid range (1024-65535)")

    # Warn about default secrets
    if SECRET_KEY == "development-secret-key" or SECRET_KEY == "development-secret-key-change-in-production":
        warns.append("SECRET_KEY is using the default value. Set a unique key for production.")

    if TEACHER_PASSWORD == "teacher123":
        warns.append("TEACHER_PASSWORD is using the default 'teacher123'. Change it for production.")

    # Validate weights sum to 1.0
    total = WEIGHT_GAZE + WEIGHT_HEAD_POSE + WEIGHT_EYE_OPENNESS + WEIGHT_FACE_PRESENCE
    if abs(total - 1.0) > 0.01:
        errors.append(f"Attention weights sum to {total}, expected 1.0")

    # Validate thresholds
    if THRESHOLD_FOCUSED <= THRESHOLD_PARTIAL:
        errors.append("THRESHOLD_FOCUSED must be greater than THRESHOLD_PARTIAL")

    # Print warnings
    for w in warns:
        warnings.warn(f"[Gaze Config] ⚠️  {w}", stacklevel=2)

    # Print errors and exit if critical
    if errors:
        for e in errors:
            print(f"[Gaze Config] ❌ {e}", file=sys.stderr)
        print("[Gaze Config] Fix the above configuration errors.", file=sys.stderr)
        sys.exit(1)


# ============================================================
# Server Configuration
# ============================================================
STUDENT_APP_PORT = int(os.getenv("STUDENT_APP_PORT", 5001))
BACKEND_PORT = int(os.getenv("BACKEND_PORT", 5002))
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:5002")
DEBUG = os.getenv("DEBUG", "True").lower() == "true"
SECRET_KEY = os.getenv("SECRET_KEY", "development-secret-key")

# ============================================================
# SSL / HTTPS Configuration
# ============================================================
SSL_ENABLED = os.getenv("SSL_ENABLED", "False").lower() == "true"
SSL_CERT_PATH = os.getenv("SSL_CERT_PATH", "")
SSL_KEY_PATH = os.getenv("SSL_KEY_PATH", "")

# ============================================================
# WebRTC Configuration
# ============================================================
ICE_SERVERS = [
    {"urls": "stun:stun.l.google.com:19302"},
    {"urls": "stun:stun1.l.google.com:19302"},
]

# Add TURN server if configured
_TURN_URL = os.getenv("TURN_SERVER_URL", "")
_TURN_USER = os.getenv("TURN_SERVER_USERNAME", "")
_TURN_CRED = os.getenv("TURN_SERVER_CREDENTIAL", "")
if _TURN_URL:
    ICE_SERVERS.append({
        "urls": _TURN_URL,
        "username": _TURN_USER,
        "credential": _TURN_CRED,
    })

# ============================================================
# LiveKit SFU Configuration
# ============================================================
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "secret")
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://localhost:7880")

# ============================================================
# Teacher Authentication
# ============================================================
TEACHER_PASSWORD = os.getenv("TEACHER_PASSWORD", "teacher123")

# ============================================================
# AI / Gemini Configuration
# ============================================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

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
# Alert Configuration
# ============================================================
ALERT_THRESHOLD = 0.35        # Below this triggers a distraction alert
ALERT_COOLDOWN = 30           # Seconds between alerts for same student

# ============================================================
# Processing Configuration
# ============================================================
FRAME_PROCESS_INTERVAL = 0.1  # Process every 100ms (10 FPS)
SCORE_SUBMIT_INTERVAL = 2.0   # Submit score every 2 seconds
DASHBOARD_POLL_INTERVAL = 2   # Dashboard polls every 2 seconds

# ============================================================
# Rate Limiting
# ============================================================
RATE_LIMIT_PER_SID = int(os.getenv("RATE_LIMIT_PER_SID", 5))       # Max requests per second per socket
RATE_LIMIT_PER_IP = int(os.getenv("RATE_LIMIT_PER_IP", 60))        # Max requests per second per IP
MAX_CONNECTIONS_PER_IP = int(os.getenv("MAX_CONNECTIONS_PER_IP", 30))  # Max sockets per IP
MAX_STUDENTS_PER_ROOM = int(os.getenv("MAX_STUDENTS_PER_ROOM", 25))   # Max students per classroom room

# ============================================================
# Security Headers
# ============================================================
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://cdn.socket.io blob:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: blob:; "
        "connect-src 'self' "
        "https://*.livekit.cloud wss://*.livekit.cloud "
        "https://*.duckdns.org wss://*.duckdns.org "
        "ws://localhost:* http://localhost:* blob:; "
        "worker-src 'self' blob:; "
        "media-src 'self' blob: data:;"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}

# ============================================================
# Run validation on import
# ============================================================
validate_config()
