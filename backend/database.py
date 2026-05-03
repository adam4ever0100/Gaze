"""
SQLite Database Module for Persistent Session Storage.

Stores session data, attention records, student information,
teacher annotations, and attendance data.
"""

import sqlite3
import os
import csv
import io
import time
from datetime import datetime
from contextlib import contextmanager

# Database file location
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'gaze.db')


@contextmanager
def get_db():
    """Context manager for database connections."""
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


def init_db():
    """Initialize the database schema."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id TEXT NOT NULL,
                teacher_name TEXT DEFAULT '',
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
# Session CRUD
# ============================================================

def create_session(room_id, teacher_name=""):
    """Create a new session and return its ID."""
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO sessions (room_id, teacher_name, started_at) VALUES (?, ?, ?)",
            (room_id, teacher_name, time.time())
        )
        return cursor.lastrowid


def end_session(session_id):
    """Mark a session as ended."""
    with get_db() as conn:
        conn.execute(
            "UPDATE sessions SET ended_at = ?, is_active = 0 WHERE id = ?",
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
        existing = conn.execute(
            "SELECT id FROM students WHERE session_id = ? AND name = ? ORDER BY joined_at DESC LIMIT 1",
            (session_id, name)
        ).fetchone()

        if existing:
            # Re-activate: clear left_at and update joined_at to now
            conn.execute(
                "UPDATE students SET joined_at = ?, left_at = NULL WHERE id = ?",
                (time.time(), existing['id'])
            )
            return existing['id']

        # New student
        cursor = conn.execute(
            "INSERT INTO students (name, session_id, joined_at) VALUES (?, ?, ?)",
            (name, session_id, time.time())
        )
        return cursor.lastrowid


def student_left(student_id):
    """Mark a student as having left the session."""
    with get_db() as conn:
        conn.execute(
            "UPDATE students SET left_at = ? WHERE id = ?",
            (time.time(), student_id)
        )


# ============================================================
# Attention Records
# ============================================================

def record_attention(student_id, session_id, score, status,
                     gaze_score=0, head_pose_score=0, eye_openness=0):
    """Record an attention data point."""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO attention_records 
               (student_id, session_id, attention_score, status,
                gaze_score, head_pose_score, eye_openness, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (student_id, session_id, score, status,
             gaze_score, head_pose_score, eye_openness, time.time())
        )


# ============================================================
# Annotations
# ============================================================

def add_annotation(session_id, text, teacher_name="", annotation_type="note", class_avg=0):
    """Add a teacher annotation to a session."""
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO annotations (session_id, teacher_name, text, annotation_type, timestamp, class_avg_at_time)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, teacher_name, text, annotation_type, time.time(), class_avg)
        )
        return cursor.lastrowid


def get_annotations(session_id):
    """Get all annotations for a session."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM annotations WHERE session_id = ? ORDER BY timestamp ASC",
            (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_annotation(annotation_id):
    """Delete an annotation."""
    with get_db() as conn:
        conn.execute("DELETE FROM annotations WHERE id = ?", (annotation_id,))


# ============================================================
# Attendance Reports
# ============================================================

def get_attendance_report(session_id):
    """Get attendance report for a session — join/leave times, durations."""
    with get_db() as conn:
        session = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            return None

        students = conn.execute(
            "SELECT * FROM students WHERE session_id = ? ORDER BY joined_at ASC",
            (session_id,)
        ).fetchall()

        session_end = session['ended_at'] or time.time()
        report = []
        for s in students:
            left = s['left_at'] or session_end
            duration = left - s['joined_at']

            # Get average attention for this student
            avg = conn.execute(
                "SELECT AVG(attention_score) as avg_score FROM attention_records WHERE student_id = ?",
                (s['id'],)
            ).fetchone()

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
    with get_db() as conn:
        session = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            return None

        students = conn.execute(
            "SELECT * FROM students WHERE session_id = ?", (session_id,)
        ).fetchall()

        records = conn.execute(
            """SELECT attention_score, status, timestamp FROM attention_records
               WHERE session_id = ? ORDER BY timestamp ASC""",
            (session_id,)
        ).fetchall()

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
            s_records = conn.execute(
                """SELECT AVG(attention_score) as avg, MIN(attention_score) as min_s,
                          MAX(attention_score) as max_s, COUNT(*) as cnt
                   FROM attention_records WHERE student_id = ?""",
                (student['id'],)
            ).fetchone()
            if s_records['cnt'] > 0:
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
        session = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()

        if not session:
            return None

        students = conn.execute(
            "SELECT * FROM students WHERE session_id = ?", (session_id,)
        ).fetchall()

        # Get per-student averages
        student_stats = []
        for student in students:
            avg = conn.execute(
                """SELECT AVG(attention_score) as avg_score,
                          MIN(attention_score) as min_score,
                          MAX(attention_score) as max_score,
                          COUNT(*) as total_records
                   FROM attention_records WHERE student_id = ?""",
                (student['id'],)
            ).fetchone()

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
        sessions = conn.execute(
            "SELECT * FROM sessions WHERE is_active = 1 ORDER BY started_at DESC"
        ).fetchall()

        result = []
        for s in sessions:
            student_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM students WHERE session_id = ?",
                (s['id'],)
            ).fetchone()['cnt']

            result.append({
                'id': s['id'],
                'room_id': s['room_id'],
                'teacher_name': s['teacher_name'],
                'started_at': s['started_at'],
                'student_count': student_count
            })

        return result


