"""
Temporal Pattern Analysis for Gaze Attention Monitoring.

Analyzes time-series attention data to detect patterns that frame-by-frame
scoring misses:

- Attention span detection (sustained focus periods)
- Fatigue onset detection (when attention drops after sustained focus)
- Recovery detection (self-correction from distracted → focused)
- Class-wide engagement trends (when attention typically drops)
- Per-student engagement profiles (early-riser, late-fader, etc.)
"""

import time
from typing import Dict, List, Optional, Tuple

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import THRESHOLD_FOCUSED, THRESHOLD_PARTIAL
from backend.database import get_db, _fetchall, _PH


# ============================================================
# Attention Span Detection
# ============================================================

def detect_attention_spans(student_id: int, session_id: int = None) -> List[Dict]:
    """
    Find contiguous periods where a student was Focused, Partially Attentive,
    or Distracted, and compute the duration of each span.

    Returns list of spans:
        [{ "state": "Focused", "start": float, "end": float,
           "duration_sec": float, "avg_score": float }, ...]
    """
    query = f"""
        SELECT attention_score, status, timestamp
        FROM attention_records
        WHERE student_id = {_PH}
    """
    params = [student_id]
    if session_id:
        query += f" AND session_id = {_PH}"
        params.append(session_id)
    query += " ORDER BY timestamp ASC"

    with get_db() as conn:
        records = _fetchall(conn, query, tuple(params))

    if not records:
        return []

    spans = []
    current_state = records[0]['status']
    span_start = records[0]['timestamp']
    span_scores = [records[0]['attention_score']]

    for r in records[1:]:
        if r['status'] != current_state:
            # Close current span
            spans.append({
                "state": current_state,
                "start": span_start,
                "end": r['timestamp'],
                "duration_sec": round(r['timestamp'] - span_start, 1),
                "avg_score": round(sum(span_scores) / len(span_scores), 3),
            })
            # Start new span
            current_state = r['status']
            span_start = r['timestamp']
            span_scores = [r['attention_score']]
        else:
            span_scores.append(r['attention_score'])

    # Close final span
    if span_scores:
        spans.append({
            "state": current_state,
            "start": span_start,
            "end": records[-1]['timestamp'],
            "duration_sec": round(records[-1]['timestamp'] - span_start, 1),
            "avg_score": round(sum(span_scores) / len(span_scores), 3),
        })

    return spans


# ============================================================
# Fatigue Onset Detection
# ============================================================

def detect_fatigue_onset(student_id: int, session_id: int,
                         window_min: float = 5.0,
                         drop_threshold: float = 0.15) -> List[Dict]:
    """
    Detect when a student's attention drops significantly after a period
    of sustained focus. Uses a sliding window comparison.

    Args:
        window_min: Window size in minutes to compare before/after.
        drop_threshold: Score drop threshold to flag fatigue (0.15 = 15%).

    Returns list of fatigue events:
        [{ "onset_time": float, "minutes_into_session": float,
           "score_before": float, "score_after": float, "drop": float }, ...]
    """
    query = f"""
        SELECT attention_score, timestamp
        FROM attention_records
        WHERE student_id = {_PH} AND session_id = {_PH}
        ORDER BY timestamp ASC
    """

    with get_db() as conn:
        records = _fetchall(conn, query, (student_id, session_id))
        session = conn.execute(
            f"SELECT started_at FROM sessions WHERE id = {_PH}", (session_id,)
        ).fetchone()

    if not records or not session or len(records) < 10:
        return []

    session_start = session['started_at'] if isinstance(session, dict) else session[0]
    window_sec = window_min * 60
    fatigue_events = []

    # Slide a window across the timeline
    for i in range(len(records)):
        t_current = records[i]['timestamp']

        # Gather scores in [t - window, t] (before) and [t, t + window] (after)
        before_scores = [
            r['attention_score'] for r in records
            if t_current - window_sec <= r['timestamp'] <= t_current
        ]
        after_scores = [
            r['attention_score'] for r in records
            if t_current <= r['timestamp'] <= t_current + window_sec
        ]

        if len(before_scores) < 5 or len(after_scores) < 5:
            continue

        avg_before = sum(before_scores) / len(before_scores)
        avg_after = sum(after_scores) / len(after_scores)
        drop = avg_before - avg_after

        if drop >= drop_threshold and avg_before >= THRESHOLD_FOCUSED:
            minutes_in = (t_current - session_start) / 60

            # Avoid duplicate events close together (within 2 minutes)
            if fatigue_events and (minutes_in - fatigue_events[-1]['minutes_into_session']) < 2:
                continue

            fatigue_events.append({
                "onset_time": t_current,
                "minutes_into_session": round(minutes_in, 1),
                "score_before": round(avg_before, 3),
                "score_after": round(avg_after, 3),
                "drop": round(drop, 3),
            })

    return fatigue_events


