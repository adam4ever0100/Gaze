/**
 * Gaze — Student Application
 * 
 * Handles:
 * - Socket.IO connection to backend
 * - WebRTC peer-to-peer video conferencing
 * - Attention detection integration
 * - Real-time score submission
 */

// Backend URL (same host, port 5002)
// In production (behind reverse proxy), backend is on same origin
// In development, backend runs on port 5002
const isProduction = (window.location.port === '' || window.location.port === '80' || window.location.port === '443');
const BACKEND_URL = isProduction
    ? window.location.origin
    : window.location.protocol + '//' + window.location.hostname + ':5002';

// App state
const state = {
    socket: null,
    studentName: '',
    roomCode: '',
    localStream: null,
    blankStream: null,       // Black canvas stream for Score Only mode
    scoreOnlyMode: false,    // Privacy mode: AI runs locally, peers see blank
    livekitRoom: null,       // LiveKit Room instance (SFU)
    detector: null,
    monitoring: false,
    sessionStart: null,
    submitInterval: null,
    cameraOn: true,
    micOn: true,
    screenStream: null,
    isScreenSharing: false,
    lastTipTime: 0,
    alertSound: null,
    reconnectAttempts: 0,
    theme: localStorage.getItem('gaze-theme') || 'dark',
    screenSharePeerSid: null
};

// DOM elements
const el = {
    joinSection: document.getElementById('joinSection'),
    classroomSection: document.getElementById('classroomSection'),
    loadingOverlay: document.getElementById('loadingOverlay'),
    loadingText: document.getElementById('loadingText'),
    connectionStatus: document.getElementById('connectionStatus'),
    studentName: document.getElementById('studentName'),
    roomCode: document.getElementById('roomCode'),
    consentCheckbox: document.getElementById('consentCheckbox'),
    joinBtn: document.getElementById('joinBtn'),
    localVideo: document.getElementById('localVideo'),
    canvasElement: document.getElementById('canvasElement'),
    localVideoName: document.getElementById('localVideoName'),
    localAttentionBadge: document.getElementById('localAttentionBadge'),
    videoGrid: document.getElementById('videoGrid'),
    toggleCameraBtn: document.getElementById('toggleCameraBtn'),
    toggleMicBtn: document.getElementById('toggleMicBtn'),
    leaveBtn: document.getElementById('leaveBtn'),
    scoreRing: document.getElementById('scoreRing'),
    scoreValue: document.getElementById('scoreValue'),
    statusBadge: document.getElementById('statusBadge'),
    gazeValue: document.getElementById('gazeValue'),
    headPoseValue: document.getElementById('headPoseValue'),
    eyeValue: document.getElementById('eyeValue'),
    blinkValue: document.getElementById('blinkValue'),
    sessionTime: document.getElementById('sessionTime'),
    participantCount: document.getElementById('participantCount'),
    participantList: document.getElementById('participantList'),
    // Chat
    chatMessages: document.getElementById('chatMessages'),
    chatInput: document.getElementById('chatInput'),
    chatSendBtn: document.getElementById('chatSendBtn'),
    // Screen share
    shareScreenBtn: document.getElementById('shareScreenBtn'),
    // Attention tips
    tipPanel: document.getElementById('tipPanel'),
    tipText: document.getElementById('tipText'),
    // Hand raise & reactions
    handRaiseBtn: document.getElementById('handRaiseBtn'),
    reactionToggleBtn: document.getElementById('reactionToggleBtn'),
    reactionMenu: document.getElementById('reactionMenu'),
    // Score Only checkbox & indicator
    scoreOnlyCheckbox: document.getElementById('scoreOnlyCheckbox'),
    scoreOnlyOverlay: document.getElementById('scoreOnlyOverlay'),
    // Screen share overlay
    screenShareOverlay: document.getElementById('screenShareOverlay'),
    screenShareVideo: document.getElementById('screenShareVideo'),
    screenShareName: document.getElementById('screenShareName'),
    exitScreenShareBtn: document.getElementById('exitScreenShareBtn')
};

// ============================================================
// Socket.IO Connection
// ============================================================

