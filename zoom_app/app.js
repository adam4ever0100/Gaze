/**
 * Attention Monitor - Student Application JavaScript
 * 
 * Uses browser-based MediaPipe for attention detection.
 * No video is sent to the server - only attention scores.
 */

// API endpoints
const API = {
    consent: '/api/consent',
    submit: '/api/submit-score',
    status: '/api/status'
};

// Backend URL for score submission
const BACKEND_URL = window.location.protocol + '//' + window.location.hostname + ':5002';

// Application state
const state = {
    monitoring: false,
    consented: false,
    studentName: '',
    meetingId: '',
    sessionStart: null,
    zoomContext: null,
    submitInterval: null,
    detector: null
};

// DOM Elements
const elements = {
    // Sections
    consentSection: document.getElementById('consentSection'),
    monitoringSection: document.getElementById('monitoringSection'),
    loadingOverlay: document.getElementById('loadingOverlay'),

    // Video
    videoElement: document.getElementById('videoElement'),
    canvasElement: document.getElementById('canvasElement'),
    faceIndicator: document.getElementById('faceIndicator'),

    // Inputs
    studentName: document.getElementById('studentName'),
    meetingId: document.getElementById('meetingId'),
    consentCheckbox: document.getElementById('consentCheckbox'),

    // Buttons
    startBtn: document.getElementById('startBtn'),
    stopBtn: document.getElementById('stopBtn'),

    // Status
    connectionStatus: document.getElementById('connectionStatus'),

    // Score display
    scoreValue: document.getElementById('scoreValue'),
    scoreCircle: document.getElementById('scoreCircle'),
    progressFill: document.getElementById('progressFill'),
    statusBadge: document.getElementById('statusBadge'),

    // Metrics
    faceValue: document.getElementById('faceValue'),
    faceIcon: document.getElementById('faceIcon'),
    gazeValue: document.getElementById('gazeValue'),
    headPoseValue: document.getElementById('headPoseValue'),
    eyeValue: document.getElementById('eyeValue'),
    blinkValue: document.getElementById('blinkValue'),
    sessionTime: document.getElementById('sessionTime')
};

// ============================================================
// Zoom SDK Integration
// ============================================================

async function initZoomSDK() {
    try {
        if (typeof zoomSdk !== 'undefined') {
            await zoomSdk.config({
                capabilities: [
                    'getMeetingContext',
                    'getUserContext'
                ]
            });

            const meetingContext = await zoomSdk.getMeetingContext();
            state.meetingId = meetingContext.meetingID || '';
            state.zoomContext = meetingContext;

            if (elements.meetingId) {
                elements.meetingId.value = state.meetingId;
                elements.meetingId.disabled = true;
            }

            try {
                const userContext = await zoomSdk.getUserContext();
                if (userContext.screenName && elements.studentName) {
                    elements.studentName.value = userContext.screenName;
                }
            } catch (e) {
                console.log('Could not get user context');
            }

            updateConnectionStatus(true, 'Connected to Zoom');
        }
    } catch (error) {
        console.log('Zoom SDK not available, running standalone');
        updateConnectionStatus(false, 'Standalone Mode');
    }
}

// ============================================================
// UI Updates
// ============================================================

function updateConnectionStatus(connected, text) {
    const statusDot = elements.connectionStatus.querySelector('.status-dot');
    const statusText = elements.connectionStatus.querySelector('.status-text');

    if (connected) {
        statusDot.classList.add('connected');
    } else {
        statusDot.classList.remove('connected');
    }
    statusText.textContent = text;
}

function updateScoreDisplay(score, status) {
    const percentage = Math.round(score * 100);

    elements.scoreValue.textContent = `${percentage}%`;
    elements.progressFill.style.width = `${percentage}%`;

    const degrees = (percentage / 100) * 360;
    let color = '#22c55e';

    if (status === 'Partially Attentive') {
        color = '#f59e0b';
    } else if (status === 'Distracted') {
        color = '#ef4444';
    }

    elements.scoreCircle.style.background = `conic-gradient(
        ${color} 0deg,
        ${color} ${degrees}deg,
        #1a1a2e ${degrees}deg
    )`;

    elements.statusBadge.textContent = status;
    elements.statusBadge.className = 'status-badge';

    if (status === 'Partially Attentive') {
        elements.statusBadge.classList.add('partial');
    } else if (status === 'Distracted') {
        elements.statusBadge.classList.add('distracted');
    }
}

function updateMetrics(metrics) {
    elements.faceValue.textContent = metrics.face_detected ? 'Yes' : 'No';
    elements.faceIcon.textContent = metrics.face_detected ? '😊' : '👤';
    elements.faceIndicator.textContent = metrics.face_detected ? '😊' : '👤';

    elements.gazeValue.textContent = `${Math.round(metrics.gaze_score * 100)}%`;
    elements.headPoseValue.textContent = `${Math.round(metrics.head_pose_score * 100)}%`;
    elements.eyeValue.textContent = `${Math.round(metrics.eye_openness * 100)}%`;
    elements.blinkValue.textContent = `${metrics.blink_rate}/min`;
}

function updateSessionTime() {
    if (!state.sessionStart) return;

    const elapsed = Math.floor((Date.now() - state.sessionStart) / 1000);
    const minutes = Math.floor(elapsed / 60).toString().padStart(2, '0');
    const seconds = (elapsed % 60).toString().padStart(2, '0');

    elements.sessionTime.textContent = `${minutes}:${seconds}`;
}

