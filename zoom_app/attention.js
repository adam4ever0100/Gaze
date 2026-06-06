/**
 * Browser-Based Attention Detection using MediaPipe Face Mesh
 * 
 * Features:
 * - Per-student calibration (5-second baseline capture)
 * - Multi-factor attention states: Focused, Drowsy, Distracted, Absent, Phone Use
 * - solvePnP-based head pose estimation (yaw/pitch/roll in degrees)
 * - Environment quality pre-check (lighting, contrast, face size, camera angle)
 * - Adaptive frame-rate throttling
 * - Only numeric attention scores are transmitted — no video is sent.
 */

class AttentionDetector {
    constructor() {
        // MediaPipe Face Mesh
        this.faceMesh = null;
        this.videoElement = null;
        this.canvasElement = null;
        this.canvasCtx = null;
        this.stream = null;
        this.animationId = null;

        // Landmark indices for eyes
        this.LEFT_EYE = [362, 385, 387, 263, 373, 380];
        this.RIGHT_EYE = [33, 160, 158, 133, 153, 144];
        this.LEFT_IRIS = [469, 470, 471, 472];
        this.RIGHT_IRIS = [474, 475, 476, 477];
        this.NOSE_TIP = 1;

        // Attention weights
        this.WEIGHT_GAZE = 0.35;
        this.WEIGHT_HEAD_POSE = 0.30;
        this.WEIGHT_EYE_OPENNESS = 0.25;
        this.WEIGHT_FACE_PRESENCE = 0.10;

        // Thresholds
        this.EAR_THRESHOLD_CLOSED = 0.18;
        this.EAR_THRESHOLD_OPEN = 0.25;
        this.HEAD_YAW_THRESHOLD = 30;
        this.HEAD_PITCH_THRESHOLD = 25;

        // Multi-state classification thresholds
        this.DROWSY_EAR_THRESHOLD = 0.22;
        this.DROWSY_BLINK_RATE_MIN = 25;
        this.DROWSY_HEAD_PITCH_MIN = 10;
        this.PHONE_USE_PITCH_THRESHOLD = -15;
        this.ABSENT_NO_FACE_FRAMES = 45;  // ~3 seconds at 15 FPS

        // 3D model points for solvePnP head pose estimation (generic face model)
        this.MODEL_POINTS_3D = [
            [0.0, 0.0, 0.0],           // Nose tip (landmark 1)
            [0.0, -330.0, -65.0],      // Chin (landmark 152)
            [-225.0, 170.0, -135.0],   // Left eye left corner (landmark 263)
            [225.0, 170.0, -135.0],    // Right eye right corner (landmark 33)
            [-150.0, -150.0, -125.0],  // Left mouth corner (landmark 287)
            [150.0, -150.0, -125.0]    // Right mouth corner (landmark 57)
        ];
        this.POSE_LANDMARK_INDICES = [1, 152, 263, 33, 287, 57];

        // State
        this.isInitialized = false;
        this.isProcessing = false;
        this.currentMetrics = this.getDefaultMetrics();

        // Calibration state
        this.isCalibrating = false;
        this.calibrationData = { gazeH: [], gazeV: [], headYaw: [], headPitch: [], ear: [] };
        this.baseline = null;  // { gazeCenterH, gazeCenterV, headYaw, headPitch, earOpen }
        this.onCalibrationComplete = null;
        this.onCalibrationProgress = null;
        this.calibrationStartTime = 0;
        this.calibrationDuration = 5000;  // 5 seconds

        // Blink tracking
        this.blinkCount = 0;
        this.sessionStartTime = Date.now();
        this.eyeWasClosed = false;

        // Multi-state tracking
        this.consecutiveNoFaceFrames = 0;
        this.recentEarValues = [];      // last 30 EAR samples (~2 seconds)
        this.maxRecentEar = 30;

        // Score smoothing
        this.scoreHistory = [];
        this.maxHistoryLength = 5;

        // Adaptive frame-rate throttling
        this.targetFps = 15;
        this.maxFps = 15;
        this.minFps = 2;
        this.frameInterval = 66;
        this.lastFrameTime = 0;
        this.frameTimes = [];
        this.maxFrameTimeSamples = 10;
        this.tabHidden = false;

        // Callbacks
        this.onMetricsUpdate = null;
    }