def get_past_sessions(limit=20):
    """Get past (ended) sessions."""
    with get_db() as conn:
        sessions = conn.execute(
            """SELECT * FROM sessions WHERE is_active = 0 
               ORDER BY ended_at DESC LIMIT ?""",
            (limit,)
        ).fetchall()

        result = []
        for s in sessions:
            student_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM students WHERE session_id = ?",
                (s['id'],)
            ).fetchone()['cnt']

            avg = conn.execute(
                """SELECT AVG(attention_score) as avg_score
                   FROM attention_records WHERE session_id = ?""",
                (s['id'],)
            ).fetchone()

            annotation_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM annotations WHERE session_id = ?",
                (s['id'],)
            ).fetchone()['cnt']

            duration = (s['ended_at'] or time.time()) - s['started_at']

            result.append({
                'id': s['id'],
                'room_id': s['room_id'],
                'teacher_name': s['teacher_name'],
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
        records = conn.execute(
            """SELECT attention_score, status, gaze_score,
                      head_pose_score, eye_openness, timestamp
               FROM attention_records
               WHERE student_id = ?
               ORDER BY timestamp DESC LIMIT ?""",
            (student_id, limit)
        ).fetchall()

        return [dict(r) for r in reversed(records)]


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
    with get_db() as conn:
        session = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
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

        overall = conn.execute(
            """SELECT AVG(attention_score) as avg,
                      MIN(attention_score) as mn,
                      MAX(attention_score) as mx,
                      COUNT(*) as cnt
               FROM attention_records WHERE session_id = ?""",
            (session_id,)
        ).fetchone()

        students_all = conn.execute(
            "SELECT * FROM students WHERE session_id = ? ORDER BY joined_at ASC",
            (session_id,)
        ).fetchall()

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

            stats = conn.execute(
                """SELECT
                       AVG(attention_score) as avg_s,
                       MIN(attention_score) as min_s,
                       MAX(attention_score) as max_s,
                       COUNT(*) as total,
                       SUM(CASE WHEN status='Focused'             THEN 1 ELSE 0 END) as focused,
                       SUM(CASE WHEN status='Partially Attentive' THEN 1 ELSE 0 END) as partial,
                       SUM(CASE WHEN status='Distracted'          THEN 1 ELSE 0 END) as distracted
                   FROM attention_records WHERE student_id = ?""",
                (s['id'],)
            ).fetchone()

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
                f"{round(stats['focused']    / total * 100, 1)}%",
                f"{round(stats['partial']    / total * 100, 1)}%",
                f"{round(stats['distracted'] / total * 100, 1)}%",
                stats['total'] or 0
            ])
        writer.writerow([])

        # ── SECTION 3: Teacher Annotations ───────────────────────
        annotations = conn.execute(
            "SELECT * FROM annotations WHERE session_id = ? ORDER BY timestamp ASC",
            (session_id,)
        ).fetchall()

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

        records = conn.execute(
            """SELECT s.name as student_name,
                      a.attention_score, a.status,
                      a.gaze_score, a.head_pose_score, a.eye_openness,
                      a.timestamp
               FROM attention_records a
               JOIN students s ON a.student_id = s.id
               WHERE a.session_id = ?
               ORDER BY a.timestamp ASC""",
            (session_id,)
        ).fetchall()

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