function connectSocket() {
    state.socket = io(BACKEND_URL, {
        transports: ['websocket', 'polling'],
        reconnection: true,
        reconnectionAttempts: Infinity,
        reconnectionDelay: 1000,
        reconnectionDelayMax: 10000,
        randomizationFactor: 0.3
    });

    state.socket.on('connect', () => {
        console.log('Connected to server');
        updateStatus(true, 'Connected');
        hideReconnectionBanner();
        state.reconnectAttempts = 0;

        // Auto-rejoin room if disconnected during session
        if (state.roomCode) {
            state.socket.emit('join-room', {
                room_code: state.roomCode,
                student_name: state.studentName
            });
        }
    });

    state.socket.on('disconnect', (reason) => {
        console.log('Disconnected from server:', reason);
        updateStatus(false, 'Reconnecting...');
        showReconnectionBanner();
    });

    state.socket.on('reconnect_attempt', (attempt) => {
        state.reconnectAttempts = attempt;
        updateStatus(false, `Reconnecting (${attempt})...`);
    });

    state.socket.on('reconnect', (attemptNumber) => {
        console.log('Reconnected after', attemptNumber, 'attempts');
        updateStatus(true, 'Connected');
        hideReconnectionBanner();
    });

    state.socket.on('reconnect_failed', () => {
        updateStatus(false, 'Connection lost — refresh page');
    });

    state.socket.on('error', (data) => {
        console.error('Server error:', data.message);
        showErrorBanner(data.message);
        showLoading(false);
    });

    // Room events
    state.socket.on('room-joined', handleRoomJoined);
    state.socket.on('peer-joined', handlePeerJoined);
    state.socket.on('peer-left', handlePeerLeft);
    state.socket.on('room-closed', handleRoomClosed);

    // Chat
    state.socket.on('chat-message', handleChatMessage);

    // Screen sharing
    state.socket.on('screen-share-started', (data) => {
        handleScreenShareStarted(data);
        state.screenSharePeerSid = data.sid;
        if (el.screenShareName) {
            el.screenShareName.textContent = `${data.name} is sharing their screen`;
        }
    });
    state.socket.on('screen-share-stopped', (data) => {
        handleScreenShareStopped(data);
        if (state.screenSharePeerSid === data.sid) {
            exitScreenShareFullscreen();
        }
    });

    // Hand raise & reactions
    state.socket.on('hand-raised', handleHandRaised);
    state.socket.on('hand-lowered', handleHandLowered);
    state.socket.on('reaction-received', handleReactionReceived);

    // Screen share permissions (teacher grants/revokes)
    state.socket.on('screen-share-granted', (data) => {
        el.shareScreenBtn.classList.remove('hidden');
        showReactionBubble('📺 You can now share your screen');
    });
    state.socket.on('screen-share-revoked', () => {
        el.shareScreenBtn.classList.add('hidden');
        if (state.isScreenSharing) stopScreenShare();
    });

    // LiveKit token response
    state.socket.on('livekit-token', handleLivekitToken);

    // Force mute from teacher
    state.socket.on('force-mute', () => {
        // Step 1: mute the real MediaStream audio track (this is what actually silences the mic)
        if (state.localStream) {
            state.localStream.getAudioTracks().forEach(t => { t.enabled = false; });
        }
        // Step 2: update state and UI
        state.micOn = false;
        if (el.toggleMicBtn) el.toggleMicBtn.classList.add('muted');
        // Step 3: notify LiveKit so peers see the muted indicator
        if (state.livekitRoom && state.livekitRoom.localParticipant) {
            state.livekitRoom.localParticipant.setMicrophoneEnabled(false);
        }
        showReactionBubble('🔇 Teacher muted your mic');
    });

    // Kicked from room by teacher
    state.socket.on('room-kicked', (data) => {
        // Stop all media
        if (state.localStream) state.localStream.getTracks().forEach(t => t.stop());
        if (state.livekitRoom) state.livekitRoom.disconnect();

        // Show full-screen message then redirect
        document.body.innerHTML = `
            <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;
                        height:100vh;background:#070711;color:#f0f0ff;font-family:Inter,sans-serif;gap:16px;text-align:center;padding:24px;">
                <div style="font-size:56px">🚪</div>
                <h2 style="margin:0;color:#ef4444">Removed from Classroom</h2>
                <p style="color:#8888aa;max-width:360px">${data.message || 'You were removed by the teacher.'}</p>
                <button onclick="location.reload()" style="margin-top:12px;padding:10px 24px;background:#6366f1;color:#fff;
                    border:none;border-radius:8px;cursor:pointer;font-size:15px">Rejoin</button>
            </div>`;
    });

    // Private attention nudge from teacher
    state.socket.on('attention-nudge', (data) => {
        showReactionBubble(data.message || '👋 Your teacher wants you to pay attention!');
        // Also flash the page border briefly
        document.body.style.outline = '4px solid #f59e0b';
        document.body.style.outlineOffset = '-4px';
        setTimeout(() => { document.body.style.outline = ''; }, 2500);
    });
}

// Create notification sound
function initAlertSound() {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        state.alertSound = ctx;
    } catch (e) { /* silent fallback */ }
}

function playAlertSound() {
    try {
        if (!state.alertSound) return;
        const ctx = state.alertSound;
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.frequency.value = 440;
        osc.type = 'sine';
        gain.gain.value = 0.1;
        osc.start();
        osc.stop(ctx.currentTime + 0.15);
    } catch (e) { /* silent */ }
}

// ============================================================
// Room Management
// ============================================================