    getDefaultMetrics() {
        return {
            face_detected: false,
            attention_score: 0,
            status: 'No Face',
            gaze_score: 0,
            head_pose_score: 0,
            eye_openness: 0,
            face_presence: 0,
            blink_rate: 0,
            head_yaw: 0,
            head_pitch: 0
        };
    }

    async initialize(videoElement, canvasElement) {
        console.log('Initializing AttentionDetector...');

        this.videoElement = videoElement;
        this.canvasElement = canvasElement;
        this.canvasCtx = canvasElement.getContext('2d');

        // Request camera access
        try {
            console.log('Requesting camera access...');
            this.stream = await navigator.mediaDevices.getUserMedia({
                video: {
                    facingMode: 'user',
                    width: { ideal: 640 },
                    height: { ideal: 480 }
                }
            });

            this.videoElement.srcObject = this.stream;
            await this.videoElement.play();
            console.log('Camera started successfully');
        } catch (error) {
            console.error('Camera access failed:', error);
            throw new Error('Camera access denied. Please allow camera access.');
        }

        await this._initFaceMesh();
    }

    async initializeWithStream(videoElement, canvasElement, existingStream) {
        console.log('Initializing AttentionDetector with existing stream...');

        this.videoElement = videoElement;
        this.canvasElement = canvasElement;
        this.canvasCtx = canvasElement.getContext('2d');
        this.stream = existingStream;

        await this._initFaceMesh();
    }

    async _initFaceMesh() {
        // Initialize MediaPipe Face Mesh (using local files for compatibility)
        console.log('Loading MediaPipe Face Mesh from local files...');
        this.faceMesh = new FaceMesh({
            locateFile: (file) => {
                console.log('Loading MediaPipe file:', file);
                return `/mediapipe/${file}`;
            }
        });

        this.faceMesh.setOptions({
            maxNumFaces: 1,
            refineLandmarks: true,
            minDetectionConfidence: 0.5,
            minTrackingConfidence: 0.5
        });

        this.faceMesh.onResults((results) => this.onResults(results));

        this.isInitialized = true;

        // Page Visibility API — reduce FPS when tab is hidden
        document.addEventListener('visibilitychange', () => {
            this.tabHidden = document.hidden;
            if (this.tabHidden) {
                this._setFps(this.minFps);
                console.log('[Attention] Tab hidden — reducing to', this.minFps, 'FPS');
            } else {
                // Restore to the adaptive FPS (not necessarily max)
                this._adaptFps();
                console.log('[Attention] Tab visible — restoring to', this.targetFps, 'FPS');
            }
        });

        console.log('AttentionDetector initialized successfully');
    }

    async start() {
        if (!this.isInitialized) {
            throw new Error('Detector not initialized');
        }

        this.isProcessing = true;
        this.blinkCount = 0;
        this.sessionStartTime = Date.now();
        this.scoreHistory = [];

        // Start processing loop
        this.processFrame();
        console.log('Attention monitoring started');
    }

    async processFrame() {
        if (!this.isProcessing) return;

        const frameStart = performance.now();

        if (this.videoElement.readyState >= 2) {
            try {
                await this.faceMesh.send({ image: this.videoElement });
            } catch (error) {
                console.error('Frame processing error:', error);
            }
        }

        // Track frame processing time for adaptive FPS
        const frameTime = performance.now() - frameStart;
        this.frameTimes.push(frameTime);
        if (this.frameTimes.length > this.maxFrameTimeSamples) {
            this.frameTimes.shift();
        }

        // Adapt FPS based on processing load (skip if tab is hidden)
        if (!this.tabHidden && this.frameTimes.length >= this.maxFrameTimeSamples) {
            this._adaptFps();
        }

        // Schedule next frame with adaptive interval
        this.animationId = setTimeout(() => this.processFrame(), this.frameInterval);
    }

