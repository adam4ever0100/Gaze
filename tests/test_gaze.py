"""
Unit tests for the Gaze Attention Monitoring System.

Tests cover:
- Attention score classification logic
- Backend API endpoints
- Database operations
- Annotations CRUD
- Attendance reports
- AI session summaries
- Rate limiter configuration
- Environment validation
"""

import sys
import os
import time
import json
import pytest
import tempfile

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# Test: Attention Score Classification
# ============================================================

class TestAttentionClassification:
    """Test the attention status classification logic."""
    
    def test_focused_score(self):
        from config import THRESHOLD_FOCUSED
        assert 0.85 >= THRESHOLD_FOCUSED  # 85% should be Focused
    
    def test_partial_score(self):
        from config import THRESHOLD_FOCUSED, THRESHOLD_PARTIAL
        assert 0.55 < THRESHOLD_FOCUSED
        assert 0.55 >= THRESHOLD_PARTIAL  # 55% should be Partially Attentive
    
    def test_distracted_score(self):
        from config import THRESHOLD_PARTIAL
        assert 0.20 < THRESHOLD_PARTIAL  # 20% should be Distracted
    
    def test_weights_sum_to_one(self):
        from config import WEIGHT_GAZE, WEIGHT_HEAD_POSE, WEIGHT_EYE_OPENNESS, WEIGHT_FACE_PRESENCE
        total = WEIGHT_GAZE + WEIGHT_HEAD_POSE + WEIGHT_EYE_OPENNESS + WEIGHT_FACE_PRESENCE
        assert abs(total - 1.0) < 0.001

    def test_classify_status(self):
        """Test backend server's classify_status function."""
        from backend.server import classify_status
        assert classify_status(0.85) == "Focused"
        assert classify_status(0.70) == "Focused"
        assert classify_status(0.55) == "Partially Attentive"
        assert classify_status(0.40) == "Partially Attentive"
        assert classify_status(0.20) == "Distracted"
        assert classify_status(0.0) == "Distracted"


# ============================================================
# Test: Configuration Validation
# ============================================================

class TestConfigValidation:
    """Test configuration settings and validation."""

    def test_threshold_order(self):
        from config import THRESHOLD_FOCUSED, THRESHOLD_PARTIAL
        assert THRESHOLD_FOCUSED > THRESHOLD_PARTIAL

    def test_port_ranges(self):
        from config import STUDENT_APP_PORT, BACKEND_PORT
        assert 1024 <= STUDENT_APP_PORT <= 65535
        assert 1024 <= BACKEND_PORT <= 65535

    def test_rate_limit_settings(self):
        from config import RATE_LIMIT_PER_SID, RATE_LIMIT_PER_IP, MAX_CONNECTIONS_PER_IP
        assert RATE_LIMIT_PER_SID > 0
        assert RATE_LIMIT_PER_IP > 0
        assert MAX_CONNECTIONS_PER_IP > 0

    def test_ice_servers_present(self):
        from config import ICE_SERVERS
        assert len(ICE_SERVERS) >= 1
        assert 'urls' in ICE_SERVERS[0]

    def test_security_headers_present(self):
        from config import SECURITY_HEADERS
        assert 'Content-Security-Policy' in SECURITY_HEADERS
        assert 'X-Content-Type-Options' in SECURITY_HEADERS


# ============================================================
# Test: Database Operations
# ============================================================

