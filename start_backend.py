"""
Gaze — Backend startup script.
Used by Docker to start the Flask-SocketIO server with eventlet.
"""
import eventlet
eventlet.monkey_patch()

from backend.server import app, socketio

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5002, log_output=True)
