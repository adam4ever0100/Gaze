#!/usr/bin/env python3
"""
Gaze Attention Monitoring System — Entry Point

Starts the student application server (serves the student web app
with video conferencing and attention detection).

The teacher dashboard runs on the backend server (backend/server.py).

Usage:
    python main.py [--port PORT] [--host HOST] [--debug]
    python main.py --backend   # Start the backend/teacher dashboard instead

Example:
    python main.py --port 5001          # Student app on port 5001
    python main.py --backend --port 5002 # Backend on port 5002
"""

import argparse
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import STUDENT_APP_PORT, BACKEND_PORT, DEBUG, SSL_ENABLED, SSL_CERT_PATH, SSL_KEY_PATH


def main():
    parser = argparse.ArgumentParser(
        description='Gaze Attention Monitoring System',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                    # Start student app on port 5001
  python main.py --backend          # Start backend + dashboard on port 5002
  python main.py --port 5001        # Student app on custom port
  python main.py --debug            # Enable debug mode
        """
    )

    parser.add_argument(
        '--port', '-p',
        type=int,
        default=None,
        help='Port to run the server on'
    )

    parser.add_argument(
        '--host', '-H',
        type=str,
        default='0.0.0.0',
        help='Host to bind to (default: 0.0.0.0 for LAN access)'
    )

    parser.add_argument(
        '--debug', '-d',
        action='store_true',
        default=DEBUG,
        help='Enable debug mode'
    )

    parser.add_argument(
        '--backend', '-b',
        action='store_true',
        default=False,
        help='Start the backend server (teacher dashboard) instead of student app'
    )

    parser.add_argument(
        '--ssl',
        action='store_true',
        default=SSL_ENABLED,
        help='Enable HTTPS with SSL'
    )

    args = parser.parse_args()

    # SSL config
    ssl_ctx = None
    protocol = 'http'
    if args.ssl:
        ssl_ctx = _get_ssl_context()
        protocol = 'https'

    if args.backend:
        port = args.port or BACKEND_PORT
        from backend.server import run_server
        run_server(host=args.host, port=port, debug=args.debug, ssl_context=ssl_ctx)
    else:
        port = args.port or STUDENT_APP_PORT
        print("""
╔═══════════════════════════════════════════════════════════╗
║         Gaze — Student Application                        ║
╠═══════════════════════════════════════════════════════════╣
║  🔒 Privacy-First Local Processing                        ║
║  • All video processing happens on your device            ║
║  • No video is stored or transmitted                      ║
║  • Only numeric attention scores are shared               ║
╠═══════════════════════════════════════════════════════════╣
║  📹 Built-in WebRTC Video Conferencing                    ║
║  • Peer-to-peer video calls with room system              ║
║  • Real-time attention monitoring during class             ║
╚═══════════════════════════════════════════════════════════╝
        """)
        print(f"  Student App:  {protocol}://{args.host}:{port}")
        if args.ssl:
            print(f"  🔒 SSL enabled")
        print(f"  Press Ctrl+C to stop\n")

        try:
            from src.api.server import run_server
            run_server(host=args.host, port=port, debug=args.debug, ssl_context=ssl_ctx)
        except KeyboardInterrupt:
            print("\nShutting down...")
            sys.exit(0)


def _get_ssl_context():
    """Get or create SSL context for HTTPS."""
    cert_path = SSL_CERT_PATH
    key_path = SSL_KEY_PATH

    if cert_path and key_path and os.path.exists(cert_path) and os.path.exists(key_path):
        print(f"  🔒 Using SSL cert: {cert_path}")
        return (cert_path, key_path)

    # Auto-generate self-signed cert
    try:
        import ssl
        import tempfile
        import subprocess

        cert_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
        os.makedirs(cert_dir, exist_ok=True)
        cert_path = os.path.join(cert_dir, 'self_signed.pem')
        key_path = os.path.join(cert_dir, 'self_signed_key.pem')

        if not os.path.exists(cert_path):
            print("  🔒 Generating self-signed SSL certificate...")
            subprocess.run([
                'openssl', 'req', '-x509', '-newkey', 'rsa:2048',
                '-keyout', key_path, '-out', cert_path,
                '-days', '365', '-nodes', '-batch',
                '-subj', '/CN=localhost'
            ], check=True, capture_output=True)
            print(f"  🔒 Certificate saved to {cert_path}")

        return (cert_path, key_path)
    except Exception as e:
        print(f"  ⚠️  SSL cert generation failed: {e}")
        print("  ⚠️  Running without SSL")
        return None


if __name__ == '__main__':
    main()
