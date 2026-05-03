"""
Backend Server with WebRTC Signaling and Real-time Dashboard.

Uses Flask-SocketIO for:
- WebRTC signaling (offer/answer/ICE candidate exchange)
- Room management (create/join/leave)
- Real-time attention score broadcasting to teacher dashboard
- Teacher annotations
- Session persistence via SQLite

REST Endpoints:
- GET  /                           - Teacher dashboard UI
- GET  /dashboard                  - Dashboard data (JSON)
- GET  /dashboard/history          - Time-series chart data
- GET  /sessions                   - Past session list
- GET  /sessions/<id>/export       - Export session as CSV
- GET  /sessions/<id>/summary      - Session summary
- GET  /sessions/<id>/analytics    - Detailed analytics
- GET  /sessions/<id>/attendance   - Attendance report
- GET  /sessions/<id>/ai-summary   - AI-generated summary
- GET  /sessions/<id>/annotations  - Session annotations
- POST /submit                     - Score submission (HTTP fallback)

Socket.IO Events:
- create-room      → Room created, teacher joins
- join-room        → Student joins a room
- leave-room       → Participant leaves
- offer            → WebRTC SDP offer relay
- answer           → WebRTC SDP answer relay
- ice-candidate    → ICE candidate relay
- attention-score  → Student sends attention score
- send-message     → Chat message from participant
- add-annotation   → Teacher adds annotation
- start-screen-share → Screen share started
- stop-screen-share  → Screen share stopped
"""

import os
import sys
import time
import string
import random
from datetime import datetime
from collections import defaultdict
from functools import wraps

from flask import Flask, jsonify, request, send_from_directory, send_file
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    BACKEND_PORT, DEBUG, SECRET_KEY,
    THRESHOLD_FOCUSED, THRESHOLD_PARTIAL,
    SECURITY_HEADERS, ICE_SERVERS,
    TEACHER_PASSWORD, ALERT_THRESHOLD, ALERT_COOLDOWN,
    RATE_LIMIT_PER_SID, RATE_LIMIT_PER_IP, MAX_CONNECTIONS_PER_IP,
    MAX_STUDENTS_PER_ROOM,
    LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_URL
)
from backend.database import (
    init_db, create_session, end_session, add_student, student_left,
    record_attention, get_session_summary, get_active_sessions,
    get_past_sessions, export_session_csv, get_student_timeline,
    add_annotation, get_annotations, delete_annotation,
    get_attendance_report, generate_ai_summary
)

# Initialize database
init_db()

# ============================================================
# Flask & SocketIO Setup
# ============================================================

app = Flask(__name__,
            static_folder='../teacher_dashboard',
            static_url_path='/static')
app.secret_key = SECRET_KEY
CORS(app, resources={r"/*": {"origins": "*"}})

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')


# ============================================================
# In-Memory State
# ============================================================

# Room state
rooms = {}

# Map socket IDs to room codes for cleanup
sid_to_room = {}

# Alert cooldowns: { (room_code, student_name): last_alert_time }
alert_cooldowns = {}

# Rate limiting per SID: { sid: [timestamp, ...] }
rate_limits_sid = defaultdict(list)

# Rate limiting per IP: { ip: [timestamp, ...] }
rate_limits_ip = defaultdict(list)

# Connections per IP: { ip: set(sids) }
connections_per_ip = defaultdict(set)

# Chat history per room
chat_history = defaultdict(list)


def generate_room_code():
    """Generate a 6-character room code."""
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(random.choices(chars, k=6))
        if code not in rooms:
            return code


def classify_status(score):
    """Classify attention status based on score."""
    if score >= THRESHOLD_FOCUSED:
        return "Focused"
    elif score >= THRESHOLD_PARTIAL:
        return "Partially Attentive"
    else:
        return "Distracted"


def check_rate_limit(sid):
    """Check per-SID rate limit. Returns True if allowed."""
    now = time.time()
    timestamps = rate_limits_sid[sid]
    rate_limits_sid[sid] = [t for t in timestamps if now - t < 1.0]
    if len(rate_limits_sid[sid]) >= RATE_LIMIT_PER_SID:
        return False
    rate_limits_sid[sid].append(now)
    return True


def check_ip_rate_limit(ip):
    """Check per-IP rate limit. Returns True if allowed."""
    now = time.time()
    timestamps = rate_limits_ip[ip]
    rate_limits_ip[ip] = [t for t in timestamps if now - t < 1.0]
    if len(rate_limits_ip[ip]) >= RATE_LIMIT_PER_IP:
        return False
    rate_limits_ip[ip].append(now)
    return True


def require_room_member(f):
    """Decorator to verify the socket is a member of a room."""
    @wraps(f)
    def decorated(*args, **kwargs):
        room_code = sid_to_room.get(request.sid)
        if not room_code or room_code not in rooms:
            emit('error', {'message': 'Not in a room'})
            return
        return f(*args, **kwargs)
    return decorated


# ============================================================
# Security Headers
# ============================================================

@app.after_request
def after_request(response):
    for header, value in SECURITY_HEADERS.items():
        response.headers[header] = value
    return response


# ============================================================
# Socket.IO — Connection Throttle
# ============================================================

@socketio.on('connect')
def handle_connect():
    """Track connections per IP for throttling."""
    ip = request.remote_addr or '127.0.0.1'
    connections_per_ip[ip].add(request.sid)
    if len(connections_per_ip[ip]) > MAX_CONNECTIONS_PER_IP:
        emit('error', {'message': 'Too many connections from your IP'})
        return False  # Reject connection


# ============================================================
# Socket.IO Events — Room Management
# ============================================================

@socketio.on('create-room')
def handle_create_room(data):
    """Teacher creates a new room."""
    teacher_name = data.get('teacher_name', 'Teacher')
    password = data.get('password', '')

    if password != TEACHER_PASSWORD:
        emit('error', {'message': 'Invalid teacher password'})
        return

    room_code = generate_room_code()

    # Create DB session
    session_id = create_session(room_code, teacher_name)

    rooms[room_code] = {
        'teacher_sid': request.sid,
        'teacher_name': teacher_name,
        'session_id': session_id,
        'students': {},
        'class_history': [],
        'created_at': time.time(),
        'share_allowed': set()   # SIDs of students allowed to screen share
    }

    sid_to_room[request.sid] = room_code
    join_room(room_code)

    emit('room-created', {
        'room_code': room_code,
        'session_id': session_id,
        'ice_servers': ICE_SERVERS
    })

    print(f"Room {room_code} created by {teacher_name}")