async function joinRoom() {
    state.studentName = el.studentName.value.trim();
    state.roomCode = el.roomCode.value.trim().toUpperCase();
    state.scoreOnlyMode = el.scoreOnlyCheckbox ? el.scoreOnlyCheckbox.checked : false;

    if (!state.studentName || !state.roomCode) return;

    showLoading(true, 'Connecting to room...');

    // Get camera/mic
    try {
        state.localStream = await navigator.mediaDevices.getUserMedia({
            video: {
                facingMode: 'user',
                width: { ideal: 640, min: 320 },
                height: { ideal: 480, min: 240 },
                frameRate: { ideal: 24, min: 15 },
                aspectRatio: { ideal: 4 / 3 }
            },
            audio: {
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true
            }
        });

        // Apply advanced video enhancements if supported
        try {
            const videoTrack = state.localStream.getVideoTracks()[0];
            const capabilities = videoTrack.getCapabilities ? videoTrack.getCapabilities() : {};
            const advancedConstraints = {};

            if (capabilities.brightness) {
                advancedConstraints.brightness = capabilities.brightness.max * 0.6;
            }
            if (capabilities.contrast) {
                advancedConstraints.contrast = capabilities.contrast.max * 0.55;
            }
            if (capabilities.sharpness) {
                advancedConstraints.sharpness = capabilities.sharpness.max * 0.7;
            }
            if (capabilities.whiteBalanceMode) {
                advancedConstraints.whiteBalanceMode = 'continuous';
            }
            if (capabilities.exposureMode) {
                advancedConstraints.exposureMode = 'continuous';
            }

            if (Object.keys(advancedConstraints).length > 0) {
                await videoTrack.applyConstraints({ advanced: [advancedConstraints] });
                console.log('Applied advanced camera enhancements');
            }
        } catch (enhanceErr) {
            console.log('Advanced camera enhancements not supported:', enhanceErr.message);
        }

        // Score Only mode: create a blank black stream for peers
        if (state.scoreOnlyMode) {
            state.blankStream = createBlankStream();
            // Add audio tracks from real stream to blank stream
            state.localStream.getAudioTracks().forEach(t => state.blankStream.addTrack(t));
            // Show Score Only overlay on local video tile
            if (el.scoreOnlyOverlay) el.scoreOnlyOverlay.classList.remove('hidden');
            console.log('Score Only mode: camera private, blank stream for peers');
        }

        el.localVideo.srcObject = state.localStream;
    } catch (err) {
        console.error('Camera error:', err);
        alert('Could not access camera. Please allow camera permission.');
        showLoading(false);
        return;
    }

    showLoading(true, 'Loading AI model...');

    // Initialize attention detector (always uses real camera stream)
    try {
        state.detector = new AttentionDetector();
        state.detector.onMetricsUpdate = onMetricsUpdate;
        await state.detector.initializeWithStream(el.localVideo, el.canvasElement, state.localStream);
        await state.detector.start();
    } catch (err) {
        console.error('Attention detector error:', err);
        // Continue without attention detection
    }

    showLoading(true, 'Joining room...');

    // Join via socket
    state.socket.emit('join-room', {
        room_code: state.roomCode,
        student_name: state.studentName
    });
}

/**
 * Create a blank (black) MediaStream from an offscreen canvas.
 * Used in Score Only mode so peers see a black video instead of the real camera.
 */
function createBlankStream() {
    const canvas = document.createElement('canvas');
    canvas.width = 640;
    canvas.height = 480;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#000000';
    ctx.fillRect(0, 0, 640, 480);
    // Capture at 15 FPS to stay lightweight
    const stream = canvas.captureStream(15);
    return stream;
}

function handleRoomJoined(data) {
    console.log('Joined room:', data.room_code);
    state.monitoring = true;
    state.sessionStart = Date.now();

    // Show classroom UI
    el.joinSection.classList.add('hidden');
    el.classroomSection.classList.remove('hidden');
    el.localVideoName.textContent = state.studentName;
    showLoading(false);
    updateStatus(true, `Room ${data.room_code}`);

    // Existing participants will be shown when LiveKit connects and
    // fires ParticipantConnected / TrackSubscribed events.
    // No need to create UI tiles here — LiveKit identity differs from socket SID.

    // Request LiveKit token from backend
    showLoading(true, 'Connecting to video server...');
    state.socket.emit('get-livekit-token');

    // Start periodic score submission
    state.submitInterval = setInterval(submitScore, 2000);
    setTimeout(submitScore, 500);
}

async function handleLivekitToken(data) {
    console.log('Got LiveKit token, connecting to SFU...');
    const maxRetries = 3;
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
        try {
            await connectToLiveKit(data.url, data.token);
            showLoading(false);
            hideErrorBanner();
            return;
        } catch (err) {
            console.error(`LiveKit connection attempt ${attempt}/${maxRetries} failed:`, err);
            if (attempt < maxRetries) {
                showLoading(true, `Retrying video connection (${attempt}/${maxRetries})...`);
                await new Promise(r => setTimeout(r, 2000 * attempt));
            }
        }
    }
    showErrorBanner('Failed to connect to video server — video is unavailable, but attention tracking still works');
    showLoading(false);
}

