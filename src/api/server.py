"""
Student Application Flask Server

Serves the student web app with:
- Static files (HTML, CSS, JS, MediaPipe models)
- REST API for consent
- MediaPipe file serving with correct MIME types
"""

import os
import sys
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import SECRET_KEY, SECURITY_HEADERS

# Initialize Flask app
app = Flask(__name__,
            static_folder='../../zoom_app',
            static_url_path='/static')
app.secret_key = SECRET_KEY
CORS(app)


@app.after_request
def after_request(response):
    """Apply security headers."""
    for header, value in SECURITY_HEADERS.items():
        response.headers[header] = value
    return response


# ============================================================
# Main UI Routes
# ============================================================

@app.route('/')
def index():
    """Serve the student app UI."""
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/mediapipe/<path:filename>')
def mediapipe_files(filename):
    """Serve MediaPipe files with correct MIME types."""
    mediapipe_folder = os.path.join(app.static_folder, 'mediapipe')

    try:
        response = send_from_directory(mediapipe_folder, filename)

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
# API
# ============================================================

@app.route('/api/consent', methods=['POST'])
def set_consent():
    """Handle student consent."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    return jsonify({"success": True})


@app.route('/api/status', methods=['GET'])
def get_status():
    """Get system status."""
    return jsonify({"status": "ready", "version": "2.0"})


# ============================================================
# Server Entry Point
# ============================================================

def create_app():
    return app


def run_server(host='127.0.0.1', port=5001, debug=False, ssl_context=None):
    """Run the Flask development server."""
    protocol = 'https' if ssl_context else 'http'
    print(f"  Serving student app on {protocol}://{host}:{port}")
    app.run(host=host, port=port, debug=debug, threaded=True, ssl_context=ssl_context)


if __name__ == '__main__':
    run_server(debug=True)