class TestDatabase:
    """Test SQLite database operations."""

    def setup_method(self):
        """Use a temp database for each test."""
        import backend.database as db
        self.original_path = db.DB_PATH
        self.temp_dir = tempfile.mkdtemp()
        db.DB_PATH = os.path.join(self.temp_dir, 'test.db')
        db.init_db()
        self.db = db
    
    def teardown_method(self):
        """Restore original DB path."""
        self.db.DB_PATH = self.original_path
    
    def test_create_session(self):
        session_id = self.db.create_session("TEST01", "Dr. Smith")
        assert session_id is not None
        assert session_id > 0
    
    def test_add_student(self):
        session_id = self.db.create_session("TEST02", "Teacher")
        student_id = self.db.add_student(session_id, "Alice")
        assert student_id is not None
        assert student_id > 0
    
    def test_record_attention(self):
        session_id = self.db.create_session("TEST03", "Teacher")
        student_id = self.db.add_student(session_id, "Bob")
        
        # Should not raise
        self.db.record_attention(student_id, session_id, 0.85, "Focused")
        self.db.record_attention(student_id, session_id, 0.45, "Partially Attentive")
    
    def test_session_summary(self):
        session_id = self.db.create_session("TEST04", "Teacher")
        student_id = self.db.add_student(session_id, "Charlie")
        
        self.db.record_attention(student_id, session_id, 0.80, "Focused")
        self.db.record_attention(student_id, session_id, 0.90, "Focused")
        
        summary = self.db.get_session_summary(session_id)
        assert summary is not None
        assert summary['room_id'] == "TEST04"
        assert summary['student_count'] == 1
        assert summary['students'][0]['avg_score'] == 0.85
    
    def test_end_session(self):
        session_id = self.db.create_session("TEST05", "Teacher")
        self.db.end_session(session_id)
        
        past = self.db.get_past_sessions()
        assert len(past) >= 1
        assert past[0]['room_id'] == "TEST05"
    
    def test_export_csv(self):
        session_id = self.db.create_session("TEST06", "Teacher")
        student_id = self.db.add_student(session_id, "Diana")
        self.db.record_attention(student_id, session_id, 0.75, "Focused")
        
        csv = self.db.export_session_csv(session_id)
        assert "Diana" in csv
        assert "Focused" in csv
        assert "Student Name" in csv  # Header row
    
    def test_active_sessions(self):
        self.db.create_session("ACTIVE1", "Teacher")
        active = self.db.get_active_sessions()
        assert len(active) >= 1


# ============================================================
# Test: Annotations
# ============================================================

class TestAnnotations:
    """Test annotation CRUD operations."""

    def setup_method(self):
        import backend.database as db
        self.original_path = db.DB_PATH
        self.temp_dir = tempfile.mkdtemp()
        db.DB_PATH = os.path.join(self.temp_dir, 'test.db')
        db.init_db()
        self.db = db

    def teardown_method(self):
        self.db.DB_PATH = self.original_path

    def test_add_annotation(self):
        session_id = self.db.create_session("ANN01", "Teacher")
        ann_id = self.db.add_annotation(session_id, "Students lost focus", "Teacher", "warning", 0.65)
        assert ann_id is not None
        assert ann_id > 0

    def test_get_annotations(self):
        session_id = self.db.create_session("ANN02", "Teacher")
        self.db.add_annotation(session_id, "Note 1", "Teacher", "note", 0.8)
        self.db.add_annotation(session_id, "Bookmark here", "Teacher", "bookmark", 0.75)

        anns = self.db.get_annotations(session_id)
        assert len(anns) == 2
        assert anns[0]['text'] == "Note 1"
        assert anns[1]['annotation_type'] == "bookmark"

    def test_delete_annotation(self):
        session_id = self.db.create_session("ANN03", "Teacher")
        ann_id = self.db.add_annotation(session_id, "To delete", "Teacher")

        self.db.delete_annotation(ann_id)
        anns = self.db.get_annotations(session_id)
        assert len(anns) == 0

    def test_empty_annotations(self):
        session_id = self.db.create_session("ANN04", "Teacher")
        anns = self.db.get_annotations(session_id)
        assert len(anns) == 0


# ============================================================
# Test: Attendance Reports
# ============================================================

class TestAttendance:
    """Test attendance report generation."""

    def setup_method(self):
        import backend.database as db
        self.original_path = db.DB_PATH
        self.temp_dir = tempfile.mkdtemp()
        db.DB_PATH = os.path.join(self.temp_dir, 'test.db')
        db.init_db()
        self.db = db

    def teardown_method(self):
        self.db.DB_PATH = self.original_path

    def test_attendance_report(self):
        session_id = self.db.create_session("ATT01", "Teacher")
        s1 = self.db.add_student(session_id, "Alice")
        s2 = self.db.add_student(session_id, "Bob")

        self.db.record_attention(s1, session_id, 0.85, "Focused")
        self.db.record_attention(s2, session_id, 0.45, "Partially Attentive")

        report = self.db.get_attendance_report(session_id)
        assert report is not None
        assert report['total_students'] == 2
        assert report['room_id'] == "ATT01"
        assert len(report['students']) == 2

    def test_attendance_report_with_left(self):
        session_id = self.db.create_session("ATT02", "Teacher")
        s1 = self.db.add_student(session_id, "Charlie")
        self.db.student_left(s1)

        report = self.db.get_attendance_report(session_id)
        assert report['students'][0]['name'] == "Charlie"
        assert report['students'][0]['duration_seconds'] >= 0

    def test_attendance_report_nonexistent(self):
        report = self.db.get_attendance_report(99999)
        assert report is None