async function connectToLiveKit(url, token) {
    const { Room, RoomEvent, Track, VideoPresets } = LivekitClient;

    const room = new Room({
        adaptiveStream: true,
        dynacast: true,
        autoSubscribe: true
        // videoCaptureDefaults intentionally omitted — tracks are published
        // manually via publishTrack(), so this option has no effect and
        // VideoPresets.h360 may not exist in all SDK versions.
    });

    state.livekitRoom = room;

    // --- Reusable track attachment helper ---
    function attachTrack(track, publication, participant) {
        const identity = participant.identity;
        const el_id = `video-${identity}`;
        let tile = document.getElementById(`tile-${identity}`);

        if (!tile) {
            const name = participant.name || identity;
            addParticipantUI(identity, name, identity.startsWith('teacher-'));
            tile = document.getElementById(`tile-${identity}`);
        }

        if (track.kind === Track.Kind.Video) {
            if (publication.source === Track.Source.ScreenShare) {
                // Screen share → fullscreen overlay
                const stream = new MediaStream([track.mediaStreamTrack]);
                const name = participant.name || identity;
                if (el.screenShareName) el.screenShareName.textContent = `${name} is sharing`;
                showScreenShareFullscreen(identity, stream);
            } else {
                // Camera track → video tile
                const videoEl = document.getElementById(el_id);
                if (videoEl) {
                    track.attach(videoEl);
                }
            }
        } else if (track.kind === Track.Kind.Audio) {
            if (!document.getElementById(`audio-${identity}`)) {
                const audioEl = track.attach();
                audioEl.id = `audio-${identity}`;
                document.body.appendChild(audioEl);
            }
        }
    }

    // Helper: process all published tracks for a given participant
    function syncParticipantTracks(participant) {
        const identity = participant.identity;
        const name = participant.name || identity;

        // Ensure UI tile exists
        if (!document.getElementById(`tile-${identity}`)) {
            addParticipantUI(identity, name, identity.startsWith('teacher-'));
        }

        participant.trackPublications.forEach((publication) => {
            if (publication.isSubscribed && publication.track) {
                attachTrack(publication.track, publication, participant);
            } else if (!publication.isSubscribed && publication.kind) {
                // Force-subscribe if auto-subscribe missed it
                publication.setSubscribed(true);
            }
        });
    }

    // --- Track Subscribed: remote participant's track is ready ---
    room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
        console.log(`[LK] TrackSubscribed: ${participant.identity} ${track.kind} (${publication.source})`);
        attachTrack(track, publication, participant);
    });

    // --- Track Unsubscribed ---
    room.on(RoomEvent.TrackUnsubscribed, (track, publication, participant) => {
        console.log(`[LK] TrackUnsubscribed: ${participant.identity}`);
        track.detach().forEach(el => el.remove());

        if (publication.source === Track.Source.ScreenShare) {
            exitScreenShareFullscreen();
        }
    });

    // --- Track Published: a remote participant published a new track ---
    // This fires BEFORE TrackSubscribed. Ensure we subscribe and have a tile ready.
    room.on(RoomEvent.TrackPublished, (publication, participant) => {
        console.log(`[LK] TrackPublished: ${participant.identity} ${publication.kind} (subscribed: ${publication.isSubscribed})`);
        const identity = participant.identity;

        // Ensure tile exists so the track has somewhere to attach when subscribed
        if (!document.getElementById(`tile-${identity}`)) {
            addParticipantUI(identity, participant.name || identity, identity.startsWith('teacher-'));
        }

        // Force subscribe if not already
        if (!publication.isSubscribed) {
            publication.setSubscribed(true);
        }
    });

    // --- Participant Connected ---
    room.on(RoomEvent.ParticipantConnected, (participant) => {
        console.log(`[LK] ParticipantConnected: ${participant.identity}`);
        addParticipantUI(participant.identity, participant.name || participant.identity, participant.identity.startsWith('teacher-'));
        updateParticipantCount();

        // Process any tracks already published by this participant
        syncParticipantTracks(participant);
    });

    // --- Participant Disconnected ---
    room.on(RoomEvent.ParticipantDisconnected, (participant) => {
        console.log(`[LK] ParticipantDisconnected: ${participant.identity}`);
        removePeer(participant.identity);
        updateParticipantCount();
    });

    // --- Room fully connected: all existing participants are available NOW ---
    room.on(RoomEvent.Connected, () => {
        console.log(`[LK] Room connected. Remote participants: ${room.remoteParticipants.size}`);
        room.remoteParticipants.forEach((participant) => {
            syncParticipantTracks(participant);
        });
        updateParticipantCount();
    });

    // --- Disconnected from room ---
    room.on(RoomEvent.Disconnected, (reason) => {
        console.log('[LK] Disconnected from LiveKit:', reason);
        // Stop the sync interval
        if (state._livekitSyncInterval) {
            clearInterval(state._livekitSyncInterval);
            state._livekitSyncInterval = null;
        }
    });

    // Connect to LiveKit room
    await room.connect(url, token);
    console.log('Connected to LiveKit SFU room:', room.name);

    // Publish local camera + mic
    const outboundStream = (state.scoreOnlyMode && state.blankStream) ? state.blankStream : state.localStream;
    if (outboundStream) {
        const videoTrack = outboundStream.getVideoTracks()[0];
        const audioTrack = state.localStream.getAudioTracks()[0];

        if (videoTrack) {
            await room.localParticipant.publishTrack(videoTrack, {
                name: 'camera',
                simulcast: false
            });
        }
        if (audioTrack) {
            await room.localParticipant.publishTrack(audioTrack, {
                name: 'microphone'
            });
        }
    }

    // Process any already-connected participants (right after connect)
    room.remoteParticipants.forEach((participant) => {
        syncParticipantTracks(participant);
    });
    updateParticipantCount();

    // Safety net: re-sync 1s and 3s after connect in case remoteParticipants
    // was not fully populated immediately after room.connect() resolved.
    setTimeout(() => {
        if (!state.livekitRoom) return;
        console.log(`[LK] 1s delayed sync — remote participants: ${state.livekitRoom.remoteParticipants.size}`);
        state.livekitRoom.remoteParticipants.forEach(syncParticipantTracks);
        updateParticipantCount();
    }, 1000);
    setTimeout(() => {
        if (!state.livekitRoom) return;
        console.log(`[LK] 3s delayed sync — remote participants: ${state.livekitRoom.remoteParticipants.size}`);
        state.livekitRoom.remoteParticipants.forEach(syncParticipantTracks);
        updateParticipantCount();
    }, 3000);

    // Periodic sync: catch any tracks missed by events (runs every 3 seconds)
    if (state._livekitSyncInterval) clearInterval(state._livekitSyncInterval);
    state._livekitSyncInterval = setInterval(() => {
        if (!state.livekitRoom) return;
        state.livekitRoom.remoteParticipants.forEach((participant) => {
            const identity = participant.identity;

            // Create tile if missing
            if (!document.getElementById(`tile-${identity}`)) {
                addParticipantUI(identity, participant.name || identity, identity.startsWith('teacher-'));
            }

            // Attach any subscribed but unattached video tracks
            participant.trackPublications.forEach((publication) => {
                if (publication.isSubscribed && publication.track) {
                    if (publication.track.kind === Track.Kind.Video && publication.source !== Track.Source.ScreenShare) {
                        const videoEl = document.getElementById(`video-${identity}`);
                        if (videoEl && !videoEl.srcObject) {
                            console.log(`[LK Sync] Re-attaching video for ${identity}`);
                            publication.track.attach(videoEl);
                        }
                    } else if (publication.track.kind === Track.Kind.Audio) {
                        if (!document.getElementById(`audio-${identity}`)) {
                            console.log(`[LK Sync] Re-attaching audio for ${identity}`);
                            const audioEl = publication.track.attach();
                            audioEl.id = `audio-${identity}`;
                            document.body.appendChild(audioEl);
                        }
                    }
                } else if (!publication.isSubscribed && publication.kind) {
                    // Not subscribed yet — force it
                    console.log(`[LK Sync] Force-subscribing to ${identity} ${publication.kind}`);
                    publication.setSubscribed(true);
                }
            });
        });
        updateParticipantCount();
    }, 3000);
}