@socketio.on('join-room')
def handle_join_room(data):
    """Student joins an existing room."""
    room_code = data.get('room_code', '').upper().strip()
    student_name = data.get('student_name', 'Anonymous')

    if room_code not in rooms:
        emit('error', {'message': f'Room {room_code} not found'})
        return

    room = rooms[room_code]

    # Enforce room capacity
    if len(room['students']) >= MAX_STUDENTS_PER_ROOM:
        emit('error', {'message': f'Room is full ({MAX_STUDENTS_PER_ROOM} students max)'})
        return

    # Add student to DB
    student_db_id = add_student(room['session_id'], student_name)

    room['students'][request.sid] = {
        'name': student_name,
        'student_db_id': student_db_id,
        'score': 0,
        'status': 'Connecting',
        'last_update': time.time(),
        'history': []
    }

    sid_to_room[request.sid] = room_code
    join_room(room_code)

    # Notify the student
    emit('room-joined', {
        'room_code': room_code,
        'ice_servers': ICE_SERVERS,
        'participants': [
            {'sid': sid, 'name': s['name'], 'is_teacher': False}
            for sid, s in room['students'].items()
        ] + [{'sid': room['teacher_sid'], 'name': room['teacher_name'], 'is_teacher': True}]
    })

    # Notify everyone else
    emit('peer-joined', {
        'sid': request.sid,
        'name': student_name,
        'is_teacher': False
    }, room=room_code, skip_sid=request.sid)

    # Update teacher dashboard
    emit('student-joined', {
        'name': student_name,
        'sid': request.sid,
        'student_count': len(room['students'])
    }, room=room_code)

    print(f"Student {student_name} joined room {room_code}")


@socketio.on('leave-room')
def handle_leave_room(data=None):
    _handle_disconnect(request.sid)


# ============================================================
# Socket.IO Events — LiveKit Token Generation
# ============================================================

@socketio.on('get-livekit-token')
@require_room_member
def handle_get_livekit_token(data=None):
    """Generate a LiveKit access token for the requesting participant."""
    from livekit import api as livekit_api

    room_code = sid_to_room.get(request.sid)
    room = rooms.get(room_code)
    if not room:
        emit('error', {'message': 'Room not found'})
        return

    is_teacher = request.sid == room.get('teacher_sid')
    student = room['students'].get(request.sid)

    if is_teacher:
        identity = f"teacher-{request.sid}"
        name = room.get('teacher_name', 'Teacher')
    elif student:
        identity = f"student-{request.sid}"
        name = student['name']
    else:
        emit('error', {'message': 'Not a room member'})
        return

    from datetime import timedelta

    token = (
        livekit_api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name(name)
        .with_grants(
            livekit_api.VideoGrants(
                room_join=True,
                room=room_code,
                can_publish=True,
                can_subscribe=True
            )
        )
        .with_ttl(timedelta(hours=4))
    )

    jwt_token = token.to_jwt()
    emit('livekit-token', {
        'token': jwt_token,
        'url': LIVEKIT_URL,
        'identity': identity
    })


@socketio.on('mute-all')
@require_room_member
def handle_mute_all(data=None):
    """Teacher mutes all students' microphones."""
    room_code = sid_to_room.get(request.sid)
    room = rooms.get(room_code)
    if not room:
        return

    if request.sid != room.get('teacher_sid'):
        emit('error', {'message': 'Only the teacher can mute all'})
        return

    emit('force-mute', {}, room=room_code, skip_sid=request.sid)
    print(f"Teacher muted all in room {room_code}")


@socketio.on('kick-student')
@require_room_member
def handle_kick_student(data):
    """Teacher removes a student from the room."""
    room_code = sid_to_room.get(request.sid)
    room = rooms.get(room_code)
    if not room or request.sid != room.get('teacher_sid'):
        emit('error', {'message': 'Only the teacher can kick students'})
        return

    target_sid = data.get('target_sid')
    if not target_sid or target_sid not in room['students']:
        return

    student = room['students'][target_sid]
    student_name = student['name']

    # Notify the kicked student
    emit('room-kicked', {'message': 'You were removed from the classroom by the teacher.'}, to=target_sid)

    # Remove from room state (disconnect handler will clean up the rest)
    _handle_disconnect(target_sid)

    # Notify the teacher
    emit('student-kicked', {'name': student_name, 'sid': target_sid}, to=request.sid)
    print(f"Teacher kicked {student_name} from room {room_code}")


@socketio.on('nudge-student')
@require_room_member
def handle_nudge_student(data):
    """Teacher sends a private attention reminder to one student."""
    room_code = sid_to_room.get(request.sid)
    room = rooms.get(room_code)
    if not room or request.sid != room.get('teacher_sid'):
        return

    target_sid = data.get('target_sid')
    message = data.get('message', '👋 Your teacher is asking you to pay attention.')
    if target_sid and target_sid in room['students']:
        emit('attention-nudge', {'message': message}, to=target_sid)
        print(f"Teacher nudged student {target_sid} in room {room_code}")


# ============================================================
# Socket.IO Events — Chat
# ============================================================

@socketio.on('send-message')
@require_room_member
def handle_send_message(data):
    """Relay chat message to all participants in the room."""
    room_code = sid_to_room.get(request.sid)
    room = rooms[room_code]

    # IP rate limit for chat
    ip = request.remote_addr or '127.0.0.1'
    if not check_ip_rate_limit(ip):
        return

    student = room['students'].get(request.sid)
    sender_name = student['name'] if student else room.get('teacher_name', 'Teacher')
    is_teacher = request.sid == room.get('teacher_sid')

    message = str(data.get('message', '')).strip()[:500]
    if not message:
        return

    msg_data = {
        'sender': sender_name,
        'message': message,
        'timestamp': time.time(),
        'is_teacher': is_teacher
    }

    chat_history[room_code].append(msg_data)
    if len(chat_history[room_code]) > 500:
        chat_history[room_code] = chat_history[room_code][-500:]

    emit('chat-message', msg_data, room=room_code)


# ============================================================
# Socket.IO Events — Annotations
# ============================================================