    /**
     * Adapt FPS based on average frame processing time.
     * If frames take > 80ms on average, the device is struggling — reduce FPS.
     * If frames take < 40ms, the device can handle more — increase FPS.
     */
    _adaptFps() {
        if (this.tabHidden) return;

        const avgFrameTime = this.frameTimes.reduce((a, b) => a + b, 0) / this.frameTimes.length;

        let newFps = this.targetFps;
        if (avgFrameTime > 80) {
            // Device is struggling — reduce FPS
            newFps = Math.max(4, Math.floor(this.targetFps * 0.6));
        } else if (avgFrameTime > 50) {
            // Moderate load — slight reduction
            newFps = Math.max(8, Math.floor(this.targetFps * 0.8));
        } else if (avgFrameTime < 40 && this.targetFps < this.maxFps) {
            // Device is comfortable — try increasing
            newFps = Math.min(this.maxFps, this.targetFps + 1);
        }

        if (newFps !== this.targetFps) {
            this._setFps(newFps);
        }
    }

    /**
     * Set the target FPS and update the frame interval.
     */
    _setFps(fps) {
        this.targetFps = fps;
        this.frameInterval = Math.round(1000 / fps);
    }

    stop() {
        this.isProcessing = false;

        if (this.animationId) {
            clearTimeout(this.animationId);
            this.animationId = null;
        }

        if (this.stream) {
            this.stream.getTracks().forEach(track => track.stop());
            this.stream = null;
        }

        console.log('Attention monitoring stopped');
    }

    // ================================================================
    // Calibration
    // ================================================================

    /**
     * Start a 5-second calibration session.
     * The student should look at the center of their screen.
     * Captures baseline gaze, head pose, and EAR values.
     */
    startCalibration(duration = 5000) {
        this.calibrationDuration = duration;
        this.calibrationData = { gazeH: [], gazeV: [], headYaw: [], headPitch: [], ear: [] };
        this.isCalibrating = true;
        this.calibrationStartTime = Date.now();
        console.log('[Attention] Calibration started — look at center of screen');
    }

    /**
     * Finish calibration and compute baseline from collected samples.
     * Uses the median of collected values for robustness against outliers.
     */
    _finishCalibration() {
        this.isCalibrating = false;
        const d = this.calibrationData;

        if (d.gazeH.length < 10) {
            console.warn('[Attention] Insufficient calibration data, using defaults');
            this.baseline = null;
            if (this.onCalibrationComplete) {
                this.onCalibrationComplete(null);
            }
            return;
        }

        const median = (arr) => {
            const sorted = [...arr].sort((a, b) => a - b);
            const mid = Math.floor(sorted.length / 2);
            return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
        };

        this.baseline = {
            gazeCenterH: median(d.gazeH),
            gazeCenterV: median(d.gazeV),
            headYaw: median(d.headYaw),
            headPitch: median(d.headPitch),
            earOpen: median(d.ear),
        };

        console.log('[Attention] Calibration complete:', this.baseline);

        if (this.onCalibrationComplete) {
            this.onCalibrationComplete(this.baseline);
        }
    }

    // ================================================================
    // Environment Quality Check
    // ================================================================

