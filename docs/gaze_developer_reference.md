# Gaze Attention Monitoring System — Developer & Context Reference

This document is a high-density technical reference of the Gaze codebase, mapping out the architecture, database schema, mathematical formulas, classification algorithms, and endpoints. It is designed to serve as a complete standalone context guide to minimize token usage in future development sessions.

---

## 1. System Architecture & Data Flow

```
[Student Client (Browser)]
  │  ├─ MediaPipe Face Mesh (478 Landmarks)
  │  ├─ 6-Point Perspective-n-Point (solvePnP) Head Pose Euler Solver
  │  ├─ Gaze Vector / Pupil Deviation Estimator
  │  ├─ Calibration Medians & Environment Quality Pre-Check
  │  └─ 5-State Classifier (Focused, Distracted, Drowsy, Phone Use, Absent)
  │
  ▼ (Every 2 seconds - Attention Score & Raw Metrics)
[Flask & Socket.IO Backend Server]
  │  ├─ DB Buffered Writer (Flushes every 200 records or 10 seconds)
  │  ├─ Rolling Average Deque (5 minutes / 150 samples per student)
  │  ├─ Adaptive Baseline Deviation (Alerts if student score drops > 20% below avg)
  │  ├─ Temporal Pattern Analyzer (Fatigue windows, recovery events, trends)
  │  └─ Gemini-1.5 / Rule-Based Summary Engine
  │
  ▼ (Real-time WebSockets & REST API)
[Teacher Dashboard (Browser)]
  ├─ Student Tiles (Video feeds, real-time status badges, deviation meters)
  └─ Session History (Interactive charts, temporal drop indicators, PDF downloader)
```

---

## 2. Database Schema (SQLite / PostgreSQL Compatible)

### Sessions
```sql
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, -- SERIAL in PG
    room_id TEXT NOT NULL,
    teacher_name TEXT DEFAULT '',
    session_name TEXT DEFAULT '',
    started_at REAL NOT NULL,             -- DOUBLE PRECISION in PG
    ended_at REAL,
    is_active INTEGER DEFAULT 1           -- BOOLEAN/INT
);
```

### Students
```sql
CREATE TABLE students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    session_id INTEGER NOT NULL,
    joined_at REAL NOT NULL,
    left_at REAL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE INDEX idx_students_session ON students(session_id);
```

### Attention Records
```sql
CREATE TABLE attention_records (
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
CREATE INDEX idx_attention_session ON attention_records(session_id);
CREATE INDEX idx_attention_student ON attention_records(student_id);
```

### Annotations
```sql
CREATE TABLE annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    teacher_name TEXT DEFAULT '',
    text TEXT NOT NULL,
    annotation_type TEXT DEFAULT 'note', -- 'note', 'alert'
    timestamp REAL NOT NULL,
    class_avg_at_time REAL DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE INDEX idx_annotations_session ON annotations(session_id);
```

---

## 3. Configuration & Parameter Thresholds

Config parameters mapped in `config.py` and matched on the frontend `zoom_app/attention.js`:

```python
# Score Weights (Sum to 1.0)
WEIGHT_GAZE = 0.35
WEIGHT_HEAD_POSE = 0.30
WEIGHT_EYE_OPENNESS = 0.25
WEIGHT_FACE_PRESENCE = 0.10

# Attention Classification
THRESHOLD_FOCUSED = 0.70          # Score >= 70% -> Focused
THRESHOLD_PARTIAL = 0.40          # 40% <= Score < 70% -> Partially Attentive
                                  # Score < 40% -> Distracted

# Eye Openness (EAR)
EAR_THRESHOLD_CLOSED = 0.18       # EAR < 0.18 -> blink/closed
EAR_THRESHOLD_OPEN = 0.25         # EAR > 0.25 -> fully open
BLINK_CONSECUTIVE_FRAMES = 3      # Frames to register a blink

# Head Pose Euler Angles (Degrees)
HEAD_YAW_THRESHOLD = 30           # Horizontal turn limit
HEAD_PITCH_THRESHOLD = 25         # Vertical tilt limit

# Multi-State Detection
DROWSY_EAR_THRESHOLD = 0.22       # Rolling avg EAR threshold
DROWSY_BLINK_RATE_MIN = 25        # Blinks/min to classify drowsy
DROWSY_HEAD_PITCH_MIN = 10        # Minimum pitch (drooping forward) in degrees
PHONE_USE_PITCH_THRESHOLD = -15    # Pitch threshold (looking down) in degrees
ABSENT_NO_FACE_SECONDS = 3        # Duration without face before marking Absent

# Alerting
ALERT_THRESHOLD = 0.35            # Drop below 35% overall triggers alert
ALERT_DEVIATION_THRESHOLD = 0.20  # 20% drop below rolling average triggers alert
ALERT_COOLDOWN = 30               # Alert throttle per student (seconds)
```

