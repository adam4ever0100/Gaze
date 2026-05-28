"""
Dual-Mode Database Module — PostgreSQL (production) / SQLite (development).

Provides persistent storage for sessions, attention records, students,
teacher annotations, and attendance data.

Key scaling features:
- Automatic PostgreSQL / SQLite selection based on DATABASE_URL env var.
- Write buffer for attention records — bulk inserts reduce DB writes by ~90%.
- Thread-safe connection pooling for PostgreSQL.
"""

import os
import csv
import io
import time
import threading
import atexit
from datetime import datetime
from contextlib import contextmanager

# ============================================================
# Database Backend Selection
# ============================================================

# Import config values
from config import DATABASE_URL, DB_WRITE_BUFFER_SIZE, DB_WRITE_FLUSH_INTERVAL

_USE_POSTGRES = bool(DATABASE_URL)

if _USE_POSTGRES:
    import psycopg2
    import psycopg2.pool
    import psycopg2.extras
else:
    import sqlite3

# Placeholder style: PostgreSQL uses %s, SQLite uses ?
_PH = '%s' if _USE_POSTGRES else '?'

# SQLite database file location (only used when not using PostgreSQL)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'gaze.db')

# PostgreSQL connection pool (created on init)
_pg_pool = None


def _init_pg_pool():
    """Create a threaded PostgreSQL connection pool."""
    global _pg_pool
    if _pg_pool is None:
        _pg_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=20,
            dsn=DATABASE_URL
        )


@contextmanager
def get_db():
    """Context manager for database connections.

    - PostgreSQL: borrows from connection pool, auto-commits on success.
    - SQLite: opens/closes a connection per call with WAL journal mode.
    """
    if _USE_POSTGRES:
        if _pg_pool is None:
            _init_pg_pool()
        conn = _pg_pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            _pg_pool.putconn(conn)
    else:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _fetchone(conn, sql, params=()):
    """Execute and fetchone — returns dict for both backends."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) if _USE_POSTGRES else conn.cursor()
    cur.execute(sql, params)
    row = cur.fetchone()
    cur.close()
    if row is None:
        return None
    return dict(row)


def _fetchall(conn, sql, params=()):
    """Execute and fetchall — returns list of dicts for both backends."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) if _USE_POSTGRES else conn.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    return [dict(r) for r in rows]


def _execute(conn, sql, params=()):
    """Execute a write statement. Returns the cursor."""
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur


def _last_insert_id(conn, cur):
    """Get the last inserted row ID (differs between backends)."""
    if _USE_POSTGRES:
        return cur.fetchone()[0]
    else:
        return cur.lastrowid


# ============================================================
# Schema Initialization
# ============================================================