@socketio.on('add-annotation')
@require_room_member
def handle_add_annotation(data):
    """Teacher adds an annotation/bookmark."""
    room_code = sid_to_room.get(request.sid)
    room = rooms[room_code]

    # Only teacher can annotate
    if request.sid != room.get('teacher_sid'):
        emit('error', {'message': 'Only the teacher can add annotations'})
        return

    text = str(data.get('text', '')).strip()[:500]
    annotation_type = data.get('type', 'note')
    if not text:
        return

    # Get current class average
    now = time.time()
    active_scores = [
        s['score'] for s in room['students'].values()
        if now - s['last_update'] < 10
    ]
    class_avg = sum(active_scores) / len(active_scores) if active_scores else 0

    ann_id = add_annotation(
        room['session_id'], text,
        teacher_name=room['teacher_name'],
        annotation_type=annotation_type,
        class_avg=class_avg
    )

    emit('annotation-added', {
        'id': ann_id,
        'text': text,
        'type': annotation_type,
        'timestamp': time.time(),
        'class_avg': round(class_avg * 100)
    }, room=room_code)


# ============================================================
# Socket.IO Events — Hand Raise & Reactions
# ============================================================

@socketio.on('hand-raise')
@require_room_member
def handle_hand_raise(data=None):
    """Student raises hand."""
    room_code = sid_to_room.get(request.sid)
    room = rooms[room_code]
    student = room['students'].get(request.sid)
    if not student:
        return

    emit('hand-raised', {
        'sid': request.sid,
        'name': student['name'],
        'timestamp': time.time()
    }, room=room_code)


@socketio.on('hand-lower')
@require_room_member
def handle_hand_lower(data=None):
    """Student or teacher lowers hand."""
    room_code = sid_to_room.get(request.sid)

    target_sid = request.sid
    if isinstance(data, dict) and data.get('target_sid'):
        target_sid = data['target_sid']

    emit('hand-lowered', {
        'sid': target_sid
    }, room=room_code)


@socketio.on('reaction')
@require_room_member
def handle_reaction(data):
    """Student or teacher sends a reaction emoji."""
    room_code = sid_to_room.get(request.sid)
    room = rooms[room_code]

    allowed_reactions = ['👍', '😕', '❓', '🎉', '👏', '❤️']
    emoji = data.get('emoji', '')
    if emoji not in allowed_reactions:
        return

    student = room['students'].get(request.sid)
    sender_name = student['name'] if student else room.get('teacher_name', 'Teacher')

    emit('reaction-received', {
        'sid': request.sid,
        'name': sender_name,
        'emoji': emoji,
        'timestamp': time.time()
    }, room=room_code)


# ============================================================
# Socket.IO Events — Screen Sharing
# ============================================================

@socketio.on('start-screen-share')
@require_room_member
def handle_start_screen_share(data):
    """Only the teacher or explicitly permitted students can share."""
    room_code = sid_to_room.get(request.sid)
    room = rooms[room_code]
    is_teacher = request.sid == room.get('teacher_sid')

    if not is_teacher and request.sid not in room.get('share_allowed', set()):
        emit('error', {'message': 'You do not have permission to share your screen'})
        return

    student = room['students'].get(request.sid)
    sharer_name = student['name'] if student else room.get('teacher_name', 'Teacher')

    emit('screen-share-started', {
        'sid': request.sid,
        'name': sharer_name
    }, room=room_code, skip_sid=request.sid)


@socketio.on('stop-screen-share')
@require_room_member
def handle_stop_screen_share(data=None):
    room_code = sid_to_room.get(request.sid)
    emit('screen-share-stopped', {
        'sid': request.sid
    }, room=room_code, skip_sid=request.sid)


@socketio.on('grant-screen-share')
@require_room_member
def handle_grant_screen_share(data):
    """Teacher grants screen share permission to a student."""
    room_code = sid_to_room.get(request.sid)
    room = rooms[room_code]

    if request.sid != room.get('teacher_sid'):
        emit('error', {'message': 'Only the teacher can grant screen share permission'})
        return

    target_sid = data.get('target_sid')
    if target_sid and target_sid in room['students']:
        room['share_allowed'].add(target_sid)
        emit('screen-share-granted', {
            'sid': target_sid,
            'name': room['students'][target_sid]['name']
        }, room=target_sid)
        print(f"Screen share granted to {room['students'][target_sid]['name']}")


@socketio.on('revoke-screen-share')
@require_room_member
def handle_revoke_screen_share(data):
    """Teacher revokes screen share permission from a student."""
    room_code = sid_to_room.get(request.sid)
    room = rooms[room_code]

    if request.sid != room.get('teacher_sid'):
        emit('error', {'message': 'Only the teacher can revoke screen share permission'})
        return

    target_sid = data.get('target_sid')
    if target_sid:
        room['share_allowed'].discard(target_sid)
        emit('screen-share-revoked', {
            'sid': target_sid
        }, room=target_sid)


# ============================================================
# Socket.IO Events — Attention Scores
# ============================================================

@socketio.on('attention-score')
@require_room_member
def handle_attention_score(data):
    """Receive attention score from a student via WebSocket."""
    # Per-SID rate limiting
    if not check_rate_limit(request.sid):
        return

    # Per-IP rate limiting
    ip = request.remote_addr or '127.0.0.1'
    if not check_ip_rate_limit(ip):
        return

    room_code = sid_to_room.get(request.sid)
    room = rooms[room_code]
    student = room['students'].get(request.sid)
    if not student:
        return

    score = float(data.get('attention_score', 0))
    status = data.get('status') or classify_status(score)

    student['score'] = score
    student['status'] = status
    student['last_update'] = time.time()
    student['history'].append((time.time(), score))

    if len(student['history']) > 200:
        student['history'] = student['history'][-200:]

    # Record in database
    record_attention(
        student['student_db_id'],
        room['session_id'],
        score, status,
        gaze_score=data.get('gaze_score', 0),
        head_pose_score=data.get('head_pose_score', 0),
        eye_openness=data.get('eye_openness', 0)
    )

    _update_class_history(room_code)
    _check_alert(room_code, student['name'], score)

    # Broadcast update to teacher dashboard
    emit('score-update', {
        'student_name': student['name'],
        'sid': request.sid,
        'score': round(score, 3),
        'status': status,
        'dashboard': _get_dashboard_data(room_code)
    }, room=room_code)