# ============================================================
# Recovery Detection
# ============================================================

def detect_recovery(student_id: int, session_id: int,
                    min_distracted_sec: float = 10,
                    min_recovery_score: float = None) -> List[Dict]:
    """
    Detect self-correction events: when a student transitions from
    Distracted → Focused without teacher intervention.

    Args:
        min_distracted_sec: Minimum duration of distraction before counting recovery.
        min_recovery_score: Minimum score after recovery (defaults to THRESHOLD_FOCUSED).

    Returns list of recovery events:
        [{ "distracted_start": float, "recovery_time": float,
           "distracted_duration_sec": float, "recovery_score": float,
           "minutes_into_session": float }, ...]
    """
    if min_recovery_score is None:
        min_recovery_score = THRESHOLD_FOCUSED

    spans = detect_attention_spans(student_id, session_id)

    # Find session start time
    with get_db() as conn:
        session = conn.execute(
            f"SELECT started_at FROM sessions WHERE id = {_PH}", (session_id,)
        ).fetchone()

    session_start = 0
    if session:
        session_start = session['started_at'] if isinstance(session, dict) else session[0]

    recoveries = []

    for i in range(len(spans) - 1):
        current = spans[i]
        next_span = spans[i + 1]

        # Look for Distracted → Focused transitions
        if (current['state'] == 'Distracted' and
            next_span['state'] == 'Focused' and
            current['duration_sec'] >= min_distracted_sec and
                next_span['avg_score'] >= min_recovery_score):

            recoveries.append({
                "distracted_start": current['start'],
                "recovery_time": next_span['start'],
                "distracted_duration_sec": current['duration_sec'],
                "recovery_score": next_span['avg_score'],
                "minutes_into_session": round(
                    (next_span['start'] - session_start) / 60, 1
                ),
            })

    return recoveries


# ============================================================
# Class-Wide Engagement Trends
# ============================================================

def get_class_trends(session_id: int, bucket_minutes: float = 5.0) -> List[Dict]:
    """
    Aggregate class attention scores into time buckets to identify
    when attention typically drops or peaks.

    Returns list of time buckets:
        [{ "minute_start": float, "minute_end": float,
           "avg_score": float, "min_score": float, "max_score": float,
           "sample_count": int }, ...]
    """
    with get_db() as conn:
        session = conn.execute(
            f"SELECT started_at, ended_at FROM sessions WHERE id = {_PH}",
            (session_id,)
        ).fetchone()

        records = _fetchall(conn,
            f"""SELECT attention_score, timestamp
                FROM attention_records
                WHERE session_id = {_PH}
                ORDER BY timestamp ASC""",
            (session_id,)
        )

    if not session or not records:
        return []

    if isinstance(session, dict):
        session_start = session['started_at']
    else:
        session_start = session[0]

    bucket_sec = bucket_minutes * 60
    buckets = []
    current_bucket_start = 0  # relative minutes
    current_scores = []

    for r in records:
        relative_time = r['timestamp'] - session_start
        bucket_idx = int(relative_time / bucket_sec)
        bucket_start_min = bucket_idx * bucket_minutes

        if bucket_start_min != current_bucket_start and current_scores:
            buckets.append({
                "minute_start": current_bucket_start,
                "minute_end": current_bucket_start + bucket_minutes,
                "avg_score": round(sum(current_scores) / len(current_scores), 3),
                "min_score": round(min(current_scores), 3),
                "max_score": round(max(current_scores), 3),
                "sample_count": len(current_scores),
            })
            current_scores = []
            current_bucket_start = bucket_start_min

        current_scores.append(r['attention_score'])

    # Final bucket
    if current_scores:
        buckets.append({
            "minute_start": current_bucket_start,
            "minute_end": current_bucket_start + bucket_minutes,
            "avg_score": round(sum(current_scores) / len(current_scores), 3),
            "min_score": round(min(current_scores), 3),
            "max_score": round(max(current_scores), 3),
            "sample_count": len(current_scores),
        })

    return buckets


