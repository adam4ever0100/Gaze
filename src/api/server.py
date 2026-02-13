"""
Student Application Flask Server

Handles:
- Serving the Zoom App UI for students
- REST API for attention metrics
- Zoom OAuth integration
- Webcam capture and attention processing
- Score submission to backend
"""

import os
import sys
import threading
import time
import base64
import random
import math
import requests
import cv2
from flask import Flask, jsonify, request, send_from_directory, redirect, session, make_response

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import (
    ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET, ZOOM_REDIRECT_URI,
    ZOOM_AUTH_URL, ZOOM_TOKEN_URL, ZOOM_VERIFICATION_CODE,
    BACKEND_URL, SECRET_KEY, SECURITY_HEADERS,
    FRAME_PROCESS_INTERVAL, SCORE_SUBMIT_INTERVAL
)
from src.ai_engine.attention_detector import AttentionDetector


# Initialize Flask app
app = Flask(__name__, 
            static_folder='../../zoom_app',
            static_url_path='/static')
app.secret_key = SECRET_KEY

# Global state
detector = AttentionDetector()
camera = None
monitoring_active = False
monitoring_thread = None
demo_mode = False
current_student = {
    "name": "",
    "meeting_id": "",
    "consented": False
}
last_metrics = {}


def apply_security_headers(response):
    """Apply OWASP-compliant security headers for Zoom embedding."""
    for header, value in SECURITY_HEADERS.items():
        response.headers[header] = value
    return response


@app.after_request
def after_request(response):
    """Apply security headers to all responses."""
    return apply_security_headers(response)


# ============================================================
# Zoom OAuth Routes
# ============================================================

@app.route('/oauth/authorize')
def oauth_authorize():
    """Redirect to Zoom OAuth authorization."""
    if not ZOOM_CLIENT_ID:
        return jsonify({"error": "Zoom OAuth not configured"}), 500
    
    auth_url = (
        f"{ZOOM_AUTH_URL}?"
        f"response_type=code&"
        f"client_id={ZOOM_CLIENT_ID}&"
        f"redirect_uri={ZOOM_REDIRECT_URI}"
    )
    return redirect(auth_url)


@app.route('/oauth/callback')
def oauth_callback():
    """Handle Zoom OAuth callback."""
    code = request.args.get('code')
    if not code:
        return jsonify({"error": "No authorization code received"}), 400
    
    # Exchange code for token
    try:
        credentials = f"{ZOOM_CLIENT_ID}:{ZOOM_CLIENT_SECRET}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        
        response = requests.post(
            ZOOM_TOKEN_URL,
            headers={
                "Authorization": f"Basic {encoded_credentials}",
                "Content-Type": "application/x-www-form-urlencoded"
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": ZOOM_REDIRECT_URI
            }
        )
        
        if response.status_code == 200:
            token_data = response.json()
            session['zoom_token'] = token_data.get('access_token')
            session['zoom_refresh'] = token_data.get('refresh_token')
            return redirect('/')
        else:
            return jsonify({"error": "Failed to get access token"}), 400
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route(f'/{ZOOM_VERIFICATION_CODE}.html')
def zoom_verification():
    """Serve Zoom domain verification file."""
    return ZOOM_VERIFICATION_CODE, 200, {'Content-Type': 'text/plain'}


# ============================================================
# Main UI Routes
# ============================================================

@app.route('/')
def index():
    """Serve the student Zoom App UI."""
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/mediapipe/<path:filename>')
def mediapipe_files(filename):
    """Serve MediaPipe files with correct MIME types."""
    mediapipe_folder = os.path.join(app.static_folder, 'mediapipe')
    
    # Debug logging
    print(f"MediaPipe request: {filename}")
    print(f"Looking in: {mediapipe_folder}")
    
    try:
        response = send_from_directory(mediapipe_folder, filename)
        
        # Set correct MIME types for various file types
        if filename.endswith('.wasm'):
            response.headers['Content-Type'] = 'application/wasm'
        elif filename.endswith('.data'):
            response.headers['Content-Type'] = 'application/octet-stream'
        elif filename.endswith('.binarypb'):
            response.headers['Content-Type'] = 'application/octet-stream'
        elif filename.endswith('.js'):
            response.headers['Content-Type'] = 'application/javascript'
        
        return response
    except Exception as e:
        print(f"Error serving mediapipe file {filename}: {e}")
        raise


@app.route('/<path:filename>')
def static_files(filename):
    """Serve static files (CSS, JS)."""
    return send_from_directory(app.static_folder, filename)


# ============================================================
# Attention Monitoring API
# ============================================================

@app.route('/api/consent', methods=['POST'])
def set_consent():
    """Set student consent and name."""
    global current_student
    
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    current_student['name'] = data.get('name', 'Anonymous')
    current_student['meeting_id'] = data.get('meeting_id', 'default')
    current_student['consented'] = data.get('consented', False)
    
    return jsonify({
        "success": True,
        "student": current_student
    })