---

## 4. Feature Extraction & Attention Scores

### A. Eye Openness (EAR)
Computed using eye landmarks to capture height-to-width ratio:
$$\text{EAR} = \frac{\|\mathbf{p}_2 - \mathbf{p}_6\| + \|\mathbf{p}_3 - \mathbf{p}_5\|}{2 \|\mathbf{p}_1 - \mathbf{p}_4\|}$$
*   **Left Eye Indices**: $\mathbf{p}_1$: 362, $\mathbf{p}_2$: 385, $\mathbf{p}_3$: 387, $\mathbf{p}_4$: 263, $\mathbf{p}_5$: 373, $\mathbf{p}_6$: 380
*   **Right Eye Indices**: $\mathbf{p}_1$: 33, $\mathbf{p}_2$: 160, $\mathbf{p}_3$: 158, $\mathbf{p}_4$: 133, $\mathbf{p}_5$: 153, $\mathbf{p}_6$: 144
*   **Normalizer**: Normalized score linearly maps current EAR between `EAR_THRESHOLD_CLOSED` (0.0) and `EAR_THRESHOLD_OPEN` (1.0).

### B. Gaze Tracking
Uses horizontal iris displacement and vertical iris-eyelid ratios.
*   **Horizontal Ratio ($G_H$)**: Ratio of distance from pupil center to left eye corner versus the overall width:
    $$G_H = \frac{\|\mathbf{p}_{\text{iris}} - \mathbf{p}_{\text{corner1}}\|}{\|\mathbf{p}_{\text{corner2}} - \mathbf{p}_{\text{corner1}}\|}$$
    Expected neutral $\approx 0.5$. Deviations from baseline center are mapped.
*   **Vertical Ratio ($G_V$)**: Eyelid top/bottom midpoints estimate vertical iris ratio:
    $$\text{Top}_Y = \frac{\mathbf{p}_{385}.y + \mathbf{p}_{387}.y}{2}, \quad \text{Bottom}_Y = \frac{\mathbf{p}_{373}.y + \mathbf{p}_{380}.y}{2}$$
    $$G_V = \frac{\mathbf{p}_{\text{iris}}.y - \text{Top}_Y}{\text{Bottom}_Y - \text{Top}_Y}$$
*   **Deviation Score**: Calculated as deviation from calibrated baseline:
    $$\text{Dev} = \sqrt{(G_H - B_H)^2 + (G_V - B_V)^2}$$
    If $\text{Dev} \le \text{GAZE\_THRESHOLD}$, score = 1.0; else decays linearly to 0.0.

### C. Head Pose Estimation (Perspective-n-Point / solvePnP)
Employs standard 3D generic facial points mapped to 2D image coordinates:
1.  **3D Points**: Nose Tip (0,0,0), Chin (0,-330,-65), Left Eye Left Corner (-225,170,-135), Right Eye Right Corner (225,170,-135), Left Mouth Corner (-150,-150,-125), Right Mouth Corner (150,-150,-125).
2.  **2D Landmarks**: Corresponding indices [1, 152, 263, 33, 287, 57].
3.  **Solver**: Estimates rotation vector ($\mathbf{r}_{\text{vec}}$) and translation vector ($\mathbf{t}_{\text{vec}}$). Rotation vector is decomposed into Euler angles: **Yaw** ($\theta_Y$) and **Pitch** ($\theta_P$).
4.  **Score**:
    $$\text{HeadScore} = \max\left(0, 1 - \frac{|\theta_Y - B_Y|}{\text{HEAD\_YAW\_THRESHOLD}} - \frac{|\theta_P - B_P|}{\text{HEAD\_PITCH\_THRESHOLD}}\right)$$

### D. Composite Attention Score
$$\text{AttentionScore} = w_g \cdot \text{Gaze} + w_h \cdot \text{HeadScore} + w_e \cdot \text{EyeOpenness} + w_f \cdot \text{Presence}$$

---

## 5. Multi-Factor Attention States Classifier

Both client-side (`attention.js`) and server-side (`attention_detector.py`) evaluate states sequentially:

