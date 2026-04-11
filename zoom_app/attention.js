/**
 * Browser-Based Attention Detection using MediaPipe Face Mesh
 * 
 * Uses native browser camera API for better compatibility with Zoom iframe.
 * Only numeric attention scores are transmitted - no video is sent.
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

        // State
        this.isInitialized = false;
        this.isProcessing = false;
        this.currentMetrics = this.getDefaultMetrics();

        // Blink tracking
        this.blinkCount = 0;
        this.sessionStartTime = Date.now();
        this.eyeWasClosed = false;

        // Score smoothing
        this.scoreHistory = [];
        this.maxHistoryLength = 5;

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

        if (this.videoElement.readyState >= 2) {
            try {
                await this.faceMesh.send({ image: this.videoElement });
            } catch (error) {
                console.error('Frame processing error:', error);
            }
        }

        // Process at ~15 FPS
        this.animationId = setTimeout(() => this.processFrame(), 66);
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

    onResults(results) {
        if (!results.multiFaceLandmarks || results.multiFaceLandmarks.length === 0) {
            this.currentMetrics = this.getDefaultMetrics();
            this.notifyUpdate();
            return;
        }

        const landmarks = results.multiFaceLandmarks[0];

        // Calculate Eye Aspect Ratio (EAR)
        const leftEAR = this.calculateEAR(this.LEFT_EYE, landmarks);
        const rightEAR = this.calculateEAR(this.RIGHT_EYE, landmarks);
        const avgEAR = (leftEAR + rightEAR) / 2;

        // Calculate gaze and head pose
        const gaze = this.calculateGaze(landmarks);
        const headPose = this.estimateHeadPose(landmarks);

        // Blink detection
        this.detectBlink(avgEAR);

        // Calculate all scores
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

        // Update metrics
        this.currentMetrics = {
            face_detected: true,
            attention_score: Math.round(smoothedScore * 1000) / 1000,
            status: this.classifyStatus(smoothedScore),
            gaze_score: scores.gaze_score,
            head_pose_score: scores.head_pose_score,
            eye_openness: scores.eye_openness,
            face_presence: 1.0,
            blink_rate: blinkRate,
            head_yaw: Math.round(headPose.yaw),
            head_pitch: Math.round(headPose.pitch)
        };

        this.notifyUpdate();
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

            return { horizontal: (leftRatio + rightRatio) / 2 };
        } catch (e) {
            return { horizontal: 0.5 };
        }
    }

    estimateHeadPose(landmarks) {
        try {
            const nose = landmarks[this.NOSE_TIP];
            const leftCheek = landmarks[234];
            const rightCheek = landmarks[454];
            const forehead = landmarks[10];
            const chin = landmarks[152];

            const faceWidth = Math.abs(rightCheek.x - leftCheek.x);
            const noseOffset = nose.x - (leftCheek.x + rightCheek.x) / 2;
            const yaw = faceWidth > 0 ? (noseOffset / faceWidth) * 60 : 0;

            const faceHeight = Math.abs(chin.y - forehead.y);
            const verticalOffset = nose.y - (forehead.y + chin.y) / 2;
            const pitch = faceHeight > 0 ? (verticalOffset / faceHeight) * 45 : 0;

            return { yaw, pitch, roll: 0 };
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

        // Eye openness score
        let eyeOpenness;
        if (ear < this.EAR_THRESHOLD_CLOSED) {
            eyeOpenness = 0;
        } else if (ear > this.EAR_THRESHOLD_OPEN) {
            eyeOpenness = 1;
        } else {
            eyeOpenness = (ear - this.EAR_THRESHOLD_CLOSED) /
                (this.EAR_THRESHOLD_OPEN - this.EAR_THRESHOLD_CLOSED);
        }

        // Gaze score (how centered)
        const hDeviation = Math.abs(gaze.horizontal - 0.5) * 2;
        const gazeScore = Math.max(0, 1 - hDeviation);

        // Head pose score
        const yawScore = Math.max(0, 1 - Math.abs(headPose.yaw) / this.HEAD_YAW_THRESHOLD);
        const pitchScore = Math.max(0, 1 - Math.abs(headPose.pitch) / this.HEAD_PITCH_THRESHOLD);
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

    classifyStatus(score) {
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