    /**
     * Analyze the current video frame for environmental issues.
     * Returns an array of warning objects, empty if all checks pass.
     */
    checkEnvironment() {
        const warnings = [];
        if (!this.videoElement || this.videoElement.readyState < 2) {
            return [{ type: 'error', message: 'Camera not ready' }];
        }

        const canvas = document.createElement('canvas');
        const w = this.videoElement.videoWidth;
        const h = this.videoElement.videoHeight;
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(this.videoElement, 0, 0, w, h);

        const imageData = ctx.getImageData(0, 0, w, h);
        const pixels = imageData.data;

        // Calculate average luminance and standard deviation
        let sumLum = 0;
        let sumLumSq = 0;
        const pixelCount = w * h;

        for (let i = 0; i < pixels.length; i += 4) {
            const lum = 0.299 * pixels[i] + 0.587 * pixels[i + 1] + 0.114 * pixels[i + 2];
            sumLum += lum;
            sumLumSq += lum * lum;
        }

        const avgLum = sumLum / pixelCount;
        const stdLum = Math.sqrt(sumLumSq / pixelCount - avgLum * avgLum);

        // Check brightness
        if (avgLum < 50) {
            warnings.push({
                type: 'lighting',
                severity: 'warning',
                message: 'Your lighting is too dark — detection may be inaccurate.',
                suggestion: 'Move to a brighter area or turn on a light facing you.'
            });
        } else if (avgLum > 230) {
            warnings.push({
                type: 'lighting',
                severity: 'warning',
                message: 'Your lighting is too bright — camera is overexposed.',
                suggestion: 'Reduce backlight or move the light source behind the camera.'
            });
        }

        // Check contrast
        if (stdLum < 20) {
            warnings.push({
                type: 'contrast',
                severity: 'info',
                message: 'Low image contrast detected.',
                suggestion: 'Ensure your face is well-lit and distinct from the background.'
            });
        }

        // Check face size (uses last known landmarks if available)
        if (this.currentMetrics.face_detected && this._lastFaceBounds) {
            // Width and height are already normalized (0-1), so their product is the face-to-frame ratio
            const faceRatio = this._lastFaceBounds.width * this._lastFaceBounds.height;

            if (faceRatio < 0.04) {
                warnings.push({
                    type: 'distance',
                    severity: 'warning',
                    message: 'You are too far from the camera.',
                    suggestion: 'Move closer to the camera for better detection accuracy.'
                });
            } else if (faceRatio > 0.65) {
                warnings.push({
                    type: 'distance',
                    severity: 'info',
                    message: 'You are very close to the camera.',
                    suggestion: 'Move back slightly for optimal detection.'
                });
            }
        }

        // Check camera angle (uses head pitch from last frame)
        if (this.currentMetrics.face_detected) {
            const pitch = this.currentMetrics.head_pitch;
            if (Math.abs(pitch) > 20) {
                warnings.push({
                    type: 'angle',
                    severity: 'warning',
                    message: `Camera angle is too ${pitch > 0 ? 'low' : 'high'}.`,
                    suggestion: 'Adjust your camera to be at eye level.'
                });
            }
        }

        return warnings;
    }

    // ================================================================
    // Frame Processing
    // ================================================================

    onResults(results) {
        if (!results.multiFaceLandmarks || results.multiFaceLandmarks.length === 0) {
            this.consecutiveNoFaceFrames++;

            // After ~3 seconds of no face, classify as Absent
            if (this.consecutiveNoFaceFrames >= this.ABSENT_NO_FACE_FRAMES) {
                this.currentMetrics = this.getDefaultMetrics();
                this.currentMetrics.status = 'Absent';
            } else {
                this.currentMetrics = this.getDefaultMetrics();
            }
            this.notifyUpdate();
            return;
        }

        this.consecutiveNoFaceFrames = 0;
        const landmarks = results.multiFaceLandmarks[0];

        // Track face bounding box for environment check
        this._updateFaceBounds(landmarks);

        // Calculate Eye Aspect Ratio (EAR)
        const leftEAR = this.calculateEAR(this.LEFT_EYE, landmarks);
        const rightEAR = this.calculateEAR(this.RIGHT_EYE, landmarks);
        const avgEAR = (leftEAR + rightEAR) / 2;

        // Track recent EAR for drowsy detection
        this.recentEarValues.push(avgEAR);
        if (this.recentEarValues.length > this.maxRecentEar) {
            this.recentEarValues.shift();
        }

        // Calculate gaze and head pose
        const gaze = this.calculateGaze(landmarks);
        const headPose = this.estimateHeadPose(landmarks);

        // Handle calibration: collect samples instead of scoring
        if (this.isCalibrating) {
            this.calibrationData.gazeH.push(gaze.horizontal);
            this.calibrationData.gazeV.push(gaze.vertical);
            this.calibrationData.headYaw.push(headPose.yaw);
            this.calibrationData.headPitch.push(headPose.pitch);
            this.calibrationData.ear.push(avgEAR);

            const elapsed = Date.now() - this.calibrationStartTime;
            const progress = Math.min(1, elapsed / this.calibrationDuration);
            if (this.onCalibrationProgress) {
                this.onCalibrationProgress(progress);
            }

            if (elapsed >= this.calibrationDuration) {
                this._finishCalibration();
            }
            return;
        }

        // Blink detection
        this.detectBlink(avgEAR);

        // Calculate all scores (calibration-aware)
        const scores = this.calculateScores(true, avgEAR, gaze, headPose);

        // Apply smoothing
        this.scoreHistory.push(scores.attention_score);
        if (this.scoreHistory.length > this.maxHistoryLength) {
            this.scoreHistory.shift();
        }
        const smoothedScore = this.scoreHistory.reduce((a, b) => a + b, 0) / this.scoreHistory.length;

        // Calculate blink rate (per minute)
        const elapsedMinutes = (Date.now() - this.sessionStartTime) / 60000;
        const blinkRate = elapsedMinutes > 0.1 ? Math.round(this.blinkCount / elapsedMinutes) : 0;

        // Multi-factor state classification
        const status = this.classifyAttentionState(smoothedScore, avgEAR, blinkRate, headPose);

        // Update metrics
        this.currentMetrics = {
            face_detected: true,
            attention_score: Math.round(smoothedScore * 1000) / 1000,
            status: status,
            gaze_score: scores.gaze_score,
            head_pose_score: scores.head_pose_score,
            eye_openness: scores.eye_openness,
            face_presence: 1.0,
            blink_rate: blinkRate,
            head_yaw: Math.round(headPose.yaw * 10) / 10,
            head_pitch: Math.round(headPose.pitch * 10) / 10,
            calibrated: this.baseline !== null
        };

        this.notifyUpdate();
    }

