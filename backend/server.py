"""
Backend Aggregation Server

Receives attention scores from multiple students and provides
aggregated data for the teacher dashboard.

Endpoints:
- POST /submit - Students send their attention scores
- GET /dashboard - Returns aggregated class data
- GET /dashboard/history - Returns time-series data for charts
"""

import os
import sys
import time
from datetime import datetime
from collections import defaultdict
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import BACKEND_PORT, DEBUG, THRESHOLD_FOCUSED, THRESHOLD_PARTIAL

# Initialize Flask app
app = Flask(__name__,
            static_folder='../teacher_dashboard',
            static_url_path='/static')
CORS(app)  # Allow cross-origin requests from student apps

# ============================================================
# In-Memory Storage
# ============================================================

# Storage structure:
# {
#     meeting_id: {
#         'students': {
#             student_name: {
#                 'attention_score': float,
#                 'status': str,
#                 'last_update': float,
#                 'history': [(timestamp, score), ...]
#             }
#         },
#         'class_history': [(timestamp, avg_score), ...],
#         'start_time': float
#     }
# }
meetings = defaultdict(lambda: {
    'students': {},
    'class_history': [],
    'start_time': time.time()
})


def classify_status(score):
    """Classify attention status based on score."""
    if score >= THRESHOLD_FOCUSED:
        return "Focused"
    elif score >= THRESHOLD_PARTIAL:
        return "Partially Attentive"
    else:
        return "Distracted"


# ============================================================
# Student Submission Endpoint
# ============================================================

@app.route('/submit', methods=['POST'])
def submit_score():
    """
    Receive attention score from a student.
    
    Expected payload:
    {
        "student_name": "John Doe",
        "meeting_id": "123456789",
        "attention_score": 0.85,
        "status": "Focused",
        "timestamp": 1234567890.123
    }
    """
    data = request.get_json()
    
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    student_name = data.get('student_name', 'Anonymous')
    meeting_id = data.get('meeting_id', 'default')
    score = float(data.get('attention_score', 0))
    status = data.get('status') or classify_status(score)
    timestamp = data.get('timestamp', time.time())
    
    # Update or create student record
    meeting = meetings[meeting_id]
    
    if student_name not in meeting['students']:
        meeting['students'][student_name] = {
            'attention_score': score,
            'status': status,
            'last_update': timestamp,
            'history': []
        }
    
    student = meeting['students'][student_name]
    student['attention_score'] = score
    student['status'] = status
    student['last_update'] = timestamp
    student['history'].append((timestamp, score))
    
    # Keep only last 100 history points per student
    if len(student['history']) > 100:
        student['history'] = student['history'][-100:]
    
    # Update class average history
    update_class_history(meeting_id)
    
    return jsonify({
        "success": True,
        "message": f"Score recorded for {student_name}"
    })


def update_class_history(meeting_id):
    """Calculate and store class average."""
    meeting = meetings[meeting_id]
    students = meeting['students']
    
    if not students:
        return
    
    # Calculate average of active students (updated in last 10 seconds)
    current_time = time.time()
    active_scores = [
        s['attention_score'] 
        for s in students.values() 
        if current_time - s['last_update'] < 10
    ]
    
    if active_scores:
        avg_score = sum(active_scores) / len(active_scores)
        meeting['class_history'].append((current_time, avg_score))
        
        # Keep only last 300 points (10 minutes at 2-second intervals)
        if len(meeting['class_history']) > 300:
            meeting['class_history'] = meeting['class_history'][-300:]


# ============================================================
# Dashboard Endpoints
# ============================================================

@app.route('/dashboard', methods=['GET'])
def get_dashboard():
    """
    Get current class dashboard data.
    
    Query params:
    - meeting_id: Filter by specific meeting (optional)
    
    Returns aggregated class data with student details.
    """
    meeting_id = request.args.get('meeting_id', 'default')
    meeting = meetings[meeting_id]
    
    current_time = time.time()
    
    # Get active students (updated in last 10 seconds)
    active_students = []
    all_scores = []
    
    for name, data in meeting['students'].items():
        is_active = current_time - data['last_update'] < 10
        
        student_info = {
            'name': name,
            'score': round(data['attention_score'], 3),
            'status': data['status'],
            'active': is_active,
            'last_update': data['last_update']
        }
        active_students.append(student_info)
        
        if is_active:
            all_scores.append(data['attention_score'])
    
    # Sort by name
    active_students.sort(key=lambda x: x['name'])
    
    # Calculate class statistics
    if all_scores:
        avg_score = sum(all_scores) / len(all_scores)
        min_score = min(all_scores)
        max_score = max(all_scores)
    else:
        avg_score = min_score = max_score = 0
    
    # Session duration
    session_duration = current_time - meeting['start_time']
    
    # Count by status
    status_counts = {
        'focused': sum(1 for s in active_students if s['status'] == 'Focused' and s['active']),
        'partial': sum(1 for s in active_students if s['status'] == 'Partially Attentive' and s['active']),
        'distracted': sum(1 for s in active_students if s['status'] == 'Distracted' and s['active'])
    }
    
    return jsonify({
        "success": True,
        "meeting_id": meeting_id,
        "class_average": round(avg_score, 3),
        "class_status": classify_status(avg_score),
        "total_students": len(active_students),
        "active_students": len(all_scores),
        "min_score": round(min_score, 3),
        "max_score": round(max_score, 3),
        "status_counts": status_counts,
        "session_duration": round(session_duration),
        "students": active_students,
        "timestamp": current_time
    })


@app.route('/dashboard/history', methods=['GET'])
def get_history():
    """
    Get time-series data for the class average chart.
    
    Query params:
    - meeting_id: Filter by specific meeting (optional)
    - limit: Maximum number of points to return (default: 60)
    """
    meeting_id = request.args.get('meeting_id', 'default')
    limit = int(request.args.get('limit', 60))
    
    meeting = meetings[meeting_id]
    history = meeting['class_history'][-limit:]
    
    # Convert to chart-friendly format
    labels = []
    data = []
    
    for timestamp, score in history:
        dt = datetime.fromtimestamp(timestamp)
        labels.append(dt.strftime('%H:%M:%S'))
        data.append(round(score * 100, 1))  # Convert to percentage
    
    return jsonify({
        "success": True,
        "meeting_id": meeting_id,
        "labels": labels,
        "data": data,
        "count": len(data)
    })


@app.route('/meetings', methods=['GET'])
def list_meetings():
    """List all active meetings."""
    meeting_list = []
    
    for meeting_id, meeting in meetings.items():
        meeting_list.append({
            'meeting_id': meeting_id,
            'student_count': len(meeting['students']),
            'start_time': meeting['start_time']
        })
    
    return jsonify({
        "success": True,
        "meetings": meeting_list
    })


# ============================================================
# Teacher Dashboard UI
# ============================================================

@app.route('/')
def index():
    """Serve the teacher dashboard UI."""
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/<path:filename>')
def static_files(filename):
    """Serve static files."""
    return send_from_directory(app.static_folder, filename)


# ============================================================
# Server Entry Point
# ============================================================

def run_server(host='127.0.0.1', port=None, debug=None):
    """Run the Flask development server."""
    port = port or BACKEND_PORT
    debug = debug if debug is not None else DEBUG
    
    print(f"Starting Backend Server on http://{host}:{port}")
    print("Teacher Dashboard available at this URL")
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == '__main__':
    run_server()