def _check_alert(room_code, student_name, score):
    if score >= ALERT_THRESHOLD:
        return

    key = (room_code, student_name)
    now = time.time()

    if key in alert_cooldowns and now - alert_cooldowns[key] < ALERT_COOLDOWN:
        return

    alert_cooldowns[key] = now

    room = rooms.get(room_code)
    if room:
        emit('distraction-alert', {
            'student_name': student_name,
            'score': round(score * 100, 1),
            'message': f'{student_name} needs attention ({round(score * 100)}%)'
        }, room=room['teacher_sid'])


def _update_class_history(room_code):
    room = rooms.get(room_code)
    if not room:
        return

    now = time.time()
    active_scores = [
        s['score'] for s in room['students'].values()
        if now - s['last_update'] < 10
    ]

    if active_scores:
        avg = sum(active_scores) / len(active_scores)
        room['class_history'].append((now, avg))
        if len(room['class_history']) > 500:
            room['class_history'] = room['class_history'][-500:]


# ============================================================
# Socket.IO — Disconnect Handling
# ============================================================

@socketio.on('disconnect')
def handle_disconnect():
    """Clean up connection tracking and room state."""
    ip = request.remote_addr or '127.0.0.1'
    connections_per_ip[ip].discard(request.sid)
    if not connections_per_ip[ip]:
        del connections_per_ip[ip]

    # Clean up rate limit data
    rate_limits_sid.pop(request.sid, None)

    _handle_disconnect(request.sid)


def _handle_disconnect(sid):
    room_code = sid_to_room.pop(sid, None)
    if not room_code or room_code not in rooms:
        return

    room = rooms[room_code]

    if sid == room['teacher_sid']:
        end_session(room['session_id'])
        emit('room-closed', {'message': 'Teacher ended the session'}, room=room_code)
        del rooms[room_code]
        chat_history.pop(room_code, None)
        print(f"Room {room_code} closed (teacher left)")
        return

    student = room['students'].pop(sid, None)
    if student:
        student_left(student['student_db_id'])
        leave_room(room_code)

        emit('peer-left', {
            'sid': sid,
            'name': student['name'],
            'student_count': len(room['students'])
        }, room=room_code)

        print(f"Student {student['name']} left room {room_code}")


# ============================================================
# REST API — Dashboard Data
# ============================================================

def _get_dashboard_data(room_code):
    room = rooms.get(room_code)
    if not room:
        return {}

    now = time.time()
    active_students = []
    all_scores = []

    for sid, data in room['students'].items():
        is_active = now - data['last_update'] < 10

        active_students.append({
            'name': data['name'],
            'sid': sid,
            'score': round(data['score'], 3),
            'status': data['status'],
            'active': is_active,
            'last_update': data['last_update']
        })

        if is_active:
            all_scores.append(data['score'])

    active_students.sort(key=lambda x: x['name'])

    if all_scores:
        avg_score = sum(all_scores) / len(all_scores)
        min_score = min(all_scores)
        max_score = max(all_scores)
    else:
        avg_score = min_score = max_score = 0

    session_duration = now - room['created_at']

    status_counts = {
        'focused': sum(1 for s in active_students if s['status'] == 'Focused' and s['active']),
        'partial': sum(1 for s in active_students if s['status'] == 'Partially Attentive' and s['active']),
        'distracted': sum(1 for s in active_students if s['status'] == 'Distracted' and s['active'])
    }

    return {
        'room_code': room_code,
        'class_average': round(avg_score, 3),
        'class_status': classify_status(avg_score),
        'total_students': len(active_students),
        'active_students': len(all_scores),
        'min_score': round(min_score, 3),
        'max_score': round(max_score, 3),
        'status_counts': status_counts,
        'session_duration': round(session_duration),
        'students': active_students,
        'timestamp': now
    }


@app.route('/config', methods=['GET'])
def get_config():
    """Return frontend configuration, including the student app URL."""
    from config import STUDENT_APP_PORT

    # In production (behind nginx/Caddy), the student app is on the same origin
    # at path /.  Detect this by checking the X-Forwarded-Proto header or
    # whether the request came in on the BACKEND_PORT.
    forwarded_proto = request.headers.get('X-Forwarded-Proto', '')
    is_behind_proxy  = bool(forwarded_proto)

    if is_behind_proxy:
        # Nginx/Caddy is in front — use the forwarded protocol, not request.scheme
        # (request.scheme is always 'http' on the internal connection)
        student_app_url = f"{forwarded_proto}://{request.host}"
    else:
        # Local dev — student app runs on a different port
        host_name = request.host.split(':')[0]  # strip port
        proto = 'https' if request.is_secure else 'http'
        student_app_url = f"{proto}://{host_name}:{STUDENT_APP_PORT}"

    return jsonify({
        'success': True,
        'student_app_url': student_app_url
    })


@app.route('/dashboard', methods=['GET'])
def get_dashboard():
    room_code = request.args.get('room_code') or request.args.get('meeting_id', '')
    if not room_code and rooms:
        room_code = next(iter(rooms))

    if room_code and room_code in rooms:
        data = _get_dashboard_data(room_code)
        data['success'] = True
        return jsonify(data)

    return jsonify({
        'success': True,
        'class_average': 0, 'class_status': 'Unknown',
        'total_students': 0, 'active_students': 0,
        'min_score': 0, 'max_score': 0,
        'status_counts': {'focused': 0, 'partial': 0, 'distracted': 0},
        'session_duration': 0, 'students': [], 'timestamp': time.time()
    })


@app.route('/dashboard/history', methods=['GET'])
def get_history():
    room_code = request.args.get('room_code') or request.args.get('meeting_id', '')
    limit = int(request.args.get('limit', 60))
    if not room_code and rooms:
        room_code = next(iter(rooms))

    history = []
    if room_code and room_code in rooms:
        history = rooms[room_code]['class_history'][-limit:]

    labels = []
    data = []
    for timestamp, score in history:
        dt = datetime.fromtimestamp(timestamp)
        labels.append(dt.strftime('%H:%M:%S'))
        data.append(round(score * 100, 1))

    return jsonify({'success': True, 'labels': labels, 'data': data, 'count': len(data)})


