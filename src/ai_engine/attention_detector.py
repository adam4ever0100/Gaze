"""
Attention Detector using MediaPipe Face Mesh.

This module provides real-time attention detection by analyzing:
- Face presence and detection confidence
- Eye Aspect Ratio (EAR) for eye openness and blink detection
- Head pose estimation (yaw, pitch, roll)
- Gaze direction based on iris position

All processing happens locally - no video is stored or transmitted.
"""

import cv2
import numpy as np
import mediapipe as mp
from collections import deque
from typing import Dict, Optional, Tuple
import time

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config import (
    WEIGHT_GAZE, WEIGHT_HEAD_POSE, WEIGHT_EYE_OPENNESS, WEIGHT_FACE_PRESENCE,
    EAR_THRESHOLD_CLOSED, EAR_THRESHOLD_OPEN, BLINK_CONSECUTIVE_FRAMES,
    HEAD_YAW_THRESHOLD, HEAD_PITCH_THRESHOLD, GAZE_THRESHOLD,
    THRESHOLD_FOCUSED, THRESHOLD_PARTIAL
)


class AttentionDetector:
    """
    Real-time attention detection using MediaPipe Face Mesh.
    
    Calculates attention score based on:
    - 35% Gaze direction
    - 30% Head pose (looking toward screen)
    - 25% Eye openness
    - 10% Face presence
    """
    
    # MediaPipe Face Mesh landmark indices
    # Left eye landmarks
    LEFT_EYE = [362, 385, 387, 263, 373, 380]
    LEFT_IRIS = [469, 470, 471, 472]
    LEFT_EYE_INNER = 362
    LEFT_EYE_OUTER = 263
    
    # Right eye landmarks
    RIGHT_EYE = [33, 160, 158, 133, 153, 144]
    RIGHT_IRIS = [474, 475, 476, 477]
    RIGHT_EYE_INNER = 133
    RIGHT_EYE_OUTER = 33
    
    # 3D model points for head pose estimation
    MODEL_POINTS = np.array([
        (0.0, 0.0, 0.0),          # Nose tip
        (0.0, -330.0, -65.0),     # Chin
        (-225.0, 170.0, -135.0),  # Left eye left corner
        (225.0, 170.0, -135.0),   # Right eye right corner
        (-150.0, -150.0, -125.0), # Left mouth corner
        (150.0, -150.0, -125.0)   # Right mouth corner
    ], dtype=np.float64)
    
    # Landmark indices for head pose (nose tip, chin, eye corners, mouth corners)
    POSE_LANDMARKS = [1, 152, 263, 33, 287, 57]
    
    def __init__(self):
        """Initialize the attention detector with MediaPipe Face Mesh."""
        self.mp_face_mesh = mp.solutions.face_mesh
        self.face_mesh = self.mp_face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=True,  # Enables iris landmarks
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5
        )
        
        # Blink detection state
        self.blink_counter = 0
        self.total_blinks = 0
        self.blink_start_time = time.time()
        self.ear_history = deque(maxlen=10)
        
        # Smoothing for scores
        self.score_history = deque(maxlen=5)
        
        # Current metrics
        self.current_metrics: Dict = {
            "face_detected": False,
            "attention_score": 0.0,
            "status": "Unknown",
            "gaze_score": 0.0,
            "head_pose_score": 0.0,
            "eye_openness": 0.0,
            "face_presence": 0.0,
            "blink_rate": 0.0,
            "head_yaw": 0.0,
            "head_pitch": 0.0,
            "head_roll": 0.0,
            "left_ear": 0.0,
            "right_ear": 0.0,
        }
        
        # Camera matrix placeholder (updated with frame dimensions)
        self.camera_matrix = None
        self.dist_coeffs = np.zeros((4, 1))
    
    def _calculate_ear(self, eye_landmarks: list, landmarks, img_w: int, img_h: int) -> float:
        """
        Calculate Eye Aspect Ratio (EAR) for blink/openness detection.
        
        EAR = (|p2-p6| + |p3-p5|) / (2 * |p1-p4|)
        
        Higher EAR = more open eyes
        Lower EAR = closed/closing eyes
        """
        points = []
        for idx in eye_landmarks:
            lm = landmarks[idx]
            points.append((lm.x * img_w, lm.y * img_h))
        
        # Vertical distances
        v1 = np.linalg.norm(np.array(points[1]) - np.array(points[5]))
        v2 = np.linalg.norm(np.array(points[2]) - np.array(points[4]))
        
        # Horizontal distance
        h = np.linalg.norm(np.array(points[0]) - np.array(points[3]))
        
        if h == 0:
            return 0.0
        
        ear = (v1 + v2) / (2.0 * h)
        return ear
    
    def _calculate_gaze(self, landmarks, img_w: int, img_h: int) -> Tuple[float, float]:
        """
        Calculate gaze direction based on iris position relative to eye corners.
        
        Returns:
            Tuple of (horizontal_ratio, vertical_ratio) where:
            - 0.5, 0.5 = looking straight
            - < 0.5 = looking left/up
            - > 0.5 = looking right/down
        """
        def get_iris_center(iris_landmarks):
            x = sum(landmarks[i].x for i in iris_landmarks) / len(iris_landmarks)
            y = sum(landmarks[i].y for i in iris_landmarks) / len(iris_landmarks)
            return x * img_w, y * img_h
        
        def get_eye_bounds(inner_idx, outer_idx):
            inner = (landmarks[inner_idx].x * img_w, landmarks[inner_idx].y * img_h)
            outer = (landmarks[outer_idx].x * img_w, landmarks[outer_idx].y * img_h)
            return inner, outer
        
        # Get iris centers
        left_iris_center = get_iris_center(self.LEFT_IRIS)
        right_iris_center = get_iris_center(self.RIGHT_IRIS)
        
        # Get eye bounds
        left_inner, left_outer = get_eye_bounds(self.LEFT_EYE_INNER, self.LEFT_EYE_OUTER)
        right_inner, right_outer = get_eye_bounds(self.RIGHT_EYE_INNER, self.RIGHT_EYE_OUTER)
        
        # Calculate horizontal gaze ratio for each eye
        def horizontal_ratio(iris_center, inner, outer):
            eye_width = abs(outer[0] - inner[0])
            if eye_width == 0:
                return 0.5
            iris_pos = iris_center[0] - min(inner[0], outer[0])
            return iris_pos / eye_width
        
        left_h_ratio = horizontal_ratio(left_iris_center, left_inner, left_outer)
        right_h_ratio = horizontal_ratio(right_iris_center, right_inner, right_outer)
        
        # Average horizontal gaze
        h_gaze = (left_h_ratio + right_h_ratio) / 2
        
        # For vertical, use eye landmark positions
        # Simplified: just use the iris center relative to eye center
        v_gaze = 0.5  # Assume center for now
        
        return h_gaze, v_gaze
    
    def _estimate_head_pose(self, landmarks, img_w: int, img_h: int) -> Tuple[float, float, float]:
        """
        Estimate head pose (yaw, pitch, roll) using solvePnP.
        
        Returns:
            Tuple of (yaw, pitch, roll) in degrees
        """
        # Initialize camera matrix if needed
        if self.camera_matrix is None or self.camera_matrix[0, 2] != img_w / 2:
            focal_length = img_w
            center = (img_w / 2, img_h / 2)
            self.camera_matrix = np.array([
                [focal_length, 0, center[0]],
                [0, focal_length, center[1]],
                [0, 0, 1]
            ], dtype=np.float64)
        
        # Get 2D image points
        image_points = np.array([
            (landmarks[idx].x * img_w, landmarks[idx].y * img_h)
            for idx in self.POSE_LANDMARKS
        ], dtype=np.float64)
        
        # Solve PnP
        success, rotation_vec, translation_vec = cv2.solvePnP(
            self.MODEL_POINTS,
            image_points,
            self.camera_matrix,
            self.dist_coeffs,
            flags=cv2.SOLVEPNP_ITERATIVE
        )
        
        if not success:
            return 0.0, 0.0, 0.0
        
        # Convert rotation vector to rotation matrix
        rotation_mat, _ = cv2.Rodrigues(rotation_vec)
        
        # Get Euler angles
        pose_mat = cv2.hconcat([rotation_mat, translation_vec])
        _, _, _, _, _, _, euler_angles = cv2.decomposeProjectionMatrix(pose_mat)
        
        yaw = euler_angles[1, 0]
        pitch = euler_angles[0, 0]
        roll = euler_angles[2, 0]
        
        return yaw, pitch, roll
    
    def _calculate_scores(
        self,
        face_detected: bool,
        ear: float,
        gaze: Tuple[float, float],
        head_pose: Tuple[float, float, float]
    ) -> Dict[str, float]:
        """
        Calculate individual component scores and final attention score.
        """
        if not face_detected:
            return {
                "gaze_score": 0.0,
                "head_pose_score": 0.0,
                "eye_openness": 0.0,
                "face_presence": 0.0,
                "attention_score": 0.0
            }
        
        # Face presence score (binary for now, but detection confidence could be used)
        face_presence = 1.0
        
        # Eye openness score (normalized EAR)
        if ear < EAR_THRESHOLD_CLOSED:
            eye_openness = 0.0
        elif ear > EAR_THRESHOLD_OPEN:
            eye_openness = 1.0
        else:
            eye_openness = (ear - EAR_THRESHOLD_CLOSED) / (EAR_THRESHOLD_OPEN - EAR_THRESHOLD_CLOSED)
        
        # Gaze score (how centered is the gaze)
        h_gaze, v_gaze = gaze
        h_deviation = abs(h_gaze - 0.5) * 2  # 0 = centered, 1 = at edges
        gaze_score = max(0.0, 1.0 - (h_deviation / GAZE_THRESHOLD) * 0.5)
        
        # Head pose score (how aligned with screen)
        yaw, pitch, roll = head_pose
        yaw_score = max(0.0, 1.0 - abs(yaw) / HEAD_YAW_THRESHOLD)
        pitch_score = max(0.0, 1.0 - abs(pitch) / HEAD_PITCH_THRESHOLD)
        head_pose_score = (yaw_score + pitch_score) / 2
        
        # Final attention score
        attention_score = (
            WEIGHT_GAZE * gaze_score +
            WEIGHT_HEAD_POSE * head_pose_score +
            WEIGHT_EYE_OPENNESS * eye_openness +
            WEIGHT_FACE_PRESENCE * face_presence
        )
        
        return {
            "gaze_score": round(gaze_score, 3),
            "head_pose_score": round(head_pose_score, 3),
            "eye_openness": round(eye_openness, 3),
            "face_presence": round(face_presence, 3),
            "attention_score": round(attention_score, 3)
        }
    
    def _classify_status(self, score: float) -> str:
        """Classify attention status based on score."""
        if score >= THRESHOLD_FOCUSED:
            return "Focused"
        elif score >= THRESHOLD_PARTIAL:
            return "Partially Attentive"
        else:
            return "Distracted"
    
    def _update_blink_rate(self, ear: float) -> float:
        """
        Track blinks and calculate blink rate (blinks per minute).
        """
        self.ear_history.append(ear)
        
        if len(self.ear_history) >= BLINK_CONSECUTIVE_FRAMES:
            recent = list(self.ear_history)[-BLINK_CONSECUTIVE_FRAMES:]
            if all(e < EAR_THRESHOLD_CLOSED for e in recent):
                if self.blink_counter == 0:
                    self.total_blinks += 1
                self.blink_counter = BLINK_CONSECUTIVE_FRAMES
            elif self.blink_counter > 0:
                self.blink_counter -= 1
        
        elapsed = time.time() - self.blink_start_time
        if elapsed > 0:
            blink_rate = (self.total_blinks / elapsed) * 60  # Per minute
        else:
            blink_rate = 0
        
        return round(blink_rate, 1)
    
    def process_frame(self, frame: np.ndarray) -> Dict:
        """
        Process a single video frame and return attention metrics.
        
        Args:
            frame: BGR image from webcam (numpy array)
            
        Returns:
            Dictionary with all attention metrics
        """
        if frame is None or frame.size == 0:
            self.current_metrics["face_detected"] = False
            self.current_metrics["attention_score"] = 0.0
            self.current_metrics["status"] = "No Frame"
            return self.current_metrics
        
        img_h, img_w = frame.shape[:2]
        
        # Convert BGR to RGB for MediaPipe
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # Process with MediaPipe
        results = self.face_mesh.process(rgb_frame)
        
        if not results.multi_face_landmarks:
            self.current_metrics["face_detected"] = False
            self.current_metrics["attention_score"] = 0.0
            self.current_metrics["status"] = "No Face Detected"
            return self.current_metrics
        
        # Get first face landmarks
        face_landmarks = results.multi_face_landmarks[0].landmark
        
        # Calculate Eye Aspect Ratio
        left_ear = self._calculate_ear(self.LEFT_EYE, face_landmarks, img_w, img_h)
        right_ear = self._calculate_ear(self.RIGHT_EYE, face_landmarks, img_w, img_h)
        avg_ear = (left_ear + right_ear) / 2
        
        # Calculate gaze
        gaze = self._calculate_gaze(face_landmarks, img_w, img_h)
        
        # Estimate head pose
        head_pose = self._estimate_head_pose(face_landmarks, img_w, img_h)
        
        # Calculate component scores
        scores = self._calculate_scores(True, avg_ear, gaze, head_pose)
        
        # Apply smoothing
        self.score_history.append(scores["attention_score"])
        smoothed_score = sum(self.score_history) / len(self.score_history)
        
        # Update blink rate
        blink_rate = self._update_blink_rate(avg_ear)
        
        # Update current metrics
        self.current_metrics.update({
            "face_detected": True,
            "attention_score": round(smoothed_score, 3),
            "status": self._classify_status(smoothed_score),
            "gaze_score": scores["gaze_score"],
            "head_pose_score": scores["head_pose_score"],
            "eye_openness": scores["eye_openness"],
            "face_presence": scores["face_presence"],
            "blink_rate": blink_rate,
            "head_yaw": round(head_pose[0], 1),
            "head_pitch": round(head_pose[1], 1),
            "head_roll": round(head_pose[2], 1),
            "left_ear": round(left_ear, 3),
            "right_ear": round(right_ear, 3),
        })
        
        return self.current_metrics.copy()
    
    def get_current_metrics(self) -> Dict:
        """Return the most recent attention metrics."""
        return self.current_metrics.copy()
    
    def reset_blink_counter(self):
        """Reset blink tracking for a new session."""
        self.blink_counter = 0
        self.total_blinks = 0
        self.blink_start_time = time.time()
        self.ear_history.clear()
    
    def release(self):
        """Release MediaPipe resources."""
        self.face_mesh.close()


# Quick test
if __name__ == "__main__":
    print("Testing AttentionDetector...")
    detector = AttentionDetector()
    
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Could not open webcam")
        exit(1)
    
    print("Press 'q' to quit")
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        metrics = detector.process_frame(frame)
        
        # Display metrics on frame
        y = 30
        for key in ["attention_score", "status", "gaze_score", "head_pose_score", "eye_openness"]:
            text = f"{key}: {metrics.get(key, 'N/A')}"
            cv2.putText(frame, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            y += 25
        
        cv2.imshow("Attention Detector Test", frame)
        
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
    
    cap.release()
    cv2.destroyAllWindows()
    detector.release()
    print("Test complete")