# ============================================================
# Test: AI Session Summary
# ============================================================

class TestAISummary:
    """Test AI-generated session summary."""

    def setup_method(self):
        import backend.database as db
        self.original_path = db.DB_PATH
        self.temp_dir = tempfile.mkdtemp()
        db.DB_PATH = os.path.join(self.temp_dir, 'test.db')
        db.init_db()
        self.db = db

    def teardown_method(self):
        self.db.DB_PATH = self.original_path

    def test_ai_summary_with_data(self):
        session_id = self.db.create_session("AI01", "Teacher")
        s1 = self.db.add_student(session_id, "Alice")

        # Add multiple attention records
        for score in [0.85, 0.90, 0.75, 0.80, 0.70, 0.65, 0.85, 0.90, 0.80, 0.75]:
            self.db.record_attention(s1, session_id, score, "Focused")

        summary = self.db.generate_ai_summary(session_id)
        assert summary is not None
        assert 'summary' in summary
        assert summary['student_count'] == 1
        assert summary['overall_avg'] > 0

    def test_ai_summary_empty_session(self):
        session_id = self.db.create_session("AI02", "Teacher")
        summary = self.db.generate_ai_summary(session_id)
        assert summary is not None
        assert 'No attention data' in summary['summary']

    def test_ai_summary_nonexistent(self):
        summary = self.db.generate_ai_summary(99999)
        assert summary is None

    def test_ai_summary_recommendations(self):
        session_id = self.db.create_session("AI03", "Teacher")
        s1 = self.db.add_student(session_id, "Bob")

        # High scores should generate positive recommendation
        for _ in range(10):
            self.db.record_attention(s1, session_id, 0.85, "Focused")

        summary = self.db.generate_ai_summary(session_id)
        assert len(summary['recommendations']) > 0


# ============================================================
# Test: Backend API Endpoints
# ============================================================

class TestBackendAPI:
    """Test Flask REST API endpoints."""

    def setup_method(self):
        from backend.server import app
        app.config['TESTING'] = True
        self.client = app.test_client()
    
    def test_dashboard_empty(self):
        res = self.client.get('/dashboard')
        data = json.loads(res.data)
        assert data['success'] is True
        assert data['active_students'] == 0
    
    def test_history_empty(self):
        res = self.client.get('/dashboard/history')
        data = json.loads(res.data)
        assert data['success'] is True
        assert data['count'] == 0
    
    def test_rooms_list(self):
        res = self.client.get('/rooms')
        data = json.loads(res.data)
        assert data['success'] is True
        assert isinstance(data['rooms'], list)
    
    def test_submit_score(self):
        res = self.client.post('/submit',
            data=json.dumps({
                'student_name': 'TestStudent',
                'meeting_id': 'default',
                'attention_score': 0.75,
                'status': 'Focused'
            }),
            content_type='application/json'
        )
        data = json.loads(res.data)
        assert data['success'] is True
    
    def test_sessions_list(self):
        res = self.client.get('/sessions')
        data = json.loads(res.data)
        assert data['success'] is True
        assert isinstance(data['sessions'], list)
    
    def test_teacher_dashboard_served(self):
        res = self.client.get('/')
        assert res.status_code == 200

    def test_session_summary_404(self):
        res = self.client.get('/sessions/99999/summary')
        assert res.status_code == 404

    def test_attendance_endpoint_404(self):
        res = self.client.get('/sessions/99999/attendance')
        assert res.status_code == 404

    def test_ai_summary_endpoint_404(self):
        res = self.client.get('/sessions/99999/ai-summary')
        assert res.status_code == 404

    def test_annotations_endpoint(self):
        res = self.client.get('/sessions/99999/annotations')
        data = json.loads(res.data)
        assert data['success'] is True
        assert isinstance(data['annotations'], list)


# ============================================================
# Test: Rate Limiter
# ============================================================