function handlePeerJoined(data) {
    console.log('Peer joined via Socket.IO:', data.name);
    // Video tile is created by LiveKit events (ParticipantConnected / TrackSubscribed).
    // No need to create duplicate UI tiles here.
}

function handlePeerLeft(data) {
    console.log('Peer left:', data.name);
    // LiveKit handles removing video tiles via ParticipantDisconnected.
    // Clean up any leftover tiles — try both raw SID and LiveKit identity format.
    removePeer(data.sid);
    removePeer(`student-${data.sid}`);
    updateParticipantCount();
}

function handleRoomClosed(data) {
    alert(data.message || 'Session ended');
    leaveRoom();
}

function leaveRoom() {
    state.monitoring = false;

    if (state.submitInterval) {
        clearInterval(state.submitInterval);
        state.submitInterval = null;
    }

    if (state.detector) {
        state.detector.stop();
    }

    // Stop periodic LiveKit sync
    if (state._livekitSyncInterval) {
        clearInterval(state._livekitSyncInterval);
        state._livekitSyncInterval = null;
    }

    // Disconnect from LiveKit
    if (state.livekitRoom) {
        state.livekitRoom.disconnect();
        state.livekitRoom = null;
    }

    // Stop local stream
    if (state.localStream) {
        state.localStream.getTracks().forEach(t => t.stop());
        state.localStream = null;
    }

    // Stop blank stream (Score Only)
    if (state.blankStream) {
        state.blankStream.getTracks().forEach(t => t.stop());
        state.blankStream = null;
    }
    state.scoreOnlyMode = false;
    if (el.scoreOnlyOverlay) el.scoreOnlyOverlay.classList.add('hidden');

    if (state.socket) {
        state.socket.emit('leave-room');
    }

    // Reset UI
    el.classroomSection.classList.add('hidden');
    el.joinSection.classList.remove('hidden');

    // Remove remote video tiles
    document.querySelectorAll('.remote-tile').forEach(t => t.remove());
    // Remove remote audio elements
    document.querySelectorAll('[id^="audio-"]').forEach(a => a.remove());

    updateStatus(false, 'Disconnected');
}

// ============================================================
// LiveKit helpers (replaced manual WebRTC)
// ============================================================

function removePeer(identity) {
    // Remove video tile
    const tile = document.getElementById(`tile-${identity}`);
    if (tile) tile.remove();

    // Remove audio element
    const audio = document.getElementById(`audio-${identity}`);
    if (audio) audio.remove();

    // Remove from participant list
    const item = document.getElementById(`participant-${identity}`);
    if (item) item.remove();
}

// ============================================================
// UI Management
// ============================================================

function addParticipantUI(sid, name, isTeacher) {
    // Add video tile
    if (!document.getElementById(`tile-${sid}`)) {
        const tile = document.createElement('div');
        tile.className = 'video-tile remote-tile';
        tile.id = `tile-${sid}`;
        tile.innerHTML = `
            <video id="video-${sid}" autoplay playsinline></video>
            <div class="video-label">
                <span class="video-name">${escapeHtml(name)}${isTeacher ? ' 👩‍🏫' : ''}</span>
            </div>
        `;

        // Double-click tile to view fullscreen
        tile.addEventListener('dblclick', () => {
            const videoEl = document.getElementById(`video-${sid}`);
            if (videoEl && videoEl.srcObject) {
                if (el.screenShareName) el.screenShareName.textContent = `${escapeHtml(name)}`;
                showScreenShareFullscreen(sid, videoEl.srcObject);
            }
        });
        tile.style.cursor = 'pointer';
        tile.title = 'Double-click to view fullscreen';

        el.videoGrid.appendChild(tile);
    }

    // Add to participant list
    if (!document.getElementById(`participant-${sid}`)) {
        const item = document.createElement('div');
        item.className = 'participant-item';
        item.id = `participant-${sid}`;
        item.innerHTML = `
            <span class="participant-dot"></span>
            <span>${escapeHtml(name)}${isTeacher ? ' 👩‍🏫' : ''}</span>
        `;
        el.participantList.appendChild(item);
    }
}