def init_db():
    """Initialize the database schema."""
    if _USE_POSTGRES:
        _init_pg_pool()

    with get_db() as conn:
        if _USE_POSTGRES:
            conn.cursor().execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id SERIAL PRIMARY KEY,
                    room_id TEXT NOT NULL,
                    teacher_name TEXT DEFAULT '',
                    session_name TEXT DEFAULT '',
                    started_at DOUBLE PRECISION NOT NULL,
                    ended_at DOUBLE PRECISION,
                    is_active INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS students (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    session_id INTEGER NOT NULL REFERENCES sessions(id),
                    joined_at DOUBLE PRECISION NOT NULL,
                    left_at DOUBLE PRECISION
                );

                CREATE TABLE IF NOT EXISTS attention_records (
                    id SERIAL PRIMARY KEY,
                    student_id INTEGER NOT NULL REFERENCES students(id),
                    session_id INTEGER NOT NULL REFERENCES sessions(id),
                    attention_score DOUBLE PRECISION NOT NULL,
                    status TEXT NOT NULL,
                    gaze_score DOUBLE PRECISION DEFAULT 0,
                    head_pose_score DOUBLE PRECISION DEFAULT 0,
                    eye_openness DOUBLE PRECISION DEFAULT 0,
                    timestamp DOUBLE PRECISION NOT NULL
                );

                CREATE TABLE IF NOT EXISTS annotations (
                    id SERIAL PRIMARY KEY,
                    session_id INTEGER NOT NULL REFERENCES sessions(id),
                    teacher_name TEXT DEFAULT '',
                    text TEXT NOT NULL,
                    annotation_type TEXT DEFAULT 'note',
                    timestamp DOUBLE PRECISION NOT NULL,
                    class_avg_at_time DOUBLE PRECISION DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_attention_session ON attention_records(session_id);
                CREATE INDEX IF NOT EXISTS idx_attention_student ON attention_records(student_id);
                CREATE INDEX IF NOT EXISTS idx_students_session ON students(session_id);
                CREATE INDEX IF NOT EXISTS idx_annotations_session ON annotations(session_id);
            """)
        else:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id TEXT NOT NULL,
                    teacher_name TEXT DEFAULT '',
                    session_name TEXT DEFAULT '',
                    started_at REAL NOT NULL,
                    ended_at REAL,
                    is_active INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS students (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    session_id INTEGER NOT NULL,
                    joined_at REAL NOT NULL,
                    left_at REAL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );

                CREATE TABLE IF NOT EXISTS attention_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL,
                    session_id INTEGER NOT NULL,
                    attention_score REAL NOT NULL,
                    status TEXT NOT NULL,
                    gaze_score REAL DEFAULT 0,
                    head_pose_score REAL DEFAULT 0,
                    eye_openness REAL DEFAULT 0,
                    timestamp REAL NOT NULL,
                    FOREIGN KEY (student_id) REFERENCES students(id),
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );

                CREATE TABLE IF NOT EXISTS annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    teacher_name TEXT DEFAULT '',
                    text TEXT NOT NULL,
                    annotation_type TEXT DEFAULT 'note',
                    timestamp REAL NOT NULL,
                    class_avg_at_time REAL DEFAULT 0,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );

                CREATE INDEX IF NOT EXISTS idx_attention_session ON attention_records(session_id);
                CREATE INDEX IF NOT EXISTS idx_attention_student ON attention_records(student_id);
                CREATE INDEX IF NOT EXISTS idx_students_session ON students(session_id);
                CREATE INDEX IF NOT EXISTS idx_annotations_session ON annotations(session_id);
            """)


# ============================================================
# Write Buffer for Attention Records
# ============================================================
# Instead of writing each attention score to the DB immediately (which
# causes SQLite lock contention and PostgreSQL connection churn at scale),
# we buffer records in memory and flush them in bulk periodically.

_attention_buffer = []
_buffer_lock = threading.Lock()
_flush_timer = None


def _start_flush_timer():
    """Start the periodic flush timer (runs every DB_WRITE_FLUSH_INTERVAL seconds)."""
    global _flush_timer
    if _flush_timer is not None:
        return  # Already running
    _flush_timer = threading.Timer(DB_WRITE_FLUSH_INTERVAL, _periodic_flush)
    _flush_timer.daemon = True
    _flush_timer.start()


def _periodic_flush():
    """Called by the timer thread — flush and reschedule."""
    global _flush_timer
    _flush_timer = None
    flush_attention_buffer()
    _start_flush_timer()


def flush_attention_buffer():
    """Flush all buffered attention records to the database in a single transaction."""
    global _attention_buffer
    with _buffer_lock:
        if not _attention_buffer:
            return
        batch = _attention_buffer[:]
        _attention_buffer = []

    if not batch:
        return

    try:
        with get_db() as conn:
            if _USE_POSTGRES:
                cur = conn.cursor()
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO attention_records
                       (student_id, session_id, attention_score, status,
                        gaze_score, head_pose_score, eye_openness, timestamp)
                       VALUES %s""",
                    batch,
                    template="(%s, %s, %s, %s, %s, %s, %s, %s)"
                )
                cur.close()
            else:
                conn.executemany(
                    """INSERT INTO attention_records
                       (student_id, session_id, attention_score, status,
                        gaze_score, head_pose_score, eye_openness, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    batch
                )
    except Exception as e:
        print(f"[DB] Error flushing attention buffer ({len(batch)} records): {e}")
        # Put records back so they aren't lost
        with _buffer_lock:
            _attention_buffer = batch + _attention_buffer