function showLoading(show) {
    if (show) {
        elements.loadingOverlay.classList.remove('hidden');
    } else {
        elements.loadingOverlay.classList.add('hidden');
    }
}

// ============================================================
// Attention Detector Callbacks
// ============================================================

function onMetricsUpdate(metrics) {
    updateScoreDisplay(metrics.attention_score, metrics.status);
    updateMetrics(metrics);
    updateSessionTime();
}

// ============================================================
// Backend Submission
// ============================================================

async function submitScoreToBackend() {
    if (!state.detector || !state.monitoring) return;

    const metrics = state.detector.getMetrics();

    try {
        await fetch(`${BACKEND_URL}/submit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            mode: 'cors',
            body: JSON.stringify({
                student_name: state.studentName,
                meeting_id: state.meetingId,
                attention_score: metrics.attention_score,
                status: metrics.status,
                timestamp: Date.now() / 1000
            })
        });
    } catch (error) {
        // Backend might not be running
        console.log('Backend submission failed:', error.message);
    }
}

// ============================================================
// Monitoring Control
// ============================================================

async function startMonitoring() {
    console.log('Starting monitoring...');
    showLoading(true);

    try {
        // Initialize detector
        console.log('Creating AttentionDetector...');
        state.detector = new AttentionDetector();
        state.detector.onMetricsUpdate = onMetricsUpdate;

        console.log('Initializing detector with video element...');
        await state.detector.initialize(elements.videoElement, elements.canvasElement);

        console.log('Starting detector...');
        await state.detector.start();

        state.monitoring = true;
        state.sessionStart = Date.now();

        // Switch UI
        console.log('Switching to monitoring UI...');
        elements.consentSection.classList.add('hidden');
        elements.monitoringSection.classList.remove('hidden');
        showLoading(false);

        // Start submitting scores every 2 seconds
        state.submitInterval = setInterval(submitScoreToBackend, 2000);

        // Initial submit after 1 second
        setTimeout(submitScoreToBackend, 1000);

        updateConnectionStatus(true, 'Monitoring Active');
        console.log('Monitoring started successfully!');

    } catch (error) {
        console.error('Failed to start monitoring:', error);
        showLoading(false);

        // Show user-friendly error
        let errorMsg = error.message || 'Unknown error';
        let showBrowserLink = false;

        if (errorMsg.includes('denied') || errorMsg.includes('Permission')) {
            errorMsg = 'Camera access denied. Please allow camera access and try again.';
        } else if (errorMsg.includes('NotFoundError')) {
            errorMsg = 'No camera found. Please connect a camera.';
        } else if (errorMsg.includes('NotReadableError') || errorMsg.includes('Could not start video source')) {
            // This is the common error inside Zoom iframe
            errorMsg = 'Zoom is blocking camera access. Please open this app in Chrome or Safari to monitor attention.';
            showBrowserLink = true;
        }

        if (showBrowserLink || confirm(`Failed to start: ${errorMsg}\n\nDo you want to open this in your external browser instead?`)) {
            window.open(window.location.href, '_blank');
        } else {
            alert('Failed to start monitoring: ' + errorMsg);
        }
    }
}

function stopMonitoring() {
    if (state.detector) {
        state.detector.stop();
    }

    if (state.submitInterval) {
        clearInterval(state.submitInterval);
        state.submitInterval = null;
    }

    state.monitoring = false;

    elements.monitoringSection.classList.add('hidden');
    elements.consentSection.classList.remove('hidden');

    updateConnectionStatus(false, 'Stopped');
}

// ============================================================
// Event Handlers
// ============================================================

function validateForm() {
    const nameValid = elements.studentName && elements.studentName.value.trim().length > 0;
    const consented = elements.consentCheckbox && elements.consentCheckbox.checked;

    console.log('Validating form:', { nameValid, consented });

    if (elements.startBtn) {
        elements.startBtn.disabled = !(nameValid && consented);
        console.log('Start button disabled:', elements.startBtn.disabled);
    }
}

async function handleStart() {
    console.log('handleStart called');
    state.studentName = elements.studentName.value.trim();
    state.meetingId = elements.meetingId.value.trim() || 'default';

    // Submit consent to server
    try {
        await fetch(API.consent, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: state.studentName,
                meeting_id: state.meetingId,
                consented: true
            })
        });
    } catch (e) {
        console.log('Consent submission to local server failed');
    }

    await startMonitoring();
}

// ============================================================
// Initialize
// ============================================================

function init() {
    console.log('Initializing app...');
    console.log('Elements:', elements);

    if (elements.studentName) {
        elements.studentName.addEventListener('input', validateForm);
        elements.studentName.addEventListener('keyup', validateForm);
        console.log('Added listener to studentName');
    }

    if (elements.consentCheckbox) {
        elements.consentCheckbox.addEventListener('change', validateForm);
        elements.consentCheckbox.addEventListener('click', validateForm);
        console.log('Added listener to consentCheckbox');
    }

    if (elements.startBtn) {
        elements.startBtn.addEventListener('click', handleStart);
        console.log('Added listener to startBtn');
    }

    if (elements.stopBtn) {
        elements.stopBtn.addEventListener('click', stopMonitoring);
    }

    initZoomSDK();
    updateConnectionStatus(false, 'Ready');

    // Run initial validation
    validateForm();

    console.log('App initialized successfully');
}

document.addEventListener('DOMContentLoaded', init);