function updateParticipantCount() {
    // Primary: count from LiveKit's authoritative participant list
    let count = 1; // always include self
    if (state.livekitRoom && state.livekitRoom.remoteParticipants) {
        count += state.livekitRoom.remoteParticipants.size;
    } else {
        // Fallback: count DOM participant items
        count += document.querySelectorAll('.participant-item').length;
    }
    el.participantCount.textContent = count;
}

function updateStatus(connected, text) {
    const dot = el.connectionStatus.querySelector('.status-dot');
    const label = el.connectionStatus.querySelector('span:last-child');
    dot.classList.toggle('connected', connected);
    label.textContent = text;
}


// ============================================================
// Chat
// ============================================================

function handleChatMessage(data) {
    const msgEl = document.createElement('div');
    msgEl.className = `chat-msg ${data.is_teacher ? 'teacher' : ''}`;
    const time = new Date(data.timestamp * 1000);
    const timeStr = time.toLocaleTimeString('en', { hour: '2-digit', minute: '2-digit' });
    msgEl.innerHTML = `
        <span class="chat-sender">${escapeHtml(data.sender)}</span>
        <span class="chat-text">${escapeHtml(data.message)}</span>
        <span class="chat-time">${timeStr}</span>
    `;
    el.chatMessages.appendChild(msgEl);
    el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
    if (!data.is_teacher) playAlertSound();
}

function sendChatMessage() {
    const msg = el.chatInput.value.trim();
    if (!msg || !state.socket) return;
    state.socket.emit('send-message', { message: msg });
    el.chatInput.value = '';
}


// ============================================================
// Screen Sharing
// ============================================================

async function toggleScreenShare() {
    if (state.isScreenSharing) {
        stopScreenShare();
        return;
    }

    try {
        state.screenStream = await navigator.mediaDevices.getDisplayMedia({
            video: { cursor: 'always' },
            audio: false
        });

        state.isScreenSharing = true;
        el.shareScreenBtn.classList.add('active');

        // Publish screen share track via LiveKit
        const screenTrack = state.screenStream.getVideoTracks()[0];
        if (state.livekitRoom) {
            await state.livekitRoom.localParticipant.publishTrack(screenTrack, {
                name: 'screen',
                source: LivekitClient.Track.Source.ScreenShare
            });
        }

        // Show screen share in local video
        el.localVideo.srcObject = state.screenStream;

        state.socket.emit('start-screen-share', {});

        // Auto-stop when user clicks "Stop sharing"
        screenTrack.onended = () => stopScreenShare();

    } catch (err) {
        console.log('Screen share cancelled:', err.message);
    }
}

function stopScreenShare() {
    if (!state.isScreenSharing) return;
    state.isScreenSharing = false;
    el.shareScreenBtn.classList.remove('active');

    // Unpublish screen share track from LiveKit
    if (state.livekitRoom) {
        const publications = state.livekitRoom.localParticipant.trackPublications;
        publications.forEach((pub) => {
            if (pub.source === LivekitClient.Track.Source.ScreenShare) {
                state.livekitRoom.localParticipant.unpublishTrack(pub.track.mediaStreamTrack);
            }
        });
    }

    // Stop screen tracks
    if (state.screenStream) {
        state.screenStream.getTracks().forEach(t => t.stop());
        state.screenStream = null;
    }

    // Always show real camera in local video for the student
    el.localVideo.srcObject = state.localStream;

    state.socket.emit('stop-screen-share', {});
}

function handleScreenShareStarted(data) {
    showLoading(true, `${data.name} is sharing their screen`);
    setTimeout(() => showLoading(false), 2000);
}

function handleScreenShareStopped(data) {
    // Screen share ended for a remote peer
}


// ============================================================
// Screen Share Full-Screen Overlay
// ============================================================

function showScreenShareFullscreen(sid, stream) {
    if (!el.screenShareOverlay || !el.screenShareVideo) return;
    el.screenShareVideo.srcObject = stream;
    el.screenShareOverlay.classList.remove('hidden');
}

function exitScreenShareFullscreen() {
    if (!el.screenShareOverlay) return;
    el.screenShareOverlay.classList.add('hidden');
    if (el.screenShareVideo) {
        el.screenShareVideo.srcObject = null;
    }
    state.screenSharePeerSid = null;
}


// ============================================================
// Attention Tips
// ============================================================

const ATTENTION_TIPS = [
    { condition: (m) => m.gazeScore < 0.4, tip: '\ud83d\udc41\ufe0f Try looking directly at a the screen' },
    { condition: (m) => m.headPoseScore < 0.4, tip: '\ud83d\ude4c Face the camera more directly' },
    { condition: (m) => m.eyeOpenness < 0.3, tip: '\ud83d\udc40 Your eyes appear droopy \u2014 maybe take a break?' },
    { condition: (m) => m.blinkRate > 30, tip: '\ud83d\ude0c You\'re blinking a lot \u2014 rest your eyes' },
    { condition: (m) => m.score < 0.3, tip: '\u26a0\ufe0f Your attention is very low \u2014 try re-engaging' }
];