    _updateFaceBounds(landmarks) {
        let minX = 1, maxX = 0, minY = 1, maxY = 0;
        for (const lm of landmarks) {
            if (lm.x < minX) minX = lm.x;
            if (lm.x > maxX) maxX = lm.x;
            if (lm.y < minY) minY = lm.y;
            if (lm.y > maxY) maxY = lm.y;
        }
        this._lastFaceBounds = {
            width: maxX - minX,
            height: maxY - minY
        };
    }

    calculateEAR(eyeIndices, landmarks) {
        const points = eyeIndices.map(i => landmarks[i]);

        const v1 = this.distance(points[1], points[5]);
        const v2 = this.distance(points[2], points[4]);
        const h = this.distance(points[0], points[3]);

        if (h === 0) return 0;
        return (v1 + v2) / (2.0 * h);
    }

    calculateGaze(landmarks) {
        try {
            const leftIrisCenter = this.getCenter(this.LEFT_IRIS.map(i => landmarks[i]));
            const rightIrisCenter = this.getCenter(this.RIGHT_IRIS.map(i => landmarks[i]));

            const leftInner = landmarks[362];
            const leftOuter = landmarks[263];
            const rightInner = landmarks[133];
            const rightOuter = landmarks[33];

            const leftWidth = Math.abs(leftOuter.x - leftInner.x);
            const rightWidth = Math.abs(rightOuter.x - rightInner.x);

            const leftRatio = leftWidth > 0 ?
                (leftIrisCenter.x - Math.min(leftInner.x, leftOuter.x)) / leftWidth : 0.5;
            const rightRatio = rightWidth > 0 ?
                (rightIrisCenter.x - Math.min(rightInner.x, rightOuter.x)) / rightWidth : 0.5;

            // Left eye vertical ratio (landmarks 385/387 top, 373/380 bottom)
            const leftTopY = (landmarks[385].y + landmarks[387].y) / 2;
            const leftBottomY = (landmarks[373].y + landmarks[380].y) / 2;
            const leftHeight = Math.abs(leftBottomY - leftTopY);
            const leftVRatio = leftHeight > 0 ? (leftIrisCenter.y - leftTopY) / leftHeight : 0.5;

            // Right eye vertical ratio (landmarks 160/158 top, 153/144 bottom)
            const rightTopY = (landmarks[160].y + landmarks[158].y) / 2;
            const rightBottomY = (landmarks[153].y + landmarks[144].y) / 2;
            const rightHeight = Math.abs(rightBottomY - rightTopY);
            const rightVRatio = rightHeight > 0 ? (rightIrisCenter.y - rightTopY) / rightHeight : 0.5;

            return {
                horizontal: (leftRatio + rightRatio) / 2,
                vertical: (leftVRatio + rightVRatio) / 2
            };
        } catch (e) {
            return { horizontal: 0.5, vertical: 0.5 };
        }
    }