class TestRateLimiter:
    """Test rate limiting functions."""

    def test_sid_rate_limit(self):
        from backend.server import check_rate_limit, rate_limits_sid, RATE_LIMIT_PER_SID
        test_sid = 'test_sid_12345'
        rate_limits_sid.pop(test_sid, None)

        # First requests should pass
        for _ in range(RATE_LIMIT_PER_SID):
            assert check_rate_limit(test_sid) is True

        # Next request should be blocked
        assert check_rate_limit(test_sid) is False

        # Cleanup
        rate_limits_sid.pop(test_sid, None)

    def test_ip_rate_limit(self):
        from backend.server import check_ip_rate_limit, rate_limits_ip, RATE_LIMIT_PER_IP
        test_ip = '192.168.1.255'
        rate_limits_ip.pop(test_ip, None)

        # First requests should pass
        for _ in range(RATE_LIMIT_PER_IP):
            assert check_ip_rate_limit(test_ip) is True

        # Next request should be blocked
        assert check_ip_rate_limit(test_ip) is False

        # Cleanup
        rate_limits_ip.pop(test_ip, None)


# ============================================================
# Test: PDF Report Generation
# ============================================================

class TestPDFReport:
    """Test PDF report generation endpoint."""

    def setup_method(self):
        from backend.server import app
        self.client = app.test_client()
        import backend.database as db
        self._orig = db.DB_PATH
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        db.DB_PATH = self.tmp.name
        db.init_db()

    def teardown_method(self):
        import backend.database as db
        db.DB_PATH = self._orig
        os.unlink(self.tmp.name)

    def test_pdf_report_404_for_missing_session(self):
        r = self.client.get('/sessions/9999/report.pdf')
        assert r.status_code == 404

    def test_pdf_report_generates_for_valid_session(self):
        from backend.database import create_session, add_student, record_attention, end_session
        sid = create_session('PDFTEST', 'Teacher')
        student_id = add_student(sid, 'Student A')
        record_attention(student_id, sid, 0.85, 'focused')
        record_attention(student_id, sid, 0.75, 'focused')
        end_session(sid)

        r = self.client.get(f'/sessions/{sid}/report.pdf')
        assert r.status_code == 200
        assert r.content_type == 'application/pdf'
        assert b'%PDF' in r.data[:10]  # PDF magic bytes

    def test_pdf_contains_session_data(self):
        from backend.database import create_session, add_student, record_attention, end_session
        sid = create_session('PDFC', 'TestTeacher')
        student_id = add_student(sid, 'PDF Student')
        record_attention(student_id, sid, 0.9, 'focused')
        end_session(sid)

        r = self.client.get(f'/sessions/{sid}/report.pdf')
        assert r.status_code == 200
        assert len(r.data) > 500  # PDF should have substantial content


# ============================================================
# Test: API Documentation
# ============================================================

class TestAPIDocs:
    """Test API documentation endpoint."""

    def setup_method(self):
        from backend.server import app
        self.client = app.test_client()

    def test_api_docs_page_loads(self):
        r = self.client.get('/api/docs')
        assert r.status_code == 200

    def test_api_docs_contains_endpoints(self):
        r = self.client.get('/api/docs')
        html = r.data.decode()
        assert '/api/dashboard' in html
        assert '/api/rooms' in html
        assert '/api/sessions' in html
        assert 'report.pdf' in html
        assert 'Socket.IO' in html

    def test_api_docs_contains_websocket_events(self):
        r = self.client.get('/api/docs')
        html = r.data.decode()
        assert 'hand-raise' in html
        assert 'reaction' in html
        assert 'attention-score' in html
        assert 'create-room' in html


# ============================================================
# Test: Extended Database Operations
# ============================================================