# Ensure buffer is flushed on shutdown
atexit.register(flush_attention_buffer)


# ============================================================
# Session CRUD
# ============================================================

def create_session(room_id, teacher_name="", session_name=""):
    """Create a new session and return its ID."""
    with get_db() as conn:
        if _USE_POSTGRES:
            cur = _execute(conn,
                f"INSERT INTO sessions (room_id, teacher_name, session_name, started_at) VALUES (%s, %s, %s, %s) RETURNING id",
                (room_id, teacher_name, session_name, time.time())
            )
            return cur.fetchone()[0]
        else:
            # Add session_name column if it doesn't exist yet (migration-safe)
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN session_name TEXT DEFAULT ''")
            except Exception:
                pass  # Column already exists
            cur = conn.execute(
                "INSERT INTO sessions (room_id, teacher_name, session_name, started_at) VALUES (?, ?, ?, ?)",
                (room_id, teacher_name, session_name, time.time())
            )
            return cur.lastrowid


def end_session(session_id):
    """Mark a session as ended."""
    with get_db() as conn:
        # Flush any remaining buffered records for this session first
        flush_attention_buffer()
        _execute(conn,
            f"UPDATE sessions SET ended_at = {_PH}, is_active = 0 WHERE id = {_PH}",
            (time.time(), session_id)
        )


# ============================================================
# Student CRUD
# ============================================================

def add_student(session_id, name):
    """Add a student to a session, or re-activate them if they're reconnecting.

    If a student with the same name already exists for this session (left_at set),
    we reuse their record so the attendance report stays clean.
    """
    with get_db() as conn:
        # Check for a previous record of the same student (name match)
        existing = _fetchone(conn,
            f"SELECT id FROM students WHERE session_id = {_PH} AND name = {_PH} ORDER BY joined_at DESC LIMIT 1",
            (session_id, name)
        )

        if existing:
            # Re-activate: clear left_at and update joined_at to now
            _execute(conn,
                f"UPDATE students SET joined_at = {_PH}, left_at = NULL WHERE id = {_PH}",
                (time.time(), existing['id'])
            )
            return existing['id']

        # New student
        if _USE_POSTGRES:
            cur = _execute(conn,
                "INSERT INTO students (name, session_id, joined_at) VALUES (%s, %s, %s) RETURNING id",
                (name, session_id, time.time())
            )
            return cur.fetchone()[0]
        else:
            cur = conn.execute(
                "INSERT INTO students (name, session_id, joined_at) VALUES (?, ?, ?)",
                (name, session_id, time.time())
            )
            return cur.lastrowid


def student_left(student_id):
    """Mark a student as having left the session."""
    with get_db() as conn:
        _execute(conn,
            f"UPDATE students SET left_at = {_PH} WHERE id = {_PH}",
            (time.time(), student_id)
        )


# ============================================================
# Attention Records (Buffered)
# ============================================================

def record_attention(student_id, session_id, score, status,
                     gaze_score=0, head_pose_score=0, eye_openness=0):
    """Buffer an attention data point for bulk insert.

    Records are written to the database in bulk by the flush timer,
    not immediately — this reduces DB write load by ~90%.
    """
    record = (student_id, session_id, score, status,
              gaze_score, head_pose_score, eye_openness, time.time())

    with _buffer_lock:
        _attention_buffer.append(record)
        buffer_size = len(_attention_buffer)

    # Start the flush timer if not already running
    _start_flush_timer()

    # Flush immediately if buffer is full
    if buffer_size >= DB_WRITE_BUFFER_SIZE:
        flush_attention_buffer()


# ============================================================
# Annotations
# ============================================================