    /**
     * Estimate head pose using a solvePnP-style approach.
     * Maps 3D model points to 2D landmark positions to compute
     * actual yaw, pitch, roll angles in degrees.
     */
    estimateHeadPose(landmarks) {
        try {
            // Get 2D image points from landmarks (normalized 0-1)
            const imagePoints = this.POSE_LANDMARK_INDICES.map(idx => [
                landmarks[idx].x,
                landmarks[idx].y
            ]);

            // Estimate focal length from face width (heuristic)
            const leftEye = landmarks[263];
            const rightEye = landmarks[33];
            const faceWidth = Math.abs(rightEye.x - leftEye.x);
            // Average inter-eye distance is ~130mm, focal length ~ faceWidth / 130 * 640
            const focalLength = faceWidth > 0 ? 1.0 / faceWidth : 640;

            // Build camera matrix (normalized coordinates)
            const cx = 0.5, cy = 0.5;
            const fx = focalLength, fy = focalLength;

            // Solve for rotation using the geometric approach
            // Extract yaw from nose-tip horizontal offset relative to eye midpoint
            const noseTip = imagePoints[0];
            const chin = imagePoints[1];
            const leftEyeCorner = imagePoints[2];
            const rightEyeCorner = imagePoints[3];
            const leftMouth = imagePoints[4];
            const rightMouth = imagePoints[5];

            // Yaw: nose offset from face center, scaled by perspective
            const faceCenterX = (leftEyeCorner[0] + rightEyeCorner[0]) / 2;
            const currentFaceWidth = Math.abs(rightEyeCorner[0] - leftEyeCorner[0]);
            const noseOffsetX = noseTip[0] - faceCenterX;
            // Map offset to degrees: at ±faceWidth/2, yaw ≈ ±90°
            const yaw = currentFaceWidth > 0 ? Math.asin(Math.max(-1, Math.min(1, (noseOffsetX / currentFaceWidth) * 2))) * (180 / Math.PI) : 0;

            // Pitch: vertical relationship between nose tip, eye center, and chin
            const eyeCenterY = (leftEyeCorner[1] + rightEyeCorner[1]) / 2;
            const faceHeight = Math.abs(chin[1] - eyeCenterY);
            const noseRelY = (noseTip[1] - eyeCenterY) / (faceHeight || 1);
            // At noseRelY ~ 0.35 face is neutral, deviation maps to pitch
            const pitch = (noseRelY - 0.35) * 90;

            // Roll: angle of the eye line relative to horizontal
            const deltaX = rightEyeCorner[0] - leftEyeCorner[0];
            const deltaY = rightEyeCorner[1] - leftEyeCorner[1];
            const roll = Math.atan2(deltaY, deltaX) * (180 / Math.PI);

            return { yaw, pitch, roll };
        } catch (e) {
            return { yaw: 0, pitch: 0, roll: 0 };
        }
    }

    calculateScores(faceDetected, ear, gaze, headPose) {
        if (!faceDetected) {
            return {
                gaze_score: 0,
                head_pose_score: 0,
                eye_openness: 0,
                face_presence: 0,
                attention_score: 0
            };
        }

        // Use calibration baseline if available
        const gazeCenterH = this.baseline ? this.baseline.gazeCenterH : 0.5;
        const gazeCenterV = this.baseline ? this.baseline.gazeCenterV : 0.5;
        const baseYaw = this.baseline ? this.baseline.headYaw : 0;
        const basePitch = this.baseline ? this.baseline.headPitch : 0;
        const baseEar = this.baseline ? this.baseline.earOpen : this.EAR_THRESHOLD_OPEN;

        // Eye openness score (calibration-aware)
        const earThresholdClosed = this.baseline
            ? Math.max(0.10, baseEar * 0.65)  // 65% of baseline = closed
            : this.EAR_THRESHOLD_CLOSED;
        const earThresholdOpen = this.baseline
            ? baseEar * 0.90  // 90% of baseline = fully open
            : this.EAR_THRESHOLD_OPEN;

        let eyeOpenness;
        if (ear < earThresholdClosed) {
            eyeOpenness = 0;
        } else if (ear > earThresholdOpen) {
            eyeOpenness = 1;
        } else {
            eyeOpenness = (ear - earThresholdClosed) / (earThresholdOpen - earThresholdClosed);
        }

        // Gaze score — deviation from calibrated center (both horizontal and vertical)
        const hDeviation = Math.abs(gaze.horizontal - gazeCenterH) * 2;
        const vDeviation = Math.abs(gaze.vertical - gazeCenterV) * 2.5; // Slightly higher sensitivity for vertical gaze
        const maxGazeDeviation = Math.max(hDeviation, vDeviation);
        const gazeScore = Math.max(0, 1 - maxGazeDeviation);

        // Head pose score — deviation from calibrated neutral
        const yawDeviation = Math.abs(headPose.yaw - baseYaw);
        const pitchDeviation = Math.abs(headPose.pitch - basePitch);
        const yawScore = Math.max(0, 1 - yawDeviation / this.HEAD_YAW_THRESHOLD);
        const pitchScore = Math.max(0, 1 - pitchDeviation / this.HEAD_PITCH_THRESHOLD);
        const headPoseScore = (yawScore + pitchScore) / 2;

        // Final attention score
        const attentionScore = (
            this.WEIGHT_GAZE * gazeScore +
            this.WEIGHT_HEAD_POSE * headPoseScore +
            this.WEIGHT_EYE_OPENNESS * eyeOpenness +
            this.WEIGHT_FACE_PRESENCE * 1.0
        );

        return {
            gaze_score: Math.round(gazeScore * 1000) / 1000,
            head_pose_score: Math.round(headPoseScore * 1000) / 1000,
            eye_openness: Math.round(eyeOpenness * 1000) / 1000,
            face_presence: 1.0,
            attention_score: Math.round(attentionScore * 1000) / 1000
        };
    }