@app.route('/rooms', methods=['GET'])
def list_rooms():
    room_list = []
    for code, room in rooms.items():
        room_list.append({
            'room_code': code,
            'teacher_name': room['teacher_name'],
            'student_count': len(room['students']),
            'created_at': room['created_at']
        })
    return jsonify({'success': True, 'rooms': room_list})


# ============================================================
# REST API — Sessions
# ============================================================

@app.route('/sessions', methods=['GET'])
def list_sessions():
    past = get_past_sessions(limit=20)
    return jsonify({'success': True, 'sessions': past})


@app.route('/sessions/<int:session_id>/export', methods=['GET'])
def export_session(session_id):
    csv_data = export_session_csv(session_id)
    return csv_data, 200, {
        'Content-Type': 'text/csv',
        'Content-Disposition': f'attachment; filename=session_{session_id}.csv'
    }


@app.route('/sessions/<int:session_id>/summary', methods=['GET'])
def session_summary(session_id):
    summary = get_session_summary(session_id)
    if not summary:
        return jsonify({'error': 'Session not found'}), 404
    return jsonify({'success': True, **summary})


@app.route('/sessions/<int:session_id>/attendance', methods=['GET'])
def session_attendance(session_id):
    """Get attendance report for a session."""
    report = get_attendance_report(session_id)
    if not report:
        return jsonify({'error': 'Session not found'}), 404
    return jsonify({'success': True, **report})


@app.route('/sessions/<int:session_id>/ai-summary', methods=['GET'])
def session_ai_summary(session_id):
    """Get AI-generated session summary."""
    summary = generate_ai_summary(session_id)
    if not summary:
        return jsonify({'error': 'Session not found'}), 404
    return jsonify({'success': True, **summary})


@app.route('/sessions/<int:session_id>/annotations', methods=['GET'])
def session_annotations(session_id):
    """Get annotations for a session."""
    anns = get_annotations(session_id)
    return jsonify({'success': True, 'annotations': anns})


@app.route('/sessions/<int:session_id>/analytics', methods=['GET'])
def session_analytics(session_id):
    summary = get_session_summary(session_id)
    if not summary:
        return jsonify({'error': 'Session not found'}), 404

    timelines = {}
    with __import__('backend.database', fromlist=['get_db']).get_db() as conn:
        students = conn.execute(
            "SELECT id, name FROM students WHERE session_id = ?",
            (session_id,)
        ).fetchall()
        for student in students:
            records = get_student_timeline(student['id'], limit=500)
            timelines[student['name']] = records

    return jsonify({
        'success': True,
        'summary': summary,
        'timelines': timelines
    })


# ============================================================
# HTTP Fallback for Score Submission
# ============================================================

@app.route('/submit', methods=['POST'])
def submit_score():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    student_name = data.get('student_name', 'Anonymous')
    room_code = data.get('meeting_id', data.get('room_code', ''))
    score = float(data.get('attention_score', 0))
    status = data.get('status') or classify_status(score)

    if room_code in rooms:
        room = rooms[room_code]
        for sid, student in room['students'].items():
            if student['name'] == student_name:
                student['score'] = score
                student['status'] = status
                student['last_update'] = time.time()
                student['history'].append((time.time(), score))
                if len(student['history']) > 100:
                    student['history'] = student['history'][-100:]

                record_attention(
                    student['student_db_id'], room['session_id'],
                    score, status
                )
                _update_class_history(room_code)
                break

    return jsonify({"success": True, "message": f"Score recorded for {student_name}"})


# ============================================================
# PDF Report Generation
# ============================================================