def add_annotation(session_id, text, teacher_name="", annotation_type="note", class_avg=0):
    """Add a teacher annotation to a session."""
    with get_db() as conn:
        if _USE_POSTGRES:
            cur = _execute(conn,
                """INSERT INTO annotations (session_id, teacher_name, text, annotation_type, timestamp, class_avg_at_time)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (session_id, teacher_name, text, annotation_type, time.time(), class_avg)
            )
            return cur.fetchone()[0]
        else:
            cur = conn.execute(
                """INSERT INTO annotations (session_id, teacher_name, text, annotation_type, timestamp, class_avg_at_time)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (session_id, teacher_name, text, annotation_type, time.time(), class_avg)
            )
            return cur.lastrowid


def get_annotations(session_id):
    """Get all annotations for a session."""
    with get_db() as conn:
        return _fetchall(conn,
            f"SELECT * FROM annotations WHERE session_id = {_PH} ORDER BY timestamp ASC",
            (session_id,)
        )


def delete_annotation(annotation_id):
    """Delete an annotation."""
    with get_db() as conn:
        _execute(conn, f"DELETE FROM annotations WHERE id = {_PH}", (annotation_id,))


# ============================================================
# Attendance Reports
# ============================================================

def get_attendance_report(session_id):
    """Get attendance report for a session — join/leave times, durations."""
    with get_db() as conn:
        session = _fetchone(conn,
            f"SELECT * FROM sessions WHERE id = {_PH}", (session_id,)
        )
        if not session:
            return None

        students = _fetchall(conn,
            f"SELECT * FROM students WHERE session_id = {_PH} ORDER BY joined_at ASC",
            (session_id,)
        )

        session_end = session['ended_at'] or time.time()
        report = []
        for s in students:
            left = s['left_at'] or session_end
            duration = left - s['joined_at']

            # Get average attention for this student
            avg = _fetchone(conn,
                f"SELECT AVG(attention_score) as avg_score FROM attention_records WHERE student_id = {_PH}",
                (s['id'],)
            )

            report.append({
                'name': s['name'],
                'joined_at': s['joined_at'],
                'left_at': s['left_at'],
                'duration_seconds': round(duration),
                'duration_formatted': _format_duration(duration),
                'avg_attention': round((avg['avg_score'] or 0) * 100, 1),
                'was_present_at_end': s['left_at'] is None or s['left_at'] >= session_end - 5
            })

        return {
            'session_id': session_id,
            'room_id': session['room_id'],
            'teacher_name': session['teacher_name'],
            'started_at': session['started_at'],
            'ended_at': session['ended_at'],
            'total_students': len(students),
            'students': report
        }


# ============================================================
# AI Session Summary
# ============================================================