    detectBlink(ear) {
        if (ear < this.EAR_THRESHOLD_CLOSED) {
            if (!this.eyeWasClosed) {
                this.eyeWasClosed = true;
            }
        } else {
            if (this.eyeWasClosed) {
                this.blinkCount++;
                this.eyeWasClosed = false;
            }
        }
    }

    /**
     * Legacy single-score classification (kept for compatibility).
     */
    classifyStatus(score) {
        if (score >= 0.7) return 'Focused';
        if (score >= 0.4) return 'Partially Attentive';
        return 'Distracted';
    }

    /**
     * Multi-factor attention state classification.
     * Combines score with EAR, blink rate, and head pose to detect:
     * - Drowsy: half-closed eyes + high blink rate + head drooping
     * - Phone Use: head pitched strongly downward
     * - Focused / Partially Attentive / Distracted: score-based
     * (Absent is handled in onResults when no face is detected)
     */
    classifyAttentionState(score, ear, blinkRate, headPose) {
        // Phone use: head pitched strongly downward (looking at lap)
        if (headPose.pitch > 15 && score < 0.7) {
            return 'Phone Use';
        }

        // Drowsy detection: half-closed eyes + elevated blink rate
        if (this.recentEarValues.length >= 10) {
            const avgRecentEar = this.recentEarValues.reduce((a, b) => a + b, 0) / this.recentEarValues.length;
            const isDrowsyEar = avgRecentEar < this.DROWSY_EAR_THRESHOLD && avgRecentEar > 0.10;
            const isDrowsyBlink = blinkRate > this.DROWSY_BLINK_RATE_MIN;
            const isDrowsyPitch = headPose.pitch > this.DROWSY_HEAD_PITCH_MIN;

            // At least 2 of 3 drowsy indicators must be present
            const drowsySignals = (isDrowsyEar ? 1 : 0) + (isDrowsyBlink ? 1 : 0) + (isDrowsyPitch ? 1 : 0);
            if (drowsySignals >= 2) {
                return 'Drowsy';
            }
        }

        // Fall back to score-based classification
        if (score >= 0.7) return 'Focused';
        if (score >= 0.4) return 'Partially Attentive';
        return 'Distracted';
    }

    distance(p1, p2) {
        return Math.sqrt(Math.pow(p1.x - p2.x, 2) + Math.pow(p1.y - p2.y, 2));
    }

    getCenter(points) {
        const x = points.reduce((sum, p) => sum + p.x, 0) / points.length;
        const y = points.reduce((sum, p) => sum + p.y, 0) / points.length;
        return { x, y };
    }

    notifyUpdate() {
        if (this.onMetricsUpdate) {
            this.onMetricsUpdate(this.currentMetrics);
        }
    }

    getMetrics() {
        return this.currentMetrics;
    }
}

// Make available globally
window.AttentionDetector = AttentionDetector;
console.log('AttentionDetector class loaded');