class TestDatabaseExtended:
    """Extended database edge case tests."""

    def setup_method(self):
        import backend.database as db
        self._orig = db.DB_PATH
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        db.DB_PATH = self.tmp.name
        db.init_db()

    def teardown_method(self):
        import backend.database as db
        db.DB_PATH = self._orig
        os.unlink(self.tmp.name)

    def test_multiple_students_in_session(self):
        from backend.database import create_session, add_student, record_attention, get_session_summary
        sid = create_session('MULTI', 'Teacher')
        s1 = add_student(sid, 'Alice')
        s2 = add_student(sid, 'Bob')
        s3 = add_student(sid, 'Charlie')
        record_attention(s1, sid, 0.9, 'focused')
        record_attention(s2, sid, 0.5, 'partial')
        record_attention(s3, sid, 0.2, 'distracted')
        summary = get_session_summary(sid)
        assert summary['student_count'] == 3

    def test_student_left_tracking(self):
        from backend.database import create_session, add_student, student_left, get_attendance_report, end_session
        sid = create_session('LEFT', 'Teacher')
        s_id = add_student(sid, 'Leaver')
        student_left(s_id)
        end_session(sid)
        report = get_attendance_report(sid)
        assert report['students'][0]['left_at'] is not None

    def test_session_summary_with_no_students(self):
        from backend.database import create_session, get_session_summary
        sid = create_session('EMPTY', 'Teacher')
        summary = get_session_summary(sid)
        assert summary['student_count'] == 0

    def test_multiple_attention_records(self):
        from backend.database import create_session, add_student, record_attention, get_student_timeline
        sid = create_session('TIMELINE', 'Teacher')
        s_id = add_student(sid, 'TimelineStudent')
        for i in range(10):
            record_attention(s_id, sid, 0.5 + i * 0.05, 'focused' if i > 5 else 'partial')
        timeline = get_student_timeline(s_id)
        assert len(timeline) == 10

    def test_annotation_types(self):
        from backend.database import create_session, add_annotation, get_annotations
        sid = create_session('ANN', 'Teacher')
        add_annotation(sid, 'Note text', 'Teacher', 'note')
        add_annotation(sid, 'Warning text', 'Teacher', 'warning')
        add_annotation(sid, 'Highlight text', 'Teacher', 'highlight')
        anns = get_annotations(sid)
        assert len(anns) == 3
        types = [a['annotation_type'] for a in anns]
        assert 'note' in types
        assert 'warning' in types
        assert 'highlight' in types

    def test_end_session_sets_ended_at(self):
        from backend.database import create_session, end_session, get_db
        sid = create_session('ENDTEST', 'Teacher')
        end_session(sid)
        with get_db() as conn:
            row = conn.execute("SELECT ended_at FROM sessions WHERE id = ?", (sid,)).fetchone()
        assert row['ended_at'] is not None

    def test_past_sessions_returns_ended(self):
        from backend.database import create_session, end_session, get_past_sessions
        sid = create_session('PAST', 'Teacher')
        end_session(sid)
        past = get_past_sessions()
        assert any(s['id'] == sid for s in past)


# ============================================================
# Test: Score Classification Edge Cases
# ============================================================

class TestScoreEdgeCases:
    """Test attention score boundary conditions."""

    def test_zero_score(self):
        from backend.server import classify_status
        assert classify_status(0.0) == 'Distracted'

    def test_perfect_score(self):
        from backend.server import classify_status
        assert classify_status(1.0) == 'Focused'

    def test_exact_threshold_focused(self):
        from backend.server import classify_status
        assert classify_status(0.7) == 'Focused'

    def test_exact_threshold_partial(self):
        from backend.server import classify_status
        assert classify_status(0.4) == 'Partially Attentive'

    def test_just_below_focused(self):
        from backend.server import classify_status
        assert classify_status(0.69) == 'Partially Attentive'

    def test_just_below_partial(self):
        from backend.server import classify_status
        assert classify_status(0.39) == 'Distracted'

    def test_negative_score(self):
        from backend.server import classify_status
        assert classify_status(-0.1) == 'Distracted'

    def test_over_maximum(self):
        from backend.server import classify_status
        assert classify_status(1.5) == 'Focused'


# ============================================================
# Test: Backend API Extended
# ============================================================

class TestBackendAPIExtended:
    """Extended backend API tests."""

    def setup_method(self):
        from backend.server import app
        self.client = app.test_client()

    def test_dashboard_history_endpoint(self):
        r = self.client.get('/dashboard/history')
        assert r.status_code == 200

    def test_static_files_404(self):
        r = self.client.get('/nonexistent_file.xyz')
        assert r.status_code == 404

    def test_submit_score_post_endpoint(self):
        r = self.client.post('/submit',
                             json={'student_name': 'test', 'room_code': 'XYZ', 'score': 0.5, 'status': 'partial'},
                             content_type='application/json')
        assert r.status_code in [200, 400, 404]

    def test_sessions_endpoint_returns_list(self):
        r = self.client.get('/sessions')
        data = r.get_json()
        assert isinstance(data.get('sessions', []), list)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