def generate_ai_summary(session_id):
    """Generate an AI-powered session summary using Gemini (with rule-based fallback)."""
    # Flush buffer so summary has the latest data
    flush_attention_buffer()

    with get_db() as conn:
        session = _fetchone(conn,
            f"SELECT * FROM sessions WHERE id = {_PH}", (session_id,)
        )
        if not session:
            return None

        students = _fetchall(conn,
            f"SELECT * FROM students WHERE session_id = {_PH}", (session_id,)
        )

        records = _fetchall(conn,
            f"""SELECT attention_score, status, timestamp FROM attention_records
               WHERE session_id = {_PH} ORDER BY timestamp ASC""",
            (session_id,)
        )

        annotations = get_annotations(session_id)

        if not records:
            return {
                'session_id': session_id,
                'summary': 'No attention data recorded for this session.',
                'highlights': [],
                'recommendations': [],
                'ai_powered': False
            }

        # Calculate key metrics
        scores = [r['attention_score'] for r in records]
        overall_avg = sum(scores) / len(scores)
        session_start = session['started_at']
        session_end = session['ended_at'] or time.time()
        duration_min = (session_end - session_start) / 60

        # Find attention dips
        dips = []
        window_size = max(5, len(records) // 20)
        for i in range(0, len(records) - window_size, window_size):
            window = records[i:i + window_size]
            window_avg = sum(r['attention_score'] for r in window) / len(window)
            if window_avg < 0.4:
                elapsed = (window[0]['timestamp'] - session_start) / 60
                dips.append(f"Attention dipped to {round(window_avg * 100)}% at {round(elapsed)} min")

        # Find peak engagement
        peak_avg = 0
        peak_time = 0
        for i in range(0, len(records) - window_size, window_size):
            window = records[i:i + window_size]
            window_avg = sum(r['attention_score'] for r in window) / len(window)
            if window_avg > peak_avg:
                peak_avg = window_avg
                peak_time = (window[0]['timestamp'] - session_start) / 60

        # Per-student analysis
        student_insights = []
        for student in students:
            s_records = _fetchone(conn,
                f"""SELECT AVG(attention_score) as avg, MIN(attention_score) as min_s,
                          MAX(attention_score) as max_s, COUNT(*) as cnt
                   FROM attention_records WHERE student_id = {_PH}""",
                (student['id'],)
            )
            if s_records and s_records['cnt'] and s_records['cnt'] > 0:
                avg = s_records['avg'] or 0
                if avg < 0.4:
                    student_insights.append(f"{student['name']} had low engagement ({round(avg * 100)}% avg)")
                elif avg >= 0.8:
                    student_insights.append(f"{student['name']} was highly engaged ({round(avg * 100)}% avg)")

        highlights = dips + student_insights

        # Try Gemini AI summary
        from config import GEMINI_API_KEY
        gemini_summary = None
        if GEMINI_API_KEY:
            gemini_summary = _generate_gemini_summary(
                duration_min, len(students), overall_avg, peak_avg, peak_time,
                dips, student_insights, annotations
            )

        if gemini_summary:
            return {
                'session_id': session_id,
                'summary': gemini_summary,
                'overall_avg': round(overall_avg * 100, 1),
                'duration_minutes': round(duration_min),
                'student_count': len(students),
                'peak_engagement': round(peak_avg * 100, 1),
                'peak_time_minutes': round(peak_time),
                'dip_count': len(dips),
                'highlights': highlights,
                'recommendations': [],  # included in gemini summary
                'annotation_count': len(annotations),
                'ai_powered': True
            }

        # Fallback: rule-based summary
        summary_parts = [
            f"Session lasted {round(duration_min)} minutes with {len(students)} student(s).",
            f"Overall class attention: {round(overall_avg * 100)}%.",
        ]
        if peak_avg > 0:
            summary_parts.append(f"Peak engagement ({round(peak_avg * 100)}%) at ~{round(peak_time)} min.")
        if dips:
            summary_parts.append(f"Found {len(dips)} attention dip(s) during the session.")

        recommendations = []
        if overall_avg < 0.5:
            recommendations.append("Consider shorter sessions or more interactive activities.")
        if len(dips) >= 3:
            recommendations.append("Multiple attention dips detected — try breaking content into smaller segments.")
        if overall_avg >= 0.7:
            recommendations.append("Great session! Class engagement was strong overall.")
        if len(annotations) > 0:
            recommendations.append(f"Teacher made {len(annotations)} annotation(s) during the session.")

        return {
            'session_id': session_id,
            'summary': ' '.join(summary_parts),
            'overall_avg': round(overall_avg * 100, 1),
            'duration_minutes': round(duration_min),
            'student_count': len(students),
            'peak_engagement': round(peak_avg * 100, 1),
            'peak_time_minutes': round(peak_time),
            'dip_count': len(dips),
            'highlights': highlights,
            'recommendations': recommendations,
            'annotation_count': len(annotations),
            'ai_powered': False
        }


def _generate_gemini_summary(duration_min, student_count, overall_avg, peak_avg, peak_time,
                              dips, student_insights, annotations):
    """Call Google Gemini to generate a rich summary. Returns string or None."""
    try:
        import google.generativeai as genai
        from config import GEMINI_API_KEY

        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-2.0-flash')

        prompt = f"""You are an educational analytics assistant for a classroom attention monitoring system called Gaze.
Generate a concise, professional session summary (3-5 paragraphs) based on this data:

SESSION DATA:
- Duration: {round(duration_min)} minutes
- Students: {student_count}
- Overall attention: {round(overall_avg * 100)}%
- Peak engagement: {round(peak_avg * 100)}% at minute {round(peak_time)}
- Attention dips: {len(dips)} ({'; '.join(dips[:5]) if dips else 'none'})
- Student highlights: {'; '.join(student_insights[:5]) if student_insights else 'none'}
- Teacher annotations: {len(annotations)}

Include:
1. An executive summary paragraph
2. Key observations about engagement patterns
3. Specific, actionable recommendations for improvement
4. A positive note about strengths

Keep it concise, professional, and teacher-friendly. Do not use markdown formatting."""

        response = model.generate_content(prompt)
        return response.text.strip() if response.text else None
    except Exception as e:
        print(f"[Gemini AI] Error generating summary: {e}")
        return None


# ============================================================
# Session Queries
# ============================================================

def get_session_summary(session_id):
    """Get a summary of a session."""
    with get_db() as conn:
        session = _fetchone(conn,
            f"SELECT * FROM sessions WHERE id = {_PH}", (session_id,)
        )

        if not session:
            return None

        students = _fetchall(conn,
            f"SELECT * FROM students WHERE session_id = {_PH}", (session_id,)
        )

        # Get per-student averages
        student_stats = []
        for student in students:
            avg = _fetchone(conn,
                f"""SELECT AVG(attention_score) as avg_score,
                          MIN(attention_score) as min_score,
                          MAX(attention_score) as max_score,
                          COUNT(*) as total_records
                   FROM attention_records WHERE student_id = {_PH}""",
                (student['id'],)
            )

            student_stats.append({
                'name': student['name'],
                'avg_score': round(avg['avg_score'] or 0, 3),
                'min_score': round(avg['min_score'] or 0, 3),
                'max_score': round(avg['max_score'] or 0, 3),
                'total_records': avg['total_records']
            })

        duration = (session['ended_at'] or time.time()) - session['started_at']

        return {
            'session_id': session_id,
            'room_id': session['room_id'],
            'teacher_name': session['teacher_name'],
            'started_at': session['started_at'],
            'ended_at': session['ended_at'],
            'duration': round(duration),
            'is_active': bool(session['is_active']),
            'student_count': len(students),
            'students': student_stats
        }


def get_active_sessions():
    """Get all currently active sessions."""
    with get_db() as conn:
        sessions = _fetchall(conn,
            "SELECT * FROM sessions WHERE is_active = 1 ORDER BY started_at DESC"
        )

        result = []
        for s in sessions:
            count = _fetchone(conn,
                f"SELECT COUNT(*) as cnt FROM students WHERE session_id = {_PH}",
                (s['id'],)
            )

            result.append({
                'id': s['id'],
                'room_id': s['room_id'],
                'teacher_name': s['teacher_name'],
                'started_at': s['started_at'],
                'student_count': count['cnt']
            })

        return result


def get_past_sessions(limit=20):
    """Get past (ended) sessions."""
    with get_db() as conn:
        sessions = _fetchall(conn,
            f"""SELECT * FROM sessions WHERE is_active = 0
               ORDER BY ended_at DESC LIMIT {_PH}""",
            (limit,)
        )

        result = []
        for s in sessions:
            student_count = _fetchone(conn,
                f"SELECT COUNT(*) as cnt FROM students WHERE session_id = {_PH}",
                (s['id'],)
            )['cnt']

            avg = _fetchone(conn,
                f"""SELECT AVG(attention_score) as avg_score
                   FROM attention_records WHERE session_id = {_PH}""",
                (s['id'],)
            )

            annotation_count = _fetchone(conn,
                f"SELECT COUNT(*) as cnt FROM annotations WHERE session_id = {_PH}",
                (s['id'],)
            )['cnt']

            duration = (s['ended_at'] or time.time()) - s['started_at']

            result.append({
                'id': s['id'],
                'room_id': s['room_id'],
                'teacher_name': s['teacher_name'],
                'session_name': s.get('session_name', ''),
                'started_at': s['started_at'],
                'ended_at': s['ended_at'],
                'duration': round(duration),
                'student_count': student_count,
                'avg_score': round(avg['avg_score'] or 0, 3),
                'annotation_count': annotation_count
            })

        return result


def get_student_timeline(student_id, limit=300):
    """Get attention timeline for a specific student."""
    with get_db() as conn:
        records = _fetchall(conn,
            f"""SELECT attention_score, status, gaze_score,
                      head_pose_score, eye_openness, timestamp
               FROM attention_records
               WHERE student_id = {_PH}
               ORDER BY timestamp DESC LIMIT {_PH}""",
            (student_id, limit)
        )

        return list(reversed(records))


def _attention_grade(pct):
    """Return a letter grade based on attention percentage."""
    if pct >= 90: return 'A+'
    if pct >= 80: return 'A'
    if pct >= 70: return 'B'
    if pct >= 60: return 'C'
    if pct >= 50: return 'D'
    return 'F'


def export_session_csv(session_id):
    """Export comprehensive session data as a multi-section CSV (Excel-friendly UTF-8)."""
    # Flush buffer so export has the latest data
    flush_attention_buffer()

    with get_db() as conn:
        session = _fetchone(conn,
            f"SELECT * FROM sessions WHERE id = {_PH}", (session_id,)
        )
        if not session:
            return "Session not found\n"

        output = io.StringIO()
        # UTF-8 BOM — makes Excel open the file correctly without garbled text
        output.write('\ufeff')
        writer = csv.writer(output)

        started      = datetime.fromtimestamp(session['started_at'])
        ended_ts     = session['ended_at']
        ended        = datetime.fromtimestamp(ended_ts) if ended_ts else None
        duration_s   = (ended_ts or time.time()) - session['started_at']
        session_end  = ended_ts or time.time()

        overall = _fetchone(conn,
            f"""SELECT AVG(attention_score) as avg,
                      MIN(attention_score) as mn,
                      MAX(attention_score) as mx,
                      COUNT(*) as cnt
               FROM attention_records WHERE session_id = {_PH}""",
            (session_id,)
        )

        students_all = _fetchall(conn,
            f"SELECT * FROM students WHERE session_id = {_PH} ORDER BY joined_at ASC",
            (session_id,)
        )

        avg_pct = round((overall['avg'] or 0) * 100, 1)

        # ── SECTION 1: Session Overview ──────────────────────────
        writer.writerow(["GAZE — Session Report"])
        writer.writerow(["Generated", datetime.now().strftime('%Y-%m-%d %H:%M')])
        writer.writerow([])
        writer.writerow(["SECTION 1 — SESSION OVERVIEW"])
        writer.writerow(["Field", "Value"])
        writer.writerow(["Room Code",              session['room_id']])
        writer.writerow(["Teacher",                session['teacher_name']])
        writer.writerow(["Date",                   started.strftime('%A, %B %d %Y')])
        writer.writerow(["Started At",             started.strftime('%I:%M %p')])
        writer.writerow(["Ended At",               ended.strftime('%I:%M %p') if ended else "Still active"])
        writer.writerow(["Duration",               _format_duration(duration_s)])
        writer.writerow(["Total Students",         len(students_all)])
        writer.writerow(["Class Average Attention",f"{avg_pct}%"])
        writer.writerow(["Class Grade",            _attention_grade(avg_pct)])
        writer.writerow(["Lowest Attention",       f"{round((overall['mn'] or 0) * 100, 1)}%"])
        writer.writerow(["Highest Attention",      f"{round((overall['mx'] or 0) * 100, 1)}%"])
        writer.writerow(["Total Data Points",      overall['cnt']])
        writer.writerow([])

        # ── SECTION 2: Per-Student Summary ───────────────────────
        writer.writerow(["SECTION 2 — STUDENT PERFORMANCE SUMMARY"])
        writer.writerow([
            "Student Name",
            "Joined",
            "Left",
            "Time in Session",
            "Avg Attention",
            "Grade",
            "Lowest",
            "Highest",
            "Focused %",
            "Partially Attentive %",
            "Distracted %",
            "Data Points"
        ])

        for s in students_all:
            left_ts  = s['left_at'] or session_end
            duration = left_ts - s['joined_at']

            stats = _fetchone(conn,
                f"""SELECT
                       AVG(attention_score) as avg_s,
                       MIN(attention_score) as min_s,
                       MAX(attention_score) as max_s,
                       COUNT(*) as total,
                       SUM(CASE WHEN status='Focused'             THEN 1 ELSE 0 END) as focused,
                       SUM(CASE WHEN status='Partially Attentive' THEN 1 ELSE 0 END) as partial,
                       SUM(CASE WHEN status='Distracted'          THEN 1 ELSE 0 END) as distracted
                   FROM attention_records WHERE student_id = {_PH}""",
                (s['id'],)
            )

            total   = stats['total'] or 1
            s_avg   = round((stats['avg_s'] or 0) * 100, 1)
            writer.writerow([
                s['name'],
                datetime.fromtimestamp(s['joined_at']).strftime('%I:%M %p'),
                datetime.fromtimestamp(left_ts).strftime('%I:%M %p') if s['left_at'] else "Still in session",
                _format_duration(duration),
                f"{s_avg}%",
                _attention_grade(s_avg),
                f"{round((stats['min_s'] or 0) * 100, 1)}%",
                f"{round((stats['max_s'] or 0) * 100, 1)}%",
                f"{round((stats['focused'] or 0)    / total * 100, 1)}%",
                f"{round((stats['partial'] or 0)    / total * 100, 1)}%",
                f"{round((stats['distracted'] or 0) / total * 100, 1)}%",
                stats['total'] or 0
            ])
        writer.writerow([])

        # ── SECTION 3: Teacher Annotations ───────────────────────
        annotations = _fetchall(conn,
            f"SELECT * FROM annotations WHERE session_id = {_PH} ORDER BY timestamp ASC",
            (session_id,)
        )

        if annotations:
            writer.writerow(["SECTION 3 — TEACHER ANNOTATIONS"])
            writer.writerow(["Time", "Type", "Note", "Class Attention at Time"])
            for ann in annotations:
                dt = datetime.fromtimestamp(ann['timestamp'])
                writer.writerow([
                    dt.strftime('%I:%M:%S %p'),
                    ann['annotation_type'].capitalize(),
                    ann['text'],
                    f"{round(ann['class_avg_at_time'] * 100, 1)}%"
                ])
            writer.writerow([])

        # ── SECTION 4: Detailed Attention Timeline ────────────────
        section_num = 4 if annotations else 3
        writer.writerow([f"SECTION {section_num} — DETAILED ATTENTION TIMELINE"])
        writer.writerow([
            "Time", "Student Name",
            "Attention %", "Status",
            "Gaze %", "Head Pose %", "Eye Openness %"
        ])

        records = _fetchall(conn,
            f"""SELECT s.name as student_name,
                      a.attention_score, a.status,
                      a.gaze_score, a.head_pose_score, a.eye_openness,
                      a.timestamp
               FROM attention_records a
               JOIN students s ON a.student_id = s.id
               WHERE a.session_id = {_PH}
               ORDER BY a.timestamp ASC""",
            (session_id,)
        )

        for r in records:
            dt = datetime.fromtimestamp(r['timestamp'])
            writer.writerow([
                dt.strftime('%H:%M:%S'),
                r['student_name'],
                f"{round(r['attention_score'] * 100, 1)}%",
                r['status'],
                f"{round(r['gaze_score']      * 100, 1)}%",
                f"{round(r['head_pose_score'] * 100, 1)}%",
                f"{round(r['eye_openness']    * 100, 1)}%",
            ])

        return output.getvalue()


# ============================================================
# Helpers
# ============================================================

def _format_duration(seconds):
    """Format seconds into human-readable duration."""
    m = int(seconds // 60)
    s = int(seconds % 60)
    if m >= 60:
        h = m // 60
        m = m % 60
        return f"{h}h {m}m"
    return f"{m}m {s}s"


# Initialize database on import
init_db()