function checkAttentionTips(metrics) {
    const now = Date.now();
    if (now - state.lastTipTime < 15000) return; // Max 1 tip per 15 seconds

    for (const { condition, tip } of ATTENTION_TIPS) {
        if (condition(metrics)) {
            state.lastTipTime = now;
            el.tipPanel.classList.remove('hidden');
            el.tipText.textContent = tip;
            playAlertSound();
            // Auto-hide after 8 seconds
            setTimeout(() => el.tipPanel.classList.add('hidden'), 8000);
            return;
        }
    }
    // If all good, hide tip
    el.tipPanel.classList.add('hidden');
}

function showLoading(show, text) {
    if (show) {
        el.loadingOverlay.classList.remove('hidden');
        if (text) el.loadingText.textContent = text;
    } else {
        el.loadingOverlay.classList.add('hidden');
    }
}

// ============================================================
// Attention Detection Callbacks
// ============================================================

function onMetricsUpdate(metrics) {
    const pct = Math.round(metrics.attention_score * 100);

    // Score ring
    const degrees = (pct / 100) * 360;
    let color = '#22c55e';
    if (metrics.status === 'Partially Attentive') color = '#f59e0b';
    else if (metrics.status === 'Distracted') color = '#ef4444';

    el.scoreRing.style.background = `conic-gradient(${color} 0deg, ${color} ${degrees}deg, #1e1e30 ${degrees}deg)`;
    el.scoreValue.textContent = `${pct}%`;
    el.statusBadge.textContent = metrics.status;
    el.statusBadge.className = 'status-badge';
    if (metrics.status === 'Partially Attentive') el.statusBadge.classList.add('partial');
    else if (metrics.status === 'Distracted') el.statusBadge.classList.add('distracted');

    // Local badge
    el.localAttentionBadge.textContent = `${pct}%`;
    el.localAttentionBadge.className = 'attention-badge';
    if (pct >= 70) el.localAttentionBadge.classList.add('focused');
    else if (pct >= 40) el.localAttentionBadge.classList.add('partial');
    else el.localAttentionBadge.classList.add('distracted');

    // Metrics
    el.gazeValue.textContent = `${Math.round(metrics.gaze_score * 100)}%`;
    el.headPoseValue.textContent = `${Math.round(metrics.head_pose_score * 100)}%`;
    el.eyeValue.textContent = `${Math.round(metrics.eye_openness * 100)}%`;
    el.blinkValue.textContent = `${metrics.blink_rate}/min`;

    // Session time
    if (state.sessionStart) {
        const elapsed = Math.floor((Date.now() - state.sessionStart) / 1000);
        const mins = Math.floor(elapsed / 60).toString().padStart(2, '0');
        const secs = (elapsed % 60).toString().padStart(2, '0');
        el.sessionTime.textContent = `${mins}:${secs}`;
    }
    const tipMetrics = {
        score: metrics.attention_score,
        gazeScore: metrics.gaze_score,
        headPoseScore: metrics.head_pose_score,
        eyeOpenness: metrics.eye_openness,
        blinkRate: metrics.blink_rate
    };

    // Check for attention tips
    checkAttentionTips(tipMetrics);
}

function submitScore() {
    if (!state.detector || !state.monitoring || !state.socket) return;

    const m = state.detector.getMetrics();

    state.socket.emit('attention-score', {
        attention_score: m.attention_score,
        status: m.status,
        gaze_score: m.gaze_score,
        head_pose_score: m.head_pose_score,
        eye_openness: m.eye_openness
    });
}

// ============================================================
// Media Controls
// ============================================================

function toggleCamera() {
    if (!state.localStream) return;
    state.cameraOn = !state.cameraOn;
    state.localStream.getVideoTracks().forEach(t => { t.enabled = state.cameraOn; });
    el.toggleCameraBtn.classList.toggle('muted', !state.cameraOn);

    // Also toggle in LiveKit so peers see the change
    if (state.livekitRoom) {
        state.livekitRoom.localParticipant.setCameraEnabled(state.cameraOn);
    }
}

function toggleMic() {
    if (!state.localStream) return;
    state.micOn = !state.micOn;
    state.localStream.getAudioTracks().forEach(t => { t.enabled = state.micOn; });
    el.toggleMicBtn.classList.toggle('muted', !state.micOn);

    // Also toggle in LiveKit so peers hear the change
    if (state.livekitRoom) {
        state.livekitRoom.localParticipant.setMicrophoneEnabled(state.micOn);
    }
}

// ============================================================
// Helpers
// ============================================================

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function validateForm() {
    const nameValid = el.studentName.value.trim().length > 0;
    const roomValid = el.roomCode.value.trim().length >= 4;
    const consented = el.consentCheckbox.checked;
    el.joinBtn.disabled = !(nameValid && roomValid && consented);
}

let alertSound;
function initAlertSound() {
    alertSound = new Audio('/assets/alert.mp3');
    alertSound.volume = 0.5;
}

function playAlertSound() {
    if (alertSound) {
        alertSound.play().catch(e => console.warn("Failed to play sound:", e));
    }
}

// ============================================================
// Hand Raise & Reactions
// ============================================================

