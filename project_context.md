# Gaze — Consolidated Project Context

This file serves as a unified context document for the Gaze Attention Monitoring System to minimize token usage during LLM interactions. It outlines the codebase layout, system architecture, database schema, and core algorithms.

---

## 📂 Repository Structure

- `backend/`
  - [server.py](file:///Users/adamsaad/Desktop/Gaze/backend/server.py): Flask-SocketIO backend server handling rooms, signaling, and analytics.
  - [database.py](file:///Users/adamsaad/Desktop/Gaze/backend/database.py): SQLite schema, connection wrappers, and query functions.
  - [temporal_analysis.py](file:///Users/adamsaad/Desktop/Gaze/backend/temporal_analysis.py): Time-series metrics (attention span, fatigue, recovery, and insights).
- `zoom_app/` (Student App)
  - [index.html](file:///Users/adamsaad/Desktop/Gaze/zoom_app/index.html): Student classroom interface (webcam view, charts, and metrics).
  - [app.js](file:///Users/adamsaad/Desktop/Gaze/zoom_app/app.js): LiveKit client-side conference management and Socket.IO hooks.
  - [attention.js](file:///Users/adamsaad/Desktop/Gaze/zoom_app/attention.js): Browser-side MediaPipe Face Mesh processing, calibration, and scoring.
- `teacher_dashboard/` (Teacher App)
  - [index.html](file:///Users/adamsaad/Desktop/Gaze/teacher_dashboard/index.html): Real-time monitor, charts, student lists, alerts, and PDF report triggers.
  - [app.js](file:///Users/adamsaad/Desktop/Gaze/teacher_dashboard/app.js): Live dashboard view state, Chart.js updates, and management controls.
- [config.py](file:///Users/adamsaad/Desktop/Gaze/config.py): App configurations (ports, weights, thresholds, limits, LiveKit keys).
- [evaluate_algorithm.py](file:///Users/adamsaad/Desktop/Gaze/evaluate_algorithm.py): Validation script for testing weights via guided recordings.

---

## ⚙️ Configuration & Weights (`config.py`)

Attention scores ($0.0$ to $1.0$) are calculated locally on the student client or in python evaluations:
- **Gaze Score Weight**: `0.35`
- **Head Pose Weight**: `0.30`
- **Eye Openness Weight**: `0.25`
- **Face Presence Weight**: `0.10`

### Thresholds
- **Focused State**: $\ge 0.70$
- **Partially Attentive State**: $0.40$ to $0.69$
- **Distracted State**: $< 0.40$

---

## 👁️ Core Scoring Algorithms

### 1. Browser-Side scoring (`zoom_app/attention.js`)
```javascript
calculateScores(faceDetected, ear, gaze, headPose) {
    if (!faceDetected) return { gaze_score: 0, head_pose_score: 0, eye_openness: 0, face_presence: 0, attention_score: 0 };

    const gazeCenterH = this.baseline ? this.baseline.gazeCenterH : 0.5;
    const gazeCenterV = this.baseline ? this.baseline.gazeCenterV : 0.5;
    const baseYaw = this.baseline ? this.baseline.headYaw : 0;
    const basePitch = this.baseline ? this.baseline.headPitch : 0;
    const baseEar = this.baseline ? this.baseline.earOpen : 0.25;

    // Eye Openness
    const earThresholdClosed = this.baseline ? Math.max(0.10, baseEar * 0.65) : 0.18;
    const earThresholdOpen = this.baseline ? baseEar * 0.90 : 0.25;
    let eyeOpenness = ear < earThresholdClosed ? 0 : ear > earThresholdOpen ? 1 : (ear - earThresholdClosed) / (earThresholdOpen - earThresholdClosed);

    // Gaze Score (Deviation from calibrated center)
    const hDeviation = Math.abs(gaze.horizontal - gazeCenterH) * 2;
    const vDeviation = Math.abs(gaze.vertical - gazeCenterV) * 2.5; // High vertical sensitivity
    const maxGazeDeviation = Math.max(hDeviation, vDeviation);
    const gazeScore = Math.max(0, 1 - maxGazeDeviation);

    // Head Pose (Deviation from calibrated neutral)
    const yawDeviation = Math.abs(headPose.yaw - baseYaw);
    const pitchDeviation = Math.abs(headPose.pitch - basePitch);
    const yawScore = Math.max(0, 1 - yawDeviation / 30); // 30 deg threshold
    const pitchScore = Math.max(0, 1 - pitchDeviation / 25); // 25 deg threshold
    const headPoseScore = (yawScore + pitchScore) / 2;

    const attentionScore = (0.35 * gazeScore) + (0.30 * headPoseScore) + (0.25 * eyeOpenness) + (0.10 * 1.0);
    return { gaze_score: gazeScore, head_pose_score: headPoseScore, eye_openness: eyeOpenness, face_presence: 1.0, attention_score: attentionScore };
}
```

### 2. Multi-State Status Classification (`zoom_app/attention.js`)
- **Absent**: Triggered when no face is detected for 3 consecutive seconds.
- **Phone Use**: Pitch is tilted downward ($headPose.pitch > 15$ degrees) while the overall score is $< 0.70$.
- **Drowsy**: Triggered when at least 2 of 3 signals are met:
  1. Average EAR over 10 frames $< 0.22$
  2. Blinks per minute $> 25$
  3. Head pitch $> 10$ degrees
- **Focused / Partial / Distracted**: Normal score thresholds.

---

## 🗄️ Database Schema (`backend/database.py`)

- **sessions**:
  - `id` (INTEGER, PK), `room_id` (TEXT, unique code), `teacher_name` (TEXT), `session_name` (TEXT), `created_at` (TIMESTAMP), `ended_at` (TIMESTAMP).
- **students**:
  - `id` (INTEGER, PK), `session_id` (INTEGER, FK), `name` (TEXT), `sid` (TEXT, socket identifier), `joined_at` (TIMESTAMP), `left_at` (TIMESTAMP).
- **attention_records**:
  - `id` (INTEGER, PK), `student_id` (INTEGER, FK), `session_id` (INTEGER, FK), `attention_score` (REAL), `status` (TEXT), `timestamp` (TIMESTAMP).
- **annotations**:
  - `id` (INTEGER, PK), `session_id` (INTEGER, FK), `text` (TEXT), `author` (TEXT), `annotation_type` (TEXT: note, bookmark, warning, praise), `timestamp` (TIMESTAMP), `class_avg_at_timestamp` (REAL).

---

## 🔍 Frequently Asked Questions

### 1. Does closeness to the camera affect attention?
Closeness does not directly lower the attention score. However, moving too close might:
- Push eyes or face landmarks out of frame bounds, triggering an **Absent** (0%) status.
- Skew eye/iris aspect ratio due to perspective distortion.
- Students are guided by a proximity alert if their face-to-frame ratio exceeds `0.65`.

### 2. Why does looking down sometimes give a high score?
- **Calibration baseline**: If the student looked down while calibrating, looking down is set as the focused neutral state.
- **Camera angle**: Laptop cameras positioned below eye level can align perfectly with eyes/face when the user tilts down slightly.
- **Thresholds**: Normal pitch tilts within $\pm 25$ degrees are tolerated to allow normal screen interactions.