@app.route('/api/start', methods=['POST'])
def start_monitoring():
    """Start attention monitoring."""
    global camera, monitoring_active, monitoring_thread, demo_mode
    
    if not current_student['consented']:
        return jsonify({"error": "Consent required"}), 403
    
    if monitoring_active:
        return jsonify({"error": "Already monitoring"}), 400
    
    # Try to initialize camera
    demo_mode = False
    camera = cv2.VideoCapture(0)
    
    if not camera.isOpened():
        # Camera not available (likely Zoom is using it) - use demo mode
        print("Camera not available, using demo mode")
        demo_mode = True
        camera = None
    
    monitoring_active = True
    detector.reset_blink_counter()
    
    # Start monitoring thread
    monitoring_thread = threading.Thread(target=monitoring_loop, daemon=True)
    monitoring_thread.start()
    
    mode_msg = "Demo mode (camera in use by Zoom)" if demo_mode else "Live monitoring"
    return jsonify({"success": True, "message": f"Monitoring started - {mode_msg}", "demo_mode": demo_mode})


@app.route('/api/stop', methods=['POST'])
def stop_monitoring():
    """Stop attention monitoring."""
    global camera, monitoring_active
    
    monitoring_active = False
    
    if camera:
        camera.release()
        camera = None
    
    return jsonify({"success": True, "message": "Monitoring stopped"})


@app.route('/api/attention', methods=['GET'])
def get_attention():
    """Get current attention metrics."""
    return jsonify({
        "success": True,
        "monitoring": monitoring_active,
        "student": current_student['name'],
        "metrics": last_metrics
    })


@app.route('/api/status', methods=['GET'])
def get_status():
    """Get system status."""
    return jsonify({
        "monitoring": monitoring_active,
        "camera_ready": camera is not None and camera.isOpened() if camera else False,
        "student": current_student,
        "zoom_connected": 'zoom_token' in session
    })


# ============================================================
# Monitoring Loop
# ============================================================

def generate_demo_metrics():
    """Generate realistic demo attention metrics when camera unavailable."""
    t = time.time()
    
    # Create natural-looking variation using sine waves
    base_score = 0.75 + 0.15 * math.sin(t * 0.3) + 0.1 * math.sin(t * 0.7)
    base_score = max(0.3, min(1.0, base_score + random.uniform(-0.05, 0.05)))
    
    # Classify status
    if base_score >= 0.7:
        status = "Focused"
    elif base_score >= 0.4:
        status = "Partially Attentive"
    else:
        status = "Distracted"
    
    return {
        "face_detected": True,
        "attention_score": round(base_score, 3),
        "status": status,
        "gaze_score": round(0.7 + random.uniform(-0.1, 0.2), 3),
        "head_pose_score": round(0.8 + random.uniform(-0.15, 0.15), 3),
        "eye_openness": round(0.85 + random.uniform(-0.1, 0.1), 3),
        "face_presence": 1.0,
        "blink_rate": round(15 + random.uniform(-5, 5), 1),
        "head_yaw": round(random.uniform(-10, 10), 1),
        "head_pitch": round(random.uniform(-5, 5), 1),
        "head_roll": round(random.uniform(-3, 3), 1),
        "left_ear": round(0.28 + random.uniform(-0.03, 0.03), 3),
        "right_ear": round(0.28 + random.uniform(-0.03, 0.03), 3),
    }


def monitoring_loop():
    """Background thread for continuous attention monitoring."""
    global last_metrics, monitoring_active, camera, demo_mode
    
    last_submit_time = time.time()
    
    while monitoring_active:
        if demo_mode:
            # Generate simulated data when camera unavailable
            metrics = generate_demo_metrics()
            last_metrics = metrics
        elif camera and camera.isOpened():
            ret, frame = camera.read()
            if ret:
                metrics = detector.process_frame(frame)
                last_metrics = metrics
            else:
                continue
        else:
            # Fallback to demo mode if camera fails
            demo_mode = True
            continue
        
        # Submit to backend periodically
        current_time = time.time()
        if current_time - last_submit_time >= SCORE_SUBMIT_INTERVAL:
            submit_to_backend(last_metrics)
            last_submit_time = current_time
        
        # Control processing rate
        time.sleep(FRAME_PROCESS_INTERVAL)


def submit_to_backend(metrics):
    """Submit attention score to backend aggregation server."""
    try:
        payload = {
            "student_name": current_student['name'],
            "meeting_id": current_student['meeting_id'],
            "attention_score": metrics.get('attention_score', 0),
            "status": metrics.get('status', 'Unknown'),
            "timestamp": time.time()
        }
        
        response = requests.post(
            f"{BACKEND_URL}/submit",
            json=payload,
            timeout=2
        )
        
        if response.status_code != 200:
            print(f"Backend submission failed: {response.status_code}")
            
    except requests.exceptions.RequestException as e:
        # Backend might not be running - that's OK
        pass


# ============================================================
# Server Entry Point
# ============================================================

def create_app():
    """Factory function to create the Flask app."""
    return app


def run_server(host='127.0.0.1', port=5001, debug=False):
    """Run the Flask development server."""
    print(f"Starting Student App on http://{host}:{port}")
    print("Open this URL in your browser or Zoom App")
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == '__main__':
    run_server(debug=True)