let handRaised = false;

function toggleHandRaise() {
    handRaised = !handRaised;
    if (handRaised) {
        state.socket.emit('hand-raise');
        el.handRaiseBtn.classList.add('active');
        el.handRaiseBtn.style.animation = 'pulse 1s infinite';
    } else {
        state.socket.emit('hand-lower');
        el.handRaiseBtn.classList.remove('active');
        el.handRaiseBtn.style.animation = '';
    }
}

function handleHandRaised(data) {
    showReactionBubble(`✋ ${data.name} raised hand`);
}

function handleHandLowered(data) {
    // clear any UI if needed
}

function sendReaction(emoji) {
    state.socket.emit('reaction', { emoji });
    el.reactionMenu.classList.add('hidden');
}

function handleReactionReceived(data) {
    showReactionBubble(`${data.emoji} ${data.name}`);
}

function showReactionBubble(text) {
    const bubble = document.createElement('div');
    bubble.className = 'reaction-bubble';
    bubble.textContent = text;
    bubble.style.cssText = `
        position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%);
        padding: 8px 18px; background: var(--bg-glass); backdrop-filter: blur(12px);
        border: 1px solid var(--border); border-radius: 20px;
        font-size: 14px; font-weight: 500; z-index: 100;
        animation: fadeUpOut 2.5s forwards;
    `;
    document.body.appendChild(bubble);
    setTimeout(() => bubble.remove(), 2500);
}

// ============================================================
// Error Recovery UI
// ============================================================

function showReconnectionBanner() {
    let banner = document.getElementById('reconnectBanner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'reconnectBanner';
        banner.style.cssText = 'position:fixed;top:0;left:0;right:0;padding:10px;background:var(--warning);color:#000;text-align:center;font-size:13px;font-weight:600;z-index:9999;animation:slideDown 0.3s ease';
        document.body.appendChild(banner);
    }
    banner.textContent = '⚡ Connection lost — reconnecting...';
    banner.style.display = 'block';
}

function hideReconnectionBanner() {
    const banner = document.getElementById('reconnectBanner');
    if (banner) banner.style.display = 'none';
}

function showErrorBanner(message) {
    let banner = document.getElementById('errorBanner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'errorBanner';
        banner.style.cssText = 'position:fixed;top:0;left:0;right:0;padding:10px;background:var(--danger);color:white;text-align:center;font-size:13px;font-weight:600;z-index:9999';
        document.body.appendChild(banner);
    }
    banner.textContent = message;
    banner.style.display = 'block';
    setTimeout(() => { banner.style.display = 'none'; }, 8000);
}

function hideErrorBanner() {
    const banner = document.getElementById('errorBanner');
    if (banner) banner.style.display = 'none';
}

// ============================================================
// Theme Toggle
// ============================================================

function initTheme() {
    document.body.setAttribute('data-theme', state.theme);
}

// ============================================================
// Keyboard Shortcuts
// ============================================================

function handleKeydown(e) {
    // Don't trigger shortcuts when typing in inputs
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

    switch (e.key.toLowerCase()) {
        case 'm': toggleMic(); break;
        case 'v': toggleCamera(); break;
        case 'h': toggleHandRaise(); break;
        case 'escape':
            if (el.screenShareOverlay && !el.screenShareOverlay.classList.contains('hidden')) {
                exitScreenShareFullscreen();
            } else {
                leaveRoom();
            }
            break;
    }
}

// ============================================================
// Initialize
// ============================================================

function init() {
    initTheme();
    connectSocket();
    initAlertSound();

    el.studentName.addEventListener('input', validateForm);
    el.roomCode.addEventListener('input', () => {
        el.roomCode.value = el.roomCode.value.toUpperCase();
        validateForm();
    });
    el.consentCheckbox.addEventListener('change', validateForm);

    el.joinBtn.addEventListener('click', joinRoom);
    el.toggleCameraBtn.addEventListener('click', toggleCamera);
    el.toggleMicBtn.addEventListener('click', toggleMic);
    el.leaveBtn.addEventListener('click', leaveRoom);
    el.shareScreenBtn.addEventListener('click', toggleScreenShare);

    // Hide share screen button by default (teacher must grant permission)
    el.shareScreenBtn.classList.add('hidden');

    // Hand raise & reactions
    el.handRaiseBtn.addEventListener('click', toggleHandRaise);
    el.reactionToggleBtn.addEventListener('click', () => {
        el.reactionMenu.classList.toggle('hidden');
    });
    document.querySelectorAll('.reaction-emoji').forEach(btn => {
        btn.addEventListener('click', () => sendReaction(btn.dataset.emoji));
    });

    // Chat
    el.chatSendBtn.addEventListener('click', sendChatMessage);
    el.chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') sendChatMessage();
    });

    // Keyboard shortcuts
    document.addEventListener('keydown', handleKeydown);

    // Screen share overlay exit button
    if (el.exitScreenShareBtn) {
        el.exitScreenShareBtn.addEventListener('click', exitScreenShareFullscreen);
    }

    // Auto-fill room code and name from URL params
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('room')) {
        el.roomCode.value = urlParams.get('room').toUpperCase();
    }
    if (urlParams.get('name')) {
        el.studentName.value = urlParams.get('name');
    }

    validateForm();
}

document.addEventListener('DOMContentLoaded', init);