@app.route('/sessions/<int:session_id>/report.pdf')
def download_pdf_report(session_id):
    """Generate and download a professional PDF report for a session."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                        Table, TableStyle, HRFlowable)
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
        from datetime import datetime as dt
    except ImportError:
        return jsonify({'error': 'reportlab not installed'}), 500

    summary    = get_session_summary(session_id)
    if not summary:
        return jsonify({'error': 'Session not found'}), 404

    attendance  = get_attendance_report(session_id)
    ai_summary  = generate_ai_summary(session_id)
    annotations = get_annotations(session_id)

    import io as _io
    import time as _time

    # ── Helpers ─────────────────────────────────────────────
    def fmt_ts(ts):
        return dt.fromtimestamp(ts).strftime('%I:%M %p') if ts else '—'

    def fmt_dur(secs):
        m = int(secs // 60); s = int(secs % 60)
        if m >= 60:
            h = m // 60; m = m % 60
            return f"{h}h {m}m"
        return f"{m}m {s}s"

    def grade(pct):
        if pct >= 90: return 'A+'
        if pct >= 80: return 'A'
        if pct >= 70: return 'B'
        if pct >= 60: return 'C'
        if pct >= 50: return 'D'
        return 'F'

    def score_color(pct):
        if pct >= 70: return colors.HexColor('#16a34a')   # green
        if pct >= 40: return colors.HexColor('#d97706')   # amber
        return colors.HexColor('#dc2626')                 # red

    # ── Styles ───────────────────────────────────────────────
    W, H = A4
    margin = 18 * mm
    col_w  = W - 2 * margin

    buffer = _io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=margin, rightMargin=margin,
        topMargin=margin, bottomMargin=margin
    )

    BASE   = colors.HexColor('#6366f1')   # indigo
    LIGHT  = colors.HexColor('#ede9fe')
    WHITE  = colors.white
    GREY   = colors.HexColor('#6b7280')
    DARK   = colors.HexColor('#111827')
    ROW_A  = colors.HexColor('#f5f3ff')
    ROW_B  = colors.white

    styles = getSampleStyleSheet()
    def sty(name, **kw):
        return ParagraphStyle(name, parent=styles['Normal'], **kw)

    title_sty    = sty('T', fontSize=24, textColor=WHITE,      leading=28, fontName='Helvetica-Bold', alignment=TA_CENTER)
    sub_sty      = sty('S', fontSize=11, textColor=LIGHT,      leading=16, alignment=TA_CENTER)
    h2_sty       = sty('H2', fontSize=13, textColor=DARK,      spaceBefore=10, spaceAfter=5, fontName='Helvetica-Bold')
    body_sty     = sty('B', fontSize=9,  textColor=DARK,       leading=14)
    small_sty    = sty('Sm', fontSize=8, textColor=GREY,       leading=12)
    footer_sty   = sty('F', fontSize=8, textColor=GREY,        alignment=TA_CENTER)
    center_sty   = sty('C', fontSize=9, textColor=DARK,        alignment=TA_CENTER)
    bold_sty     = sty('Bd', fontSize=9, textColor=DARK,       fontName='Helvetica-Bold')

    elements = []

    # ── HEADER BANNER ────────────────────────────────────────
    started_ts = summary.get('started_at', _time.time())
    ended_ts   = summary.get('ended_at')
    duration_s = (ended_ts or _time.time()) - started_ts
    started_fmt = dt.fromtimestamp(started_ts).strftime('%A, %B %d %Y  •  %I:%M %p')

    avg_pct = round((ai_summary.get('overall_avg', 0) if ai_summary else 0), 1)
    if avg_pct == 0 and summary.get('students'):
        avg_pct = round(sum(s['avg_score'] for s in summary['students']) /
                        max(len(summary['students']), 1) * 100, 1)

    header_data = [[
        Paragraph("📊 Gaze — Session Report", title_sty),
    ]]
    sub_line = (f"Room: {summary['room_id']}  •  Teacher: {summary['teacher_name']}  •  "
                f"{started_fmt}  •  Duration: {fmt_dur(duration_s)}")
    header_table = Table([[Paragraph("📊 Gaze — Session Report", title_sty)],
                          [Paragraph(sub_line, sub_sty)]],
                         colWidths=[col_w])
    header_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), BASE),
        ('ROUNDEDCORNERS', [6]),
        ('TOPPADDING',    (0, 0), (-1, -1), 14),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 14),
        ('LEFTPADDING',   (0, 0), (-1, -1), 16),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 16),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 6 * mm))

    # ── STATS CARDS (1 row, 4 cols) ──────────────────────────
    n_students = summary.get('student_count', 0)
    peak_pct   = ai_summary.get('peak_engagement', 0) if ai_summary else 0
    dip_count  = ai_summary.get('dip_count', 0) if ai_summary else 0

    def card(label, value, color=DARK):
        return [Paragraph(f"<b><font size=20 color='#{color.hexval()[2:]}'>{value}</font></b>", center_sty),
                Paragraph(f"<font size=8 color='#6b7280'>{label}</font>", center_sty)]

    cw = col_w / 4 - 2
    cards_table = Table([
        [card("Students", str(n_students)),
         card("Class Average", f"{avg_pct}%", score_color(avg_pct)),
         card("Peak Attention", f"{peak_pct}%", score_color(peak_pct)),
         card("Attention Dips", str(dip_count))]
    ], colWidths=[cw, cw, cw, cw], rowHeights=[40])
    cards_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), ROW_A),
        ('BOX',        (0, 0), (-1, -1), 0.5, colors.HexColor('#d1d5db')),
        ('INNERGRID',  (0, 0), (-1, -1), 0.5, colors.HexColor('#d1d5db')),
        ('VALIGN',     (0, 0), (-1, -1), 'MIDDLE'),
        ('ROUNDEDCORNERS', [4]),
        ('TOPPADDING',    (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(cards_table)
    elements.append(Spacer(1, 6 * mm))

    # ── STUDENT PERFORMANCE TABLE ─────────────────────────────
    elements.append(Paragraph("Student Performance", h2_sty))
    elements.append(HRFlowable(width=col_w, thickness=1, color=BASE, spaceAfter=4))

    if attendance and attendance.get('students'):
        hdr = ['Student Name', 'Time in Session', 'Avg Attention', 'Grade',
               'Focused', 'Partial', 'Distracted', 'Status']
        rows = [hdr]

        for s in attendance['students']:
            s_avg = s.get('avg_attention', 0)
            # Get focused/partial/distracted from summary students list
            s_match = next((x for x in summary.get('students', []) if x['name'] == s['name']), None)
            rows.append([
                s['name'],
                s.get('duration_formatted', '—'),
                f"{s_avg}%",
                grade(s_avg),
                '—', '—', '—',   # placeholder — we don't have breakdown in attendance
                '✓ Present' if s.get('was_present_at_end') else '✗ Left early'
            ])

        cws = [120, 70, 65, 40, 45, 45, 55, 65]
        st = Table(rows, colWidths=cws)
        ts = TableStyle([
            ('BACKGROUND',  (0, 0), (-1, 0), BASE),
            ('TEXTCOLOR',   (0, 0), (-1, 0), WHITE),
            ('FONTNAME',    (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',    (0, 0), (-1, -1), 8),
            ('ALIGN',       (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN',       (0, 0), (0, -1),  'LEFT'),
            ('VALIGN',      (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID',        (0, 0), (-1, -1), 0.4, colors.HexColor('#d1d5db')),
            ('TOPPADDING',  (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING',(0, 0), (-1, -1), 5),
        ])
        # Alternating row colours
        for i, row in enumerate(rows[1:], 1):
            bg = ROW_A if i % 2 == 0 else ROW_B
            ts.add('BACKGROUND', (0, i), (-1, i), bg)
            # Colour the grade cell
            s_avg = attendance['students'][i-1].get('avg_attention', 0)
            ts.add('TEXTCOLOR', (3, i), (3, i), score_color(s_avg))
            ts.add('FONTNAME',  (3, i), (3, i), 'Helvetica-Bold')
        st.setStyle(ts)
        elements.append(st)
    else:
        elements.append(Paragraph("No student data available.", body_sty))

    elements.append(Spacer(1, 6 * mm))

    # ── AI / RULE-BASED SUMMARY ───────────────────────────────
    if ai_summary:
        powered = "AI-Powered Analysis" if ai_summary.get('ai_powered') else "Session Analysis"
        elements.append(Paragraph(powered, h2_sty))
        elements.append(HRFlowable(width=col_w, thickness=1, color=BASE, spaceAfter=4))

        summary_text = ai_summary.get('summary', '')
        if summary_text:
            elements.append(Paragraph(summary_text, body_sty))
            elements.append(Spacer(1, 4 * mm))

        highlights = ai_summary.get('highlights', [])
        if highlights:
            elements.append(Paragraph("<b>Key Observations:</b>", body_sty))
            for h in highlights[:6]:
                elements.append(Paragraph(f"  • {h}", body_sty))
            elements.append(Spacer(1, 3 * mm))

        recs = ai_summary.get('recommendations', [])
        if recs:
            elements.append(Paragraph("<b>Recommendations:</b>", body_sty))
            for r in recs:
                elements.append(Paragraph(f"  • {r}", body_sty))
        elements.append(Spacer(1, 5 * mm))

    # ── TEACHER ANNOTATIONS ───────────────────────────────────
    if annotations:
        elements.append(Paragraph("Teacher Annotations", h2_sty))
        elements.append(HRFlowable(width=col_w, thickness=1, color=BASE, spaceAfter=4))

        ann_hdr = ['Time', 'Type', 'Note', 'Class Attention']
        ann_rows = [ann_hdr]
        for ann in annotations:
            ann_rows.append([
                dt.fromtimestamp(ann['timestamp']).strftime('%I:%M %p'),
                ann.get('annotation_type', 'note').capitalize(),
                ann.get('text', ''),
                f"{round(ann.get('class_avg_at_time', 0) * 100, 1)}%"
            ])

        ann_table = Table(ann_rows, colWidths=[55, 55, 320, 75])
        ann_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), BASE),
            ('TEXTCOLOR',  (0, 0), (-1, 0), WHITE),
            ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',   (0, 0), (-1, -1), 8),
            ('GRID',       (0, 0), (-1, -1), 0.4, colors.HexColor('#d1d5db')),
            ('VALIGN',     (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING',(0, 0), (-1, -1), 5),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [ROW_A, ROW_B]),
        ]))
        elements.append(ann_table)
        elements.append(Spacer(1, 5 * mm))

    # ── FOOTER ────────────────────────────────────────────────
    elements.append(HRFlowable(width=col_w, thickness=0.5, color=colors.HexColor('#d1d5db')))
    elements.append(Spacer(1, 3 * mm))
    elements.append(Paragraph(
        f"Generated by Gaze — Real-Time Attention Monitoring System  •  "
        f"{dt.now().strftime('%Y-%m-%d %H:%M')}",
        footer_sty
    ))

    doc.build(elements)
    buffer.seek(0)

    room = summary.get('room_id', session_id)
    return send_file(
        buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f"gaze_report_{room}_{dt.now().strftime('%Y%m%d')}.pdf"
    )



# ============================================================
# API Documentation Page
# ============================================================

@app.route('/api/docs')
def api_docs():
    """Interactive API documentation page."""
    return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gaze API Documentation</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        *{box-sizing:border-box;margin:0;padding:0}
        body{font-family:'Inter',sans-serif;background:#0a0a14;color:#f0f0ff;padding:32px;max-width:900px;margin:0 auto}
        h1{font-size:28px;margin-bottom:8px;display:flex;align-items:center;gap:10px}
        h1 span{font-size:14px;background:linear-gradient(135deg,#6366f1,#818cf8);padding:4px 12px;border-radius:20px}
        .subtitle{color:#8888aa;font-size:14px;margin-bottom:32px}
        h2{font-size:18px;margin:28px 0 14px;padding-bottom:8px;border-bottom:1px solid rgba(255,255,255,0.06)}
        .endpoint{background:#12121f;border:1px solid rgba(255,255,255,0.06);border-radius:10px;margin-bottom:10px;overflow:hidden}
        .ep-header{display:flex;align-items:center;gap:10px;padding:14px 18px;cursor:pointer}
        .ep-header:hover{background:rgba(99,102,241,0.05)}
        .method{padding:3px 10px;border-radius:4px;font-size:11px;font-weight:700;text-transform:uppercase;min-width:50px;text-align:center}
        .GET{background:rgba(34,197,94,0.15);color:#22c55e}
        .POST{background:rgba(99,102,241,0.15);color:#818cf8}
        .path{font-family:monospace;font-size:13px;color:#f0f0ff}
        .desc{color:#8888aa;font-size:12px;margin-left:auto}
        .ep-body{display:none;padding:14px 18px;border-top:1px solid rgba(255,255,255,0.06);font-size:13px;color:#aaa}
        .ep-body.open{display:block}
        code{background:rgba(99,102,241,0.1);padding:2px 6px;border-radius:4px;font-size:12px}
        .socket-event{display:flex;align-items:center;gap:10px;padding:10px 18px;background:#12121f;border:1px solid rgba(255,255,255,0.06);border-radius:8px;margin-bottom:6px;font-size:13px}
        .direction{font-size:11px;padding:2px 8px;border-radius:4px;font-weight:600}
        .client-server{background:rgba(245,158,11,0.15);color:#f59e0b}
        .server-client{background:rgba(34,197,94,0.15);color:#22c55e}
        .event-name{font-family:monospace;font-weight:600}
        .badge{display:inline-block;font-size:10px;padding:2px 8px;border-radius:10px;background:rgba(99,102,241,0.15);color:#818cf8;margin-left:6px}
    </style>
</head>
<body>
    <h1>📡 Gaze API Documentation <span>v2.0</span></h1>
    <p class="subtitle">REST endpoints and WebSocket events for the Gaze Attention Monitoring System</p>

    <h2>🌐 REST Endpoints</h2>

    <div class="endpoint" onclick="this.querySelector('.ep-body').classList.toggle('open')">
        <div class="ep-header"><span class="method GET">GET</span><span class="path">/</span><span class="desc">Teacher dashboard UI</span></div>
        <div class="ep-body">Returns the teacher dashboard HTML page. Requires authentication via the login form.</div>
    </div>
    <div class="endpoint" onclick="this.querySelector('.ep-body').classList.toggle('open')">
        <div class="ep-header"><span class="method GET">GET</span><span class="path">/api/dashboard</span><span class="desc">Live dashboard data</span></div>
        <div class="ep-body">Returns JSON with active rooms, student scores, and class averages. Used by the dashboard for real-time updates.<br><br><b>Response:</b> <code>{ rooms: [...], students: [...], class_avg: 0.85 }</code></div>
    </div>
    <div class="endpoint" onclick="this.querySelector('.ep-body').classList.toggle('open')">
        <div class="ep-header"><span class="method GET">GET</span><span class="path">/api/rooms</span><span class="desc">List active rooms</span></div>
        <div class="ep-body">Returns all currently active room codes and their student counts.<br><br><b>Response:</b> <code>{ rooms: [{ code: "ABC123", students: 5 }] }</code></div>
    </div>
    <div class="endpoint" onclick="this.querySelector('.ep-body').classList.toggle('open')">
        <div class="ep-header"><span class="method GET">GET</span><span class="path">/api/sessions</span><span class="desc">Past sessions</span></div>
        <div class="ep-body">Returns list of all completed sessions with summary data including student count and duration.</div>
    </div>
    <div class="endpoint" onclick="this.querySelector('.ep-body').classList.toggle('open')">
        <div class="ep-header"><span class="method GET">GET</span><span class="path">/sessions/&lt;id&gt;/summary</span><span class="desc">Session summary</span></div>
        <div class="ep-body">Detailed summary of a specific session including per-student averages and class statistics.</div>
    </div>
    <div class="endpoint" onclick="this.querySelector('.ep-body').classList.toggle('open')">
        <div class="ep-header"><span class="method GET">GET</span><span class="path">/sessions/&lt;id&gt;/attendance</span><span class="desc">Attendance report</span></div>
        <div class="ep-body">Student join/leave times, duration in session, and whether they were present at session end.</div>
    </div>
    <div class="endpoint" onclick="this.querySelector('.ep-body').classList.toggle('open')">
        <div class="ep-header"><span class="method GET">GET</span><span class="path">/sessions/&lt;id&gt;/ai-summary</span><span class="desc">AI session analysis</span></div>
        <div class="ep-body">AI-powered session summary using Google Gemini (with rule-based fallback). Includes highlights, recommendations, and engagement insights.<br><br><b>Response includes:</b> <code>ai_powered: true/false</code> flag.</div>
    </div>
    <div class="endpoint" onclick="this.querySelector('.ep-body').classList.toggle('open')">
        <div class="ep-header"><span class="method GET">GET</span><span class="path">/sessions/&lt;id&gt;/annotations</span><span class="desc">Session annotations</span></div>
        <div class="ep-body">All teacher annotations/bookmarks for a session with timestamps and types.</div>
    </div>
    <div class="endpoint" onclick="this.querySelector('.ep-body').classList.toggle('open')">
        <div class="ep-header"><span class="method GET">GET</span><span class="path">/sessions/&lt;id&gt;/report.pdf</span><span class="desc">Download PDF report</span><span class="badge">NEW</span></div>
        <div class="ep-body">Downloads a professionally formatted PDF report with session overview, student performance table, AI analysis, and teacher annotations.</div>
    </div>
    <div class="endpoint" onclick="this.querySelector('.ep-body').classList.toggle('open')">
        <div class="ep-header"><span class="method POST">POST</span><span class="path">/api/submit-score</span><span class="desc">Submit attention score</span></div>
        <div class="ep-body"><b>Body:</b> <code>{ student_name: "Alice", room_code: "ABC123", score: 0.85, status: "focused" }</code><br><br>Submits an attention score for a student. Used as HTTP fallback when WebSocket is unavailable.</div>
    </div>

    <h2>🔌 WebSocket Events (Socket.IO)</h2>

    <div class="socket-event"><span class="direction client-server">C → S</span><span class="event-name">create-room</span>Teacher creates a classroom</div>
    <div class="socket-event"><span class="direction client-server">C → S</span><span class="event-name">join-room</span>Student joins with name + room code</div>
    <div class="socket-event"><span class="direction server-client">S → C</span><span class="event-name">room-created</span>Room code sent back to teacher</div>
    <div class="socket-event"><span class="direction server-client">S → C</span><span class="event-name">student-joined</span>Notify teacher of new student</div>
    <div class="socket-event"><span class="direction client-server">C → S</span><span class="event-name">attention-score</span>Student sends attention score</div>
    <div class="socket-event"><span class="direction server-client">S → C</span><span class="event-name">score-update</span>Broadcast score to teacher dashboard</div>
    <div class="socket-event"><span class="direction server-client">S → C</span><span class="event-name">distraction-alert</span>Alert: student distracted &gt;30s</div>
    <div class="socket-event"><span class="direction client-server">C → S</span><span class="event-name">hand-raise</span>Student raises hand <span class="badge">NEW</span></div>
    <div class="socket-event"><span class="direction client-server">C → S</span><span class="event-name">hand-lower</span>Student/teacher lowers hand <span class="badge">NEW</span></div>
    <div class="socket-event"><span class="direction client-server">C → S</span><span class="event-name">reaction</span>Student sends emoji reaction <span class="badge">NEW</span></div>
    <div class="socket-event"><span class="direction server-client">S → C</span><span class="event-name">reaction-received</span>Broadcast reaction to room</div>
    <div class="socket-event"><span class="direction client-server">C → S</span><span class="event-name">add-annotation</span>Teacher bookmarks a moment</div>
    <div class="socket-event"><span class="direction client-server">C → S</span><span class="event-name">send-message</span>Chat message</div>
    <div class="socket-event"><span class="direction client-server">C → S</span><span class="event-name">offer</span>WebRTC SDP offer</div>
    <div class="socket-event"><span class="direction client-server">C → S</span><span class="event-name">answer</span>WebRTC SDP answer</div>
    <div class="socket-event"><span class="direction client-server">C → S</span><span class="event-name">ice-candidate</span>WebRTC ICE candidate</div>
</body>
</html>'''


# ============================================================
# Teacher Dashboard UI
# ============================================================

@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')




@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory(app.static_folder, filename)


# ============================================================
# Server Entry Point
# ============================================================

def run_server(host='127.0.0.1', port=None, debug=None, ssl_context=None):
    """Run the Flask-SocketIO server."""
    port = port or BACKEND_PORT
    debug = debug if debug is not None else DEBUG
    protocol = 'https' if ssl_context else 'http'

    print(f"╔═══════════════════════════════════════════════════╗")
    print(f"║   Gaze — Backend & Teacher Dashboard              ║")
    print(f"╠═══════════════════════════════════════════════════╣")
    print(f"║   Dashboard: {protocol}://{host}:{port}                 ║")
    print(f"║   WebSocket:  {'wss' if ssl_context else 'ws'}://{host}:{port}                  ║")
    print(f"╚═══════════════════════════════════════════════════╝")

    kwargs = dict(host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)
    if ssl_context:
        kwargs['certfile'] = ssl_context[0]
        kwargs['keyfile'] = ssl_context[1]

    socketio.run(app, **kwargs)


if __name__ == '__main__':
    run_server()
