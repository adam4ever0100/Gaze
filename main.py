#!/usr/bin/env python3
"""
Zoom Attention Monitoring System - Student Application Entry Point

Usage:
    python main.py [--port PORT] [--host HOST] [--debug]

Example:
    python main.py --port 5001
"""

import argparse
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import STUDENT_APP_PORT, DEBUG
from src.api.server import run_server


def main():
    parser = argparse.ArgumentParser(
        description='Zoom Attention Monitoring System - Student Application',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                    # Start on default port 5001
  python main.py --port 5001        # Start on port 5001
  python main.py --debug            # Enable debug mode
        """
    )
    
    parser.add_argument(
        '--port', '-p',
        type=int,
        default=STUDENT_APP_PORT,
        help=f'Port to run the server on (default: {STUDENT_APP_PORT})'
    )
    
    parser.add_argument(
        '--host', '-H',
        type=str,
        default='127.0.0.1',
        help='Host to bind to (default: 127.0.0.1)'
    )
    
    parser.add_argument(
        '--debug', '-d',
        action='store_true',
        default=DEBUG,
        help='Enable debug mode'
    )
    
    args = parser.parse_args()
    
    print("""
╔═══════════════════════════════════════════════════════════╗
║     Zoom Attention Monitoring System - Student App        ║
╠═══════════════════════════════════════════════════════════╣
║  Privacy-First Local Processing                           ║
║  • All video processing happens on your device            ║
║  • No video is stored or transmitted                      ║
║  • Only numeric attention scores are shared               ║
╚═══════════════════════════════════════════════════════════╝
    """)
    
    print(f"Starting server on http://{args.host}:{args.port}")
    print("Open this URL in your browser or Zoom App\n")
    print("Press Ctrl+C to stop\n")
    
    try:
        run_server(host=args.host, port=args.port, debug=args.debug)
    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)


if __name__ == '__main__':
    main()