# ============================================================
# Student Engagement Profile
# ============================================================

def get_student_engagement_profile(student_id: int, session_id: int) -> Dict:
    """
    Analyze a student's engagement pattern across a session.

    Returns:
        {
            "profile": "steady" | "early-fader" | "late-bloomer" | "fluctuating",
            "avg_first_half": float,
            "avg_second_half": float,
            "longest_focus_span_min": float,
            "total_distracted_pct": float,
            "recovery_count": int,
            "fatigue_events": int,
        }
    """
    query = f"""
        SELECT attention_score, status, timestamp
        FROM attention_records
        WHERE student_id = {_PH} AND session_id = {_PH}
        ORDER BY timestamp ASC
    """

    with get_db() as conn:
        records = _fetchall(conn, query, (student_id, session_id))

    if not records or len(records) < 4:
        return {"profile": "insufficient_data"}

    scores = [r['attention_score'] for r in records]
    mid = len(scores) // 2

    avg_first_half = sum(scores[:mid]) / mid
    avg_second_half = sum(scores[mid:]) / (len(scores) - mid)

    # Determine profile
    diff = avg_second_half - avg_first_half
    if abs(diff) < 0.08:
        profile = "steady"
    elif diff < -0.08:
        profile = "early-fader"
    elif diff > 0.08:
        profile = "late-bloomer"
    else:
        profile = "fluctuating"

    # Longest focus span
    spans = detect_attention_spans(student_id, session_id)
    focus_spans = [s for s in spans if s['state'] == 'Focused']
    longest_focus = max((s['duration_sec'] for s in focus_spans), default=0)

    # Total distracted percentage
    total_duration = records[-1]['timestamp'] - records[0]['timestamp']
    distracted_duration = sum(
        s['duration_sec'] for s in spans if s['state'] in ('Distracted', 'Absent', 'Phone Use')
    )
    distracted_pct = (distracted_duration / total_duration * 100) if total_duration > 0 else 0

    # Recovery and fatigue counts
    recoveries = detect_recovery(student_id, session_id)
    fatigue_events = detect_fatigue_onset(student_id, session_id)

    return {
        "profile": profile,
        "avg_first_half": round(avg_first_half, 3),
        "avg_second_half": round(avg_second_half, 3),
        "longest_focus_span_min": round(longest_focus / 60, 1),
        "total_distracted_pct": round(distracted_pct, 1),
        "recovery_count": len(recoveries),
        "fatigue_events": len(fatigue_events),
    }


# ============================================================
# Full Temporal Analysis (Combines All)
# ============================================================

def get_temporal_analysis(session_id: int) -> Dict:
    """
    Run full temporal analysis for a session.
    Returns combined insights for all students and the class.
    """
    with get_db() as conn:
        students = _fetchall(conn,
            f"SELECT id, name FROM students WHERE session_id = {_PH}",
            (session_id,)
        )

    if not students:
        return {"session_id": session_id, "students": [], "class_trends": []}

    class_trends = get_class_trends(session_id)

    # Find when class attention typically drops
    drop_minute = None
    if class_trends:
        sorted_buckets = sorted(class_trends, key=lambda b: b['avg_score'])
        if sorted_buckets:
            drop_minute = sorted_buckets[0]['minute_start']

    student_analyses = []
    for student in students:
        profile = get_student_engagement_profile(student['id'], session_id)
        fatigue = detect_fatigue_onset(student['id'], session_id)
        recoveries = detect_recovery(student['id'], session_id)

        student_analyses.append({
            "name": student['name'],
            "student_id": student['id'],
            "profile": profile,
            "fatigue_events": fatigue,
            "recovery_events": recoveries,
        })

    # Generate insights
    insights = []
    if drop_minute is not None:
        insights.append(
            f"Class attention typically drops lowest around minute {round(drop_minute)}."
        )

    early_faders = [s for s in student_analyses if s['profile'].get('profile') == 'early-fader']
    if early_faders:
        names = ', '.join(s['name'] for s in early_faders[:3])
        insights.append(f"Early faders (attention drops in second half): {names}")

    total_recoveries = sum(len(s['recovery_events']) for s in student_analyses)
    if total_recoveries:
        insights.append(
            f"{total_recoveries} self-correction event(s) detected across all students."
        )

    return {
        "session_id": session_id,
        "class_trends": class_trends,
        "attention_drop_minute": drop_minute,
        "students": student_analyses,
        "insights": insights,
    }