```javascript
function classifyAttentionState(metrics) {
    if (!metrics.face_detected) {
        return "Absent";
    }
    
    // 1. Absent threshold
    if (metrics.consecutive_no_face >= threshold) {
        return "Absent";
    }

    // 2. Drowsy detection (Multiple factors)
    let drowsyFactors = 0;
    if (metrics.ear_rolling_avg < DROWSY_EAR_THRESHOLD) drowsyFactors++;
    if (metrics.blink_rate >= DROWSY_BLINK_RATE_MIN) drowsyFactors++;
    if (metrics.head_pitch > DROWSY_HEAD_PITCH_MIN) drowsyFactors++; // Drooping head
    
    if (drowsyFactors >= 2) {
        return "Drowsy";
    }

    // 3. Phone Use detection
    if (metrics.head_pitch < PHONE_USE_PITCH_THRESHOLD && metrics.attention_score < 0.70) {
        return "Phone Use";
    }

    // 4. Default Score-based states
    if (metrics.attention_score >= THRESHOLD_FOCUSED) {
        return "Focused";
    } else if (metrics.attention_score >= THRESHOLD_PARTIAL) {
        return "Partially Attentive";
    } else {
        return "Distracted";
    }
}
```

---

## 6. Calibration & Pre-Checks

### Per-Student Calibration Flow
*   Student looks at center screen for 5 seconds (15 FPS, ~75 frames).
*   Metrics (gaze, head yaw/pitch, EAR) are gathered.
*   **Baseline Calculations**:
    -   `gazeCenterH` / `gazeCenterV` = Median horizontal/vertical gaze values.
    -   `headYaw` / `headPitch` = Median head angles.
    -   `earOpen` = 90th percentile of captured EAR samples.
*   All subsequent calculations compute deviation relative to these baselines.

### Environment Pre-Check
Runs on start-up. Performs:
-   **Brightness**: Mean pixel value of luminance ($Y = 0.299R + 0.587G + 0.114B$). Warns if $Y < 50$ (too dark) or $Y > 230$ (too bright).
-   **Contrast**: Standard deviation of pixel luminance. Warns if SD $< 20$ (flat image/low contrast).
-   **Distance**: Bounding box size as ratio of frame area. Warns if $< 15\%$ (too far) or $> 80\%$ (too close). (Computed via landmark spatial span ratios).
-   **Camera Angle**: Yaw/pitch at neutral posture. Warns if initial yaw $> 20^\circ$ or pitch $> 15^\circ$ (poor camera placement).

---

## 7. Server-Side Intelligence

### Adaptive Thresholding & Alerts
*   Each student's score updates are fed into a sliding `rolling_scores` deque (max size 150, equivalent to ~5 minutes).
*   `personal_baseline` is the median/average of these scores (calculated once there are at least 10 samples).
*   **Alert Condition**: Triggers if:
    $$\text{Score} < \text{personal\_baseline} \times (1 - \text{ALERT\_DEVIATION\_THRESHOLD})$$
    and is throttled by `ALERT_COOLDOWN` (30 seconds).

### Temporal Pattern Analysis (`backend/temporal_analysis.py`)
-   **Attention Spans**: Groups contiguous records of the same status, returning startTime, endTime, and duration.
-   **Fatigue Onset**: Detects drops in attention score by comparing a sliding window of 5 minutes *before* vs. 5 minutes *after* a frame timestamp. Flags drop if:
    $$\text{Average}_{\text{Before}} \ge \text{THRESHOLD\_FOCUSED} \quad \text{and} \quad \text{Average}_{\text{Before}} - \text{Average}_{\text{After}} \ge 0.15$$
-   **Recovery**: Identifies Transitions from `Distracted` (duration $\ge 10$ seconds) straight to `Focused` (score $\ge 0.70$), indicating self-correction.
-   **Engagement Profiles**:
    -   *Steady*: Second-half attention average is within $\pm 8\%$ of the first half.
    -   *Early Fader*: Second-half average is $> 8\%$ lower than first half.
    -   *Late Bloomer*: Second-half average is $> 8\%$ higher than first half.
    -   *Fluctuating*: Standard deviation is high, toggling status frequently.

---

## 8. WebSockets & REST APIs

### Socket.IO Event Mappings
*   **Client Emits**:
    -   `join_session`: `{"room_id": str, "name": str, "role": "student"|"teacher"}`
    -   `submit_score`: `{"score": float, "status": str, "gaze": float, "head": float, "eye": float}`
    -   `calibration_complete`: `{"baseline": object}`
*   **Server Broadcasts**:
    -   `score_update`: `{"name": str, "score": float, "status": str, "personal_baseline": float, "deviation": float}` (to teacher)
    -   `distraction_alert`: `{"name": str, "score": float, "deviation": float}` (to teacher)
    -   `class_average_update`: `{"average": float}` (to all)

### Core REST Endpoints
*   `POST /api/sessions/create`: Create classroom session.
*   `GET /api/sessions/active`: List current live channels.
*   `GET /sessions/<id>/report.pdf`: Generate ReportLab PDF (includes statistics, 5-state distribution tables, temporal trends, annotations).
*   `GET /sessions/<id>/temporal-analysis`: Return class trends, fatigue onsets, recoveries, and student profiles.
*   `POST /api/sessions/<id>/annotations`: Store teacher note/alert bookmark.
