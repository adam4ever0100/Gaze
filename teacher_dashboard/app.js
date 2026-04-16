/**
 * Gaze — Teacher Dashboard Application
 * 
 * Features:
 * - Room creation & management
 * - Real-time student attention monitoring
 * - WebRTC video conferencing
 * - Teacher annotations/bookmarks
 * - Browser notifications for distracted students
 * - Network quality indicator
 * - Session history with playback, attendance, AI summary
 * - Dark/Light theme toggle
 * - Keyboard shortcuts
 */

// Auto-detect base path for API calls
// In production behind Caddy, teacher pages are at /teacher/ and API calls
// must go through /teacher/... so Caddy routes them to the backend.
const TEACHER_BASE = window.location.pathname.startsWith('/teacher') ? '/teacher' : '';

// ============================================================
// State
// ============================================================

const state = {
    socket: null,
    roomCode: null,
    sessionId: null,
    chart: null,
    distributionChart: null,
    authenticated: false,
    currentView: 'login',
    cachedStudents: [],
    // Video conferencing (LiveKit SFU)
    localStream: null,
    livekitRoom: null,
    cameraOn: true,
    micOn: true,
    videoCollapsed: false,
    isScreenSharing: false,
    screenStream: null,
    teacherName: 'Teacher',
    // Chat
    chatCollapsed: false,
    unreadCount: 0,
    // Hand raise
    handRaised: false,
    // Network quality tracking (legacy)
    networkStats: {},
    peers: {},
    networkInterval: null,
    // Notifications
    notificationsEnabled: false,
    // Theme
    theme: localStorage.getItem('gaze-theme') || 'dark',
    // Session detail modal
    activeModalSession: null,
    // Screen share tracking
    screenSharePeerSid: null,
    // Students allowed to screen share (tracked client-side for UI)
    shareAllowed: new Set()
};

// ============================================================
// DOM References
// ============================================================

const el = {
    // Login
    teacherPassword: document.getElementById('teacherPassword'),
    teacherName: document.getElementById('teacherName'),
    createRoomBtn: document.getElementById('createRoomBtn'),
    loginError: document.getElementById('loginError'),

    // Views
    loginView: document.getElementById('loginView'),
    dashboardView: document.getElementById('dashboardView'),
    historyView: document.getElementById('historyView'),

    // Dashboard Header
    roomCodeDisplay: document.getElementById('roomCodeDisplay'),
    sessionDuration: document.getElementById('sessionDuration'),
    exportBtn: document.getElementById('exportBtn'),
    pdfReportBtn: document.getElementById('pdfReportBtn'),
    endSessionBtn: document.getElementById('endSessionBtn'),

    // Stats
    classAverage: document.getElementById('classAverage'),
    classTrend: document.getElementById('classTrend'),
    activeStudents: document.getElementById('activeStudents'),
    focusedCount: document.getElementById('focusedCount'),
    distractedCount: document.getElementById('distractedCount'),

    // Charts
    attentionChart: document.getElementById('attentionChart'),
    distributionChart: document.getElementById('distributionChart'),

    // Students Table
    studentsTableBody: document.getElementById('studentsTableBody'),
    searchInput: document.getElementById('searchInput'),

    // Server status
    serverDot: document.getElementById('serverDot'),
    serverStatus: document.getElementById('serverStatus'),

    // Alerts
    alertContainer: document.getElementById('alertContainer'),

    // Video conferencing (elements may not exist on page)
    videoSection: document.getElementById('videoSection'),
    teacherVideoGrid: document.getElementById('teacherVideoGrid'),
    teacherLocalVideo: document.getElementById('teacherLocalVideo'),
    teacherVideoName: document.getElementById('teacherVideoName'),
    toggleTeacherCameraBtn: document.getElementById('toggleTeacherCameraBtn'),
    toggleTeacherMicBtn: document.getElementById('toggleTeacherMicBtn'),
    toggleVideoSectionBtn: document.getElementById('toggleVideoSectionBtn'),
    collapseIcon: document.getElementById('collapseIcon'),

    // Join Meeting button
    joinMeetingBtn: document.getElementById('joinMeetingBtn'),

    // Annotations
    annotationText: document.getElementById('annotationText'),
    annotationType: document.getElementById('annotationType'),
    addAnnotationBtn: document.getElementById('addAnnotationBtn'),
    annotationsList: document.getElementById('annotationsList'),

    // Theme
    themeToggle: document.getElementById('themeToggle'),
    themeIconSun: document.getElementById('themeIconSun'),
    themeIconMoon: document.getElementById('themeIconMoon'),

    // History
    sessionsList: document.getElementById('sessionsList'),

    // Session Detail Modal
    sessionDetailOverlay: document.getElementById('sessionDetailOverlay'),
    modalTitle: document.getElementById('modalTitle'),
    modalBody: document.getElementById('modalBody'),
    closeModal: document.getElementById('closeModal'),

    // Notification sound
    notifSound: document.getElementById('notifSound'),

    // Screen share overlay
    screenShareOverlay: document.getElementById('screenShareOverlay'),
    screenShareVideo: document.getElementById('screenShareVideo'),
    screenShareName: document.getElementById('screenShareName'),
    exitScreenShareBtn: document.getElementById('exitScreenShareBtn'),

    // Teacher meeting panel (added to HTML)
    shareScreenBtn: document.getElementById('shareScreenBtn'),
    teacherHandRaiseBtn: document.getElementById('teacherHandRaiseBtn'),
    teacherReactionMenu: document.getElementById('teacherReactionMenu'),
    teacherChatMessages: document.getElementById('teacherChatMessages'),
    teacherChatInput: document.getElementById('teacherChatInput'),
    chatBadge: document.getElementById('chatBadge')
};

// ============================================================
// Socket.IO Connection
// ============================================================

function connectSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    state.socket = io(window.location.origin, {
        transports: ['websocket', 'polling'],
        reconnection: true,
        reconnectionDelay: 1000,
        reconnectionAttempts: Infinity
    });

    state.socket.on('connect', () => {
        updateServerStatus(true);
    });

    state.socket.on('disconnect', () => {
        updateServerStatus(false);
    });

    state.socket.on('error', (data) => {
        showAlert(data.message || 'Connection error', 'error');
    });

    // Room events
    state.socket.on('room-created', handleRoomCreated);
    state.socket.on('student-joined', handleStudentJoined);
    state.socket.on('peer-left', handlePeerLeft);
    state.socket.on('score-update', handleScoreUpdate);
    state.socket.on('distraction-alert', handleDistractionAlert);
    state.socket.on('room-closed', () => {
        showAlert('Session ended', 'info');
        showView('login');
    });

    // LiveKit token
    state.socket.on('livekit-token', handleTeacherLivekitToken);

    // Chat
    state.socket.on('chat-message', handleTeacherChatMessage);

    // Screen sharing
    state.socket.on('screen-share-started', (data) => {
        console.log('[ScreenShare] Started from:', data.name);
        showAlert(`${data.name} is sharing their screen`, 'info');
        state.screenSharePeerSid = data.sid;
        if (el.screenShareName) {
            el.screenShareName.textContent = `${data.name} is sharing their screen`;
        }
    });
    state.socket.on('screen-share-stopped', (data) => {
        console.log('[ScreenShare] Stopped from sid:', data.sid);
        if (state.screenSharePeerSid === data.sid) {
            exitScreenShareFullscreen();
        }
    });

    // Annotations
    state.socket.on('annotation-added', handleAnnotationAdded);

    // Hand raise & reactions
    state.socket.on('hand-raised', handleTeacherHandRaised);
    state.socket.on('hand-lowered', handleTeacherHandLowered);
    state.socket.on('reaction-received', handleTeacherReaction);
}

// ============================================================
// Login & Room Creation
// ============================================================

function createRoom() {
    const password = el.teacherPassword.value;
    const name = el.teacherName.value.trim() || 'Teacher';

    if (!password) {
        el.loginError.textContent = 'Please enter the teacher password';
        el.loginError.classList.remove('hidden');
        return;
    }

    state.teacherName = name;
    el.loginError.classList.add('hidden');

    state.socket.emit('create-room', {
        teacher_name: name,
        password: password
    });
}

async function handleRoomCreated(data) {
    state.roomCode = data.room_code;
    state.authenticated = true;

    showView('dashboard');
    el.roomCodeDisplay.textContent = data.room_code;
    if (el.teacherVideoName) el.teacherVideoName.textContent = `${state.teacherName} (You)`;
    initChart();
    initDistributionChart();

    // Start duration timer
    setInterval(updateDuration, 1000);

    // Request notification permission
    requestNotificationPermission();

    // Start camera & request LiveKit token (teacher is always in the room)
    try {
        state.localStream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } },
            audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
        });
        if (el.teacherLocalVideo) el.teacherLocalVideo.srcObject = state.localStream;
    } catch (err) {
        console.error('Camera access error:', err);
        showAlert('Could not access camera. Video will be unavailable.', 'warning');
    }

    // Request LiveKit token to join the SFU room as host
    state.socket.emit('get-livekit-token');

    // Start network quality monitoring
    state.networkInterval = setInterval(updateNetworkQuality, 5000);
}

async function handleTeacherLivekitToken(data) {
    console.log('Teacher got LiveKit token, connecting to SFU...');
    try {
        await connectTeacherToLiveKit(data.url, data.token);
    } catch (err) {
        console.error('LiveKit connection error:', err);
        showAlert('Failed to connect to video server', 'error');
    }
}

async function connectTeacherToLiveKit(url, token) {
    const { Room, RoomEvent, Track, VideoPresets } = LivekitClient;

    const room = new Room({
        adaptiveStream: true,
        dynacast: true,
        videoCaptureDefaults: {
            resolution: VideoPresets.h720.resolution
        }
    });

    state.livekitRoom = room;

    // --- Track Subscribed ---
    room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
        const identity = participant.identity;
        const name = participant.name || identity;

        if (track.kind === Track.Kind.Video) {
            addRemoteVideoTile(identity, name, track);

            // Handle screen share
            if (publication.source === Track.Source.ScreenShare) {
                const stream = new MediaStream([track.mediaStreamTrack]);
                if (el.screenShareName) el.screenShareName.textContent = `${name} is sharing`;
                showScreenShareFullscreen(identity, stream);
            }
        } else if (track.kind === Track.Kind.Audio) {
            // Remove any existing audio element for this participant
            const existing = document.getElementById(`audio-${identity}`);
            if (existing) existing.remove();

            const audioEl = track.attach();
            audioEl.id = `audio-${identity}`;
            audioEl.autoplay = true;
            audioEl.setAttribute('playsinline', '');
            document.body.appendChild(audioEl);
            // Try to play immediately; if blocked, startAudio() on Join Meeting will unlock it
            audioEl.play().catch(() => {});
        }
    });

    // --- Track Unsubscribed ---
    room.on(RoomEvent.TrackUnsubscribed, (track, publication, participant) => {
        track.detach().forEach(e => e.remove());
        if (publication.source === Track.Source.ScreenShare) {
            exitScreenShareFullscreen();
        }
    });

    // --- Participant Connected ---
    room.on(RoomEvent.ParticipantConnected, (participant) => {
        console.log('[Teacher LK] ParticipantConnected:', participant.identity);
        // Subscribe to all their tracks
        participant.trackPublications.forEach((pub) => {
            if (!pub.isSubscribed) pub.setSubscribed(true);
        });
    });

    // --- Participant Disconnected ---
    room.on(RoomEvent.ParticipantDisconnected, (participant) => {
        console.log('[Teacher LK] ParticipantDisconnected:', participant.identity);
        removePeer(participant.identity);
    });

    // --- Room fully connected — sync all existing participants ---
    room.on(RoomEvent.Connected, () => {
        console.log('[Teacher LK] Room connected. Remote participants:', room.remoteParticipants.size);
        room.remoteParticipants.forEach((p) => {
            p.trackPublications.forEach((pub) => {
                if (!pub.isSubscribed) pub.setSubscribed(true);
                if (pub.isSubscribed && pub.track && pub.track.kind === Track.Kind.Video &&
                    pub.source !== Track.Source.ScreenShare) {
                    if (el.teacherVideoGrid) addRemoteVideoTile(p.identity, p.name || p.identity, pub.track);
                }
            });
        });
    });

    // Connect
    await room.connect(url, token);
    console.log('[Teacher LK] Connected to room:', room.name);

    // Publish local tracks
    if (state.localStream) {
        const videoTrack = state.localStream.getVideoTracks()[0];
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

    // Process already-connected participants immediately after connect()
    function syncTeacherParticipants() {
        room.remoteParticipants.forEach((p) => {
            p.trackPublications.forEach((pub) => {
                if (!pub.isSubscribed) pub.setSubscribed(true);
                if (pub.isSubscribed && pub.track && pub.track.kind === Track.Kind.Video &&
                    pub.source !== Track.Source.ScreenShare) {
                    if (el.teacherVideoGrid) addRemoteVideoTile(p.identity, p.name || p.identity, pub.track);
                }
            });
        });
    }

    syncTeacherParticipants();
    setTimeout(syncTeacherParticipants, 1000);
    setTimeout(syncTeacherParticipants, 3000);
}

function handleStudentJoined(data) {
    showAlert(`${data.name} joined the classroom`, 'success');
}

function handlePeerLeft(data) {
    showAlert(`${data.name} left the class`, 'warning');
    removePeer(data.sid);
}

function endSession() {
    if (!confirm('Are you sure you want to end this session?')) return;

    // Disconnect from LiveKit
    if (state.livekitRoom) {
        state.livekitRoom.disconnect();
        state.livekitRoom = null;
    }
    state.networkStats = {};

    if (state.networkInterval) clearInterval(state.networkInterval);

    // Stop local stream
    if (state.localStream) {
        state.localStream.getTracks().forEach(t => t.stop());
        state.localStream = null;
    }

    document.querySelectorAll('.remote-tile-teacher').forEach(t => t.remove());
    document.querySelectorAll('[id^="audio-"]').forEach(a => a.remove());

    state.socket.emit('leave-room');
    state.roomCode = null;
    state.authenticated = false;
    showView('login');
}

// ============================================================
// Score Updates
// ============================================================

function handleScoreUpdate(data) {
    const dashboard = data.dashboard;
    if (!dashboard) return;

    // Update stats
    el.classAverage.textContent = `${Math.round(dashboard.class_average * 100)}%`;
    el.activeStudents.textContent = dashboard.active_students;
    el.focusedCount.textContent = dashboard.status_counts?.focused || 0;
    el.distractedCount.textContent = dashboard.status_counts?.distracted || 0;

    // Trend indicator
    const trend = dashboard.class_average >= 0.7 ? '↑' : dashboard.class_average >= 0.4 ? '→' : '↓';
    el.classTrend.textContent = trend;

    // Update chart
    if (state.chart) {
        const now = new Date();
        state.chart.data.labels.push(now.toLocaleTimeString());
        state.chart.data.datasets[0].data.push(Math.round(dashboard.class_average * 100));

        if (state.chart.data.labels.length > 60) {
            state.chart.data.labels.shift();
            state.chart.data.datasets[0].data.shift();
        }
        state.chart.update('none');
    }

    // Update distribution chart
    if (state.distributionChart) {
        const counts = dashboard.status_counts || {};
        state.distributionChart.data.datasets[0].data = [
            counts.focused || 0,
            counts.partial || 0,
            counts.distracted || 0
        ];
        state.distributionChart.update('none');
    }

    // Update students table
    state.cachedStudents = dashboard.students || [];
    updateStudentsTable(state.cachedStudents);
}

function handleDistractionAlert(data) {
    showAlert(data.message, 'danger');
    playNotificationSound();
    showBrowserNotification('⚠️ Attention Alert', data.message);
}

// ============================================================
// Students Table
// ============================================================

function updateStudentsTable(students) {
    const search = el.searchInput.value.toLowerCase();
    const filtered = students.filter(s => s.name.toLowerCase().includes(search));

    el.studentsTableBody.innerHTML = filtered.map(s => {
        const scorePercent = Math.round(s.score * 100);
        const statusClass = s.status === 'Focused' ? 'badge-success' :
            s.status === 'Distracted' ? 'badge-danger' : 'badge-warning';
        const activityDot = s.active ? 'active' : 'inactive';
        const networkQuality = state.networkStats[s.sid] || 'unknown';
        const networkIcon = networkQuality === 'good' ? '🟢' :
            networkQuality === 'fair' ? '🟡' :
                networkQuality === 'poor' ? '🔴' : '⚪';

        return `
            <tr>
                <td><span class="student-name">${escapeHtml(s.name)}</span></td>
                <td>
                    <div class="score-bar-wrapper">
                        <div class="score-bar" style="width: ${scorePercent}%;
                            background: ${scorePercent >= 70 ? 'var(--success)' : scorePercent >= 40 ? 'var(--warning)' : 'var(--danger)'}">
                        </div>
                        <span class="score-text">${scorePercent}%</span>
                    </div>
                </td>
                <td><span class="status-badge ${statusClass}">${s.status}</span></td>
                <td title="Network: ${networkQuality}">${networkIcon}</td>
                <td><span class="activity-dot ${activityDot}"></span></td>
                <td>
                    <button class="btn-share-toggle ${state.shareAllowed.has(s.sid) ? 'active' : ''}" 
                            onclick="toggleSharePermission('${s.sid}')" 
                            title="${state.shareAllowed.has(s.sid) ? 'Revoke screen share' : 'Allow screen share'}">
                        📺
                    </button>
                </td>
            </tr>
        `;
    }).join('');
}

// ============================================================
// Annotations
// ============================================================

function addAnnotation() {
    const text = el.annotationText.value.trim();
    if (!text) return;

    state.socket.emit('add-annotation', {
        text: text,
        type: el.annotationType.value
    });

    el.annotationText.value = '';
}

function handleAnnotationAdded(data) {
    const dt = new Date(data.timestamp * 1000);
    const typeEmoji = { note: '📝', bookmark: '🔖', warning: '⚠️', praise: '⭐' };

    const item = document.createElement('div');
    item.className = 'annotation-item';
    item.innerHTML = `
        <span class="annotation-type">${typeEmoji[data.type] || '📝'}</span>
        <span class="annotation-text">${escapeHtml(data.text)}</span>
        <span class="annotation-time">${dt.toLocaleTimeString()} · ${data.class_avg}% avg</span>
    `;

    el.annotationsList.prepend(item);
}

// ============================================================
// Charts
// ============================================================

function initChart() {
    const ctx = el.attentionChart.getContext('2d');
    state.chart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'Class Average',
                data: [],
                borderColor: '#6366f1',
                backgroundColor: 'rgba(99, 102, 241, 0.1)',
                borderWidth: 2,
                fill: true,
                tension: 0.4,
                pointRadius: 0,
                pointHoverRadius: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: { min: 0, max: 100, grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#888' } },
                x: { grid: { display: false }, ticks: { color: '#888', maxTicksLimit: 10 } }
            },
            plugins: { legend: { display: false } },
            animation: false
        }
    });
}

function initDistributionChart() {
    const ctx = el.distributionChart.getContext('2d');
    state.distributionChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Focused', 'Partial', 'Distracted'],
            datasets: [{
                data: [0, 0, 0],
                backgroundColor: ['#22c55e', '#f59e0b', '#ef4444'],
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '65%',
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { color: '#888', padding: 12, font: { size: 12 } }
                }
            }
        }
    });
}

// ============================================================
// Views & Navigation
// ============================================================

function showView(view) {
    state.currentView = view;

    el.loginView.classList.toggle('hidden', view !== 'login');
    el.loginView.classList.toggle('active', view === 'login');
    el.dashboardView.classList.toggle('hidden', view !== 'dashboard');
    el.dashboardView.classList.toggle('active', view === 'dashboard');
    el.historyView.classList.toggle('hidden', view !== 'history');
    el.historyView.classList.toggle('active', view === 'history');

    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.toggle('active', item.dataset.view === view);
    });

    if (view === 'history') loadSessionHistory();
}

function showAlert(msg, type = 'info') {
    const alert = document.createElement('div');
    alert.className = `alert-toast alert-${type}`;
    alert.textContent = msg;
    el.alertContainer.appendChild(alert);
    setTimeout(() => {
        alert.style.opacity = '0';
        setTimeout(() => alert.remove(), 300);
    }, 4000);
}

// ============================================================
// Session History & Playback
// ============================================================

async function loadSessionHistory() {
    try {
        const res = await fetch(`${TEACHER_BASE}/sessions`);
        const data = await res.json();
        if (!data.success) return;

        el.sessionsList.innerHTML = data.sessions.length === 0
            ? '<div class="empty-state">No past sessions yet</div>'
            : data.sessions.map(s => {
                const dt = new Date(s.started_at * 1000);
                const avg = Math.round((s.avg_score || 0) * 100);
                const durMin = Math.floor(s.duration / 60);
                return `
                    <div class="session-card" onclick="openSessionDetail(${s.id})">
                        <div class="session-info">
                            <h4>Room ${escapeHtml(s.room_id)}</h4>
                            <p>${dt.toLocaleDateString()} at ${dt.toLocaleTimeString()} · ${durMin}m · ${s.student_count} student(s)</p>
                        </div>
                        <div class="session-stats">
                            <span class="stat-chip">Avg: ${avg}%</span>
                            ${s.annotation_count ? `<span class="stat-chip">📝 ${s.annotation_count}</span>` : ''}
                            <span class="stat-chip action">View Details →</span>
                        </div>
                    </div>
                `;
            }).join('');
    } catch (e) {
        el.sessionsList.innerHTML = '<div class="empty-state">Failed to load sessions</div>';
    }
}

async function openSessionDetail(sessionId) {
    state.activeModalSession = sessionId;
    el.sessionDetailOverlay.classList.remove('hidden');
    el.modalTitle.textContent = `Session ${sessionId}`;

    // Set first tab active
    document.querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active'));
    document.querySelector('.modal-tab[data-tab="summary"]').classList.add('active');

    loadSessionTab('summary', sessionId);
}

async function loadSessionTab(tab, sessionId) {
    el.modalBody.innerHTML = '<div class="modal-loading">Loading...</div>';

    try {
        let endpoint = '';
        switch (tab) {
            case 'summary': endpoint = `${TEACHER_BASE}/sessions/${sessionId}/summary`; break;
            case 'attendance': endpoint = `${TEACHER_BASE}/sessions/${sessionId}/attendance`; break;
            case 'ai-summary': endpoint = `${TEACHER_BASE}/sessions/${sessionId}/ai-summary`; break;
            case 'annotations': endpoint = `${TEACHER_BASE}/sessions/${sessionId}/annotations`; break;
            case 'timeline': endpoint = `${TEACHER_BASE}/sessions/${sessionId}/analytics`; break;
        }

        const res = await fetch(endpoint);
        const data = await res.json();

        switch (tab) {
            case 'summary': renderSummaryTab(data); break;
            case 'attendance': renderAttendanceTab(data); break;
            case 'ai-summary': renderAISummaryTab(data); break;
            case 'annotations': renderAnnotationsTab(data); break;
            case 'timeline': renderTimelineTab(data); break;
        }
    } catch (e) {
        el.modalBody.innerHTML = '<div class="modal-loading">Error loading data</div>';
    }
}

function renderSummaryTab(data) {
    const dt = new Date(data.started_at * 1000);
    const durMin = Math.floor(data.duration / 60);
    el.modalBody.innerHTML = `
        <div class="detail-grid">
            <div class="detail-item"><span>Room</span><strong>${data.room_id}</strong></div>
            <div class="detail-item"><span>Teacher</span><strong>${escapeHtml(data.teacher_name || '—')}</strong></div>
            <div class="detail-item"><span>Date</span><strong>${dt.toLocaleDateString()}</strong></div>
            <div class="detail-item"><span>Duration</span><strong>${durMin}m</strong></div>
            <div class="detail-item"><span>Students</span><strong>${data.student_count}</strong></div>
        </div>
        <h4 style="margin: 20px 0 10px;">Student Performance</h4>
        <table class="students-table compact">
            <thead><tr><th>Name</th><th>Avg</th><th>Min</th><th>Max</th><th>Records</th></tr></thead>
            <tbody>${(data.students || []).map(s => `
                <tr>
                    <td>${escapeHtml(s.name)}</td>
                    <td>${Math.round(s.avg_score * 100)}%</td>
                    <td>${Math.round(s.min_score * 100)}%</td>
                    <td>${Math.round(s.max_score * 100)}%</td>
                    <td>${s.total_records}</td>
                </tr>
            `).join('')}</tbody>
        </table>
    `;
}

function renderAttendanceTab(data) {
    el.modalBody.innerHTML = `
        <div class="detail-grid">
            <div class="detail-item"><span>Total Students</span><strong>${data.total_students}</strong></div>
        </div>
        <table class="students-table compact">
            <thead><tr><th>Name</th><th>Joined</th><th>Duration</th><th>Avg Attention</th><th>Present at End</th></tr></thead>
            <tbody>${(data.students || []).map(s => {
        const joinDt = new Date(s.joined_at * 1000);
        return `
                    <tr>
                        <td>${escapeHtml(s.name)}</td>
                        <td>${joinDt.toLocaleTimeString()}</td>
                        <td>${s.duration_formatted}</td>
                        <td>${s.avg_attention}%</td>
                        <td>${s.was_present_at_end ? '✅' : '❌'}</td>
                    </tr>
                `;
    }).join('')}</tbody>
        </table>
    `;
}

function renderAISummaryTab(data) {
    el.modalBody.innerHTML = `
        <div class="ai-summary-card">
            <div class="ai-header">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="20" height="20">
                    <path d="M12 2a7 7 0 0 1 7 7c0 2.38-1.19 4.47-3 5.74V17a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1v-2.26C6.19 13.47 5 11.38 5 9a7 7 0 0 1 7-7z"/>
                    <line x1="9" y1="21" x2="15" y2="21"/><line x1="10" y1="24" x2="14" y2="24"/>
                </svg>
                <h4>AI Analysis</h4>
            </div>
            <p class="ai-summary-text">${escapeHtml(data.summary || 'No summary available.')}</p>

            <div class="ai-metrics">
                <div class="ai-metric"><span>Overall Avg</span><strong>${data.overall_avg || 0}%</strong></div>
                <div class="ai-metric"><span>Duration</span><strong>${data.duration_minutes || 0}m</strong></div>
                <div class="ai-metric"><span>Peak</span><strong>${data.peak_engagement || 0}%</strong></div>
                <div class="ai-metric"><span>Dips</span><strong>${data.dip_count || 0}</strong></div>
            </div>

            ${(data.highlights || []).length > 0 ? `
                <h5>Highlights</h5>
                <ul class="ai-list">${data.highlights.map(h => `<li>${escapeHtml(h)}</li>`).join('')}</ul>
            ` : ''}

            ${(data.recommendations || []).length > 0 ? `
                <h5>Recommendations</h5>
                <ul class="ai-list">${data.recommendations.map(r => `<li>${escapeHtml(r)}</li>`).join('')}</ul>
            ` : ''}
        </div>
    `;
}

function renderAnnotationsTab(data) {
    const anns = data.annotations || [];
    if (anns.length === 0) {
        el.modalBody.innerHTML = '<div class="modal-loading">No annotations for this session</div>';
        return;
    }

    const typeEmoji = { note: '📝', bookmark: '🔖', warning: '⚠️', praise: '⭐' };
    el.modalBody.innerHTML = `
        <div class="annotations-timeline">
            ${anns.map(a => {
        const dt = new Date(a.timestamp * 1000);
        return `
                    <div class="annotation-timeline-item">
                        <span class="annotation-type">${typeEmoji[a.annotation_type] || '📝'}</span>
                        <div>
                            <div class="annotation-text">${escapeHtml(a.text)}</div>
                            <div class="annotation-time">${dt.toLocaleTimeString()} · Class avg: ${Math.round((a.class_avg_at_time || 0) * 100)}%</div>
                        </div>
                    </div>
                `;
    }).join('')}
        </div>
    `;
}

function renderTimelineTab(data) {
    const timelines = data.timelines || {};
    const names = Object.keys(timelines);

    if (names.length === 0) {
        el.modalBody.innerHTML = '<div class="modal-loading">No timeline data</div>';
        return;
    }

    el.modalBody.innerHTML = '<canvas id="timelineChart" style="height:300px"></canvas>';

    const colors = ['#6366f1', '#22c55e', '#f59e0b', '#ef4444', '#06b6d4', '#8b5cf6', '#ec4899', '#14b8a6'];
    const datasets = names.map((name, i) => {
        const records = timelines[name];
        return {
            label: name,
            data: records.map(r => Math.round(r.attention_score * 100)),
            borderColor: colors[i % colors.length],
            borderWidth: 1.5,
            fill: false,
            tension: 0.4,
            pointRadius: 0
        };
    });

    // Use first student's timestamps as labels
    const firstRecords = timelines[names[0]] || [];
    const labels = firstRecords.map(r => {
        const dt = new Date(r.timestamp * 1000);
        return dt.toLocaleTimeString();
    });

    new Chart(document.getElementById('timelineChart'), {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: { min: 0, max: 100, grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#888' } },
                x: { grid: { display: false }, ticks: { color: '#888', maxTicksLimit: 12 } }
            },
            plugins: {
                legend: { labels: { color: '#888', padding: 8 } }
            }
        }
    });
}

// ============================================================
// Export
// ============================================================

function exportCurrentSession() {
    if (!state.roomCode) return;
    fetch(`${TEACHER_BASE}/sessions/1/export`)
        .then(r => r.blob())
        .then(blob => {
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `session_${state.roomCode}.csv`;
            a.click();
            URL.revokeObjectURL(url);
        });
}

// ============================================================
// LiveKit — Peer Management (replaced manual WebRTC)
// ============================================================

function removePeer(identity) {
    delete state.networkStats[identity];
    const tile = document.getElementById(`teacher-tile-${identity}`);
    if (tile) tile.remove();
    const audio = document.getElementById(`audio-${identity}`);
    if (audio) audio.remove();
}

// ============================================================
// Video Tile Management
// ============================================================

function addRemoteVideoTile(identity, name, livekitTrack) {
    // Guard: video grid might not be in DOM yet (hidden)
    if (!el.teacherVideoGrid) return;

    const existing = document.getElementById(`teacher-tile-${identity}`);
    if (existing) {
        const v = existing.querySelector('video');
        if (v && !v.srcObject) livekitTrack.attach(v);
        return;
    }

    const tile = document.createElement('div');
    tile.className = 'video-tile-teacher remote-tile-teacher';
    tile.id = `teacher-tile-${identity}`;

    tile.innerHTML = `
        <video autoplay playsinline></video>
        <div class="video-label-teacher">
            <span class="video-name-teacher">${escapeHtml(name)}</span>
            <span class="network-badge" id="net-${identity}">⚪</span>
        </div>
    `;

    const videoEl = tile.querySelector('video');
    livekitTrack.attach(videoEl);

    // Double-click tile to view fullscreen
    tile.addEventListener('dblclick', () => {
        if (videoEl && videoEl.srcObject) {
            if (el.screenShareName) el.screenShareName.textContent = escapeHtml(name);
            showScreenShareFullscreen(identity, videoEl.srcObject);
        }
    });
    tile.style.cursor = 'pointer';
    tile.title = 'Double-click to view fullscreen';

    el.teacherVideoGrid.appendChild(tile);
}

// ============================================================
// Camera / Mic Controls
// ============================================================

function toggleTeacherCamera() {
    if (!state.localStream) return;
    state.cameraOn = !state.cameraOn;
    state.localStream.getVideoTracks().forEach(t => { t.enabled = state.cameraOn; });
    el.toggleTeacherCameraBtn.classList.toggle('muted', !state.cameraOn);
    if (state.livekitRoom) {
        state.livekitRoom.localParticipant.setCameraEnabled(state.cameraOn);
    }
}

function toggleTeacherMic() {
    if (!state.localStream) return;
    state.micOn = !state.micOn;
    state.localStream.getAudioTracks().forEach(t => { t.enabled = state.micOn; });
    el.toggleTeacherMicBtn.classList.toggle('muted', !state.micOn);
    if (state.livekitRoom) {
        state.livekitRoom.localParticipant.setMicrophoneEnabled(state.micOn);
    }
}

function muteAll() {
    if (state.socket) {
        state.socket.emit('mute-all');
        showAlert('🔇 All students muted', 'info');
    }
}

// ============================================================
// Screen Sharing
// ============================================================

async function toggleTeacherScreenShare() {
    if (state.isScreenSharing) {
        stopTeacherScreenShare();
        return;
    }

    try {
        state.screenStream = await navigator.mediaDevices.getDisplayMedia({
            video: { cursor: 'always' },
            audio: false
        });

        state.isScreenSharing = true;
        el.shareScreenBtn.classList.add('active');

        // Publish screen share via LiveKit
        const screenTrack = state.screenStream.getVideoTracks()[0];
        if (state.livekitRoom) {
            await state.livekitRoom.localParticipant.publishTrack(screenTrack, {
                name: 'screen',
                source: LivekitClient.Track.Source.ScreenShare
            });
        }

        // Show screen share in local video
        el.teacherLocalVideo.srcObject = state.screenStream;

        state.socket.emit('start-screen-share', {});

        // Auto-stop when user clicks browser's "Stop sharing"
        screenTrack.onended = () => stopTeacherScreenShare();

    } catch (err) {
        console.log('Screen share cancelled:', err.message);
    }
}

function stopTeacherScreenShare() {
    if (!state.isScreenSharing) return;
    state.isScreenSharing = false;
    el.shareScreenBtn.classList.remove('active');

    // Unpublish screen share from LiveKit
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

    // Restore camera
    if (state.localStream) {
        el.teacherLocalVideo.srcObject = state.localStream;
    }

    state.socket.emit('stop-screen-share', {});
}


// ============================================================
// Screen Share Permission Management
// ============================================================

function toggleSharePermission(sid) {
    if (state.shareAllowed.has(sid)) {
        revokeScreenShare(sid);
    } else {
        grantScreenShare(sid);
    }
}

function grantScreenShare(sid) {
    state.shareAllowed.add(sid);
    state.socket.emit('grant-screen-share', { target_sid: sid });
    const student = state.cachedStudents.find(s => s.sid === sid);
    showAlert(`📺 Screen share granted to ${student ? student.name : 'student'}`, 'success');
    updateStudentsTable(state.cachedStudents);
}

function revokeScreenShare(sid) {
    state.shareAllowed.delete(sid);
    state.socket.emit('revoke-screen-share', { target_sid: sid });
    const student = state.cachedStudents.find(s => s.sid === sid);
    showAlert(`Screen share revoked from ${student ? student.name : 'student'}`, 'info');
    updateStudentsTable(state.cachedStudents);
}

// ============================================================
// Chat (Teacher Side)
// ============================================================

function handleTeacherChatMessage(data) {
    if (!el.teacherChatMessages) return; // meeting panel not visible yet is fine
    const msgEl = document.createElement('div');
    msgEl.className = `chat-msg ${data.is_teacher ? 'teacher' : ''}`;
    const time = new Date(data.timestamp * 1000);
    const timeStr = time.toLocaleTimeString('en', { hour: '2-digit', minute: '2-digit' });
    msgEl.innerHTML = `
        <span class="chat-sender">${escapeHtml(data.sender)}</span>
        <span class="chat-text">${escapeHtml(data.message)}</span>
        <span class="chat-time">${timeStr}</span>
    `;
    el.teacherChatMessages.appendChild(msgEl);
    el.teacherChatMessages.scrollTop = el.teacherChatMessages.scrollHeight;

    // Show unread badge if meeting panel is hidden
    const videoSection = document.getElementById('videoSection');
    if (videoSection && videoSection.classList.contains('hidden')) {
        state.unreadCount++;
        if (el.chatBadge) {
            el.chatBadge.textContent = state.unreadCount;
            el.chatBadge.classList.remove('hidden');
        }
    }

    if (!data.is_teacher) playNotificationSound();
}

function sendTeacherChatMessage() {
    if (!el.teacherChatInput) return;
    const msg = el.teacherChatInput.value.trim();
    if (!msg || !state.socket) return;
    state.socket.emit('send-message', { message: msg });
    el.teacherChatInput.value = '';
}

function toggleChatPanel() {
    state.chatCollapsed = !state.chatCollapsed;
    el.teacherChatMessages.classList.toggle('collapsed', state.chatCollapsed);
    const input = el.teacherChatInput.parentElement;
    input.classList.toggle('collapsed', state.chatCollapsed);

    if (!state.chatCollapsed) {
        state.unreadCount = 0;
        el.chatBadge.classList.add('hidden');
    }
}

// ============================================================
// Hand Raise & Reactions (Teacher Controls)
// ============================================================

function toggleTeacherHandRaise() {
    state.handRaised = !state.handRaised;
    if (state.handRaised) {
        state.socket.emit('hand-raise');
        el.teacherHandRaiseBtn.classList.add('active');
        el.teacherHandRaiseBtn.style.animation = 'pulse 1s infinite';
    } else {
        state.socket.emit('hand-lower');
        el.teacherHandRaiseBtn.classList.remove('active');
        el.teacherHandRaiseBtn.style.animation = '';
    }
}

function sendTeacherReaction(emoji) {
    state.socket.emit('reaction', { emoji });
    el.teacherReactionMenu.classList.add('hidden');
}

function toggleVideoSection() {
    state.videoCollapsed = !state.videoCollapsed;
    if (el.teacherVideoGrid) {
        el.teacherVideoGrid.classList.toggle('collapsed', state.videoCollapsed);
    }
}

function leaveMeeting() {
    const panel = document.getElementById('videoSection');
    const bar = document.getElementById('joinMeetingBar');
    if (panel) panel.classList.add('hidden');
    if (bar) bar.classList.remove('hidden');
    showAlert('Left meeting view — still monitoring students', 'info');
}

// ============================================================
// Network Quality Indicator
// ============================================================

async function updateNetworkQuality() {
    for (const [sid, pc] of Object.entries(state.peers)) {
        try {
            const stats = await pc.getStats();
            let packetLoss = 0;
            let rtt = 0;

            stats.forEach(report => {
                if (report.type === 'inbound-rtp' && report.kind === 'video') {
                    packetLoss = report.packetsLost || 0;
                }
                if (report.type === 'candidate-pair' && report.state === 'succeeded') {
                    rtt = report.currentRoundTripTime || 0;
                }
            });

            let quality = 'good';
            if (rtt > 0.3 || packetLoss > 50) quality = 'poor';
            else if (rtt > 0.15 || packetLoss > 10) quality = 'fair';

            state.networkStats[sid] = quality;

            const badge = document.getElementById(`net-${sid}`);
            if (badge) {
                badge.textContent = quality === 'good' ? '🟢' : quality === 'fair' ? '🟡' : '🔴';
            }
        } catch (e) { /* ignore stats errors */ }
    }
}

// ============================================================
// Browser Notifications
// ============================================================

function requestNotificationPermission() {
    if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission().then(perm => {
            state.notificationsEnabled = perm === 'granted';
        });
    } else {
        state.notificationsEnabled = Notification.permission === 'granted';
    }
}

function showBrowserNotification(title, body) {
    if (!state.notificationsEnabled || document.hasFocus()) return;
    try {
        new Notification(title, { body, icon: '/static/favicon.ico' });
    } catch (e) { /* notifications may not be available */ }
}

function playNotificationSound() {
    try {
        el.notifSound.currentTime = 0;
        el.notifSound.play().catch(() => { });
    } catch (e) { }
}

// ============================================================
// Theme Toggle
// ============================================================

function initTheme() {
    document.body.setAttribute('data-theme', state.theme);
    updateThemeIcons();
}

function toggleTheme() {
    state.theme = state.theme === 'dark' ? 'light' : 'dark';
    document.body.setAttribute('data-theme', state.theme);
    localStorage.setItem('gaze-theme', state.theme);
    updateThemeIcons();
}

function updateThemeIcons() {
    el.themeIconSun.classList.toggle('hidden', state.theme === 'dark');
    el.themeIconMoon.classList.toggle('hidden', state.theme === 'light');
}

// ============================================================
// Keyboard Shortcuts
// ============================================================

function handleKeydown(e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;

    switch (e.key.toLowerCase()) {
        case 'escape':
            if (!el.screenShareOverlay.classList.contains('hidden')) {
                exitScreenShareFullscreen();
            } else if (!el.sessionDetailOverlay.classList.contains('hidden')) {
                el.sessionDetailOverlay.classList.add('hidden');
            }
            break;
    }
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
// Helpers
// ============================================================

function updateServerStatus(connected) {
    el.serverDot.classList.toggle('connected', connected);
    el.serverStatus.textContent = connected ? 'Connected' : 'Disconnected';
}

let sessionStartTime = Date.now();

function updateDuration() {
    const elapsed = Math.floor((Date.now() - sessionStartTime) / 1000);
    const mins = Math.floor(elapsed / 60).toString().padStart(2, '0');
    const secs = (elapsed % 60).toString().padStart(2, '0');
    el.sessionDuration.textContent = `${mins}:${secs}`;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ============================================================
// Initialization
// ============================================================

function init() {
    initTheme();
    connectSocket();

    // Login
    el.createRoomBtn.addEventListener('click', createRoom);
    el.teacherPassword.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') createRoom();
    });

    // Nav
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const view = item.dataset.view;
            if (view === 'dashboard' && !state.authenticated) showView('login');
            else showView(view);
        });
    });

    // Dashboard actions
    el.exportBtn.addEventListener('click', exportCurrentSession);
    el.pdfReportBtn.addEventListener('click', () => {
        if (state.sessionId) {
            window.open(`/sessions/${state.sessionId}/report.pdf`, '_blank');
        } else {
            showAlert('No active session to generate a report for.', 'warning');
        }
    });
    el.endSessionBtn.addEventListener('click', endSession);
    el.searchInput.addEventListener('input', () => updateStudentsTable(state.cachedStudents));

    // Join Meeting button — shows teacher video meeting panel inline
    el.joinMeetingBtn.addEventListener('click', () => {
        if (!state.roomCode) {
            showAlert('No active room to join.', 'warning');
            return;
        }
        const panel = document.getElementById('videoSection');
        const bar = document.getElementById('joinMeetingBar');
        if (panel) panel.classList.remove('hidden');
        if (bar) bar.classList.add('hidden');
        // Update room badge in panel
        const badge = document.getElementById('meetingRoomBadge');
        if (badge) badge.textContent = state.roomCode;
        // Reset unread count
        state.unreadCount = 0;
        if (el.chatBadge) el.chatBadge.classList.add('hidden');

        // KEY FIX: Unlock audio playback (browsers block autoplay until user gesture)
        if (state.livekitRoom) {
            state.livekitRoom.startAudio().catch(e => console.warn('[Audio] startAudio:', e));
        }

        // Re-assign local video srcObject — browser may need this after element becomes visible
        if (el.teacherLocalVideo && state.localStream) {
            el.teacherLocalVideo.srcObject = state.localStream;
            el.teacherLocalVideo.play().catch(() => {});
        }

        // Explicitly play any already-attached student audio elements
        document.querySelectorAll('[id^="audio-"]').forEach(a => {
            a.play().catch(() => {});
        });

        showAlert('Joined meeting as host 🎤', 'success');
    });

    // Annotations
    el.addAnnotationBtn.addEventListener('click', addAnnotation);
    el.annotationText.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') addAnnotation();
    });

    // Theme
    el.themeToggle.addEventListener('click', toggleTheme);

    // Session detail modal
    el.closeModal.addEventListener('click', () => el.sessionDetailOverlay.classList.add('hidden'));
    document.querySelectorAll('.modal-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            if (state.activeModalSession) loadSessionTab(tab.dataset.tab, state.activeModalSession);
        });
    });

    // Keyboard shortcuts
    document.addEventListener('keydown', handleKeydown);

    // Screen share overlay exit button
    if (el.exitScreenShareBtn) {
        el.exitScreenShareBtn.addEventListener('click', exitScreenShareFullscreen);
    }

    showView('login');
}


// ============================================================
// Hand Raise & Reactions (Teacher Side)
// ============================================================

function handleTeacherHandRaised(data) {
    showAlert(`✋ ${data.name} raised their hand!`, 'warning');

    // Mark the student row
    const row = document.querySelector(`[data-sid="${data.sid}"]`);
    if (row) {
        const nameCell = row.querySelector('.student-name');
        if (nameCell && !nameCell.querySelector('.hand-icon')) {
            const icon = document.createElement('span');
            icon.className = 'hand-icon';
            icon.textContent = ' ✋';
            icon.style.animation = 'pulse 1s infinite';
            icon.dataset.sid = data.sid;
            nameCell.appendChild(icon);
        }
    }
}

function handleTeacherHandLowered(data) {
    const icons = document.querySelectorAll(`.hand-icon[data-sid="${data.sid}"]`);
    icons.forEach(i => i.remove());
}

function handleTeacherReaction(data) {
    // Show a floating reaction bubble
    const bubble = document.createElement('div');
    bubble.textContent = `${data.emoji} ${data.name}`;
    bubble.style.cssText = `
        position: fixed; bottom: 20px; right: 20px; padding: 10px 18px;
        background: var(--bg-card); border: 1px solid var(--border); border-radius: 16px;
        font-size: 14px; font-weight: 500; z-index: 100; box-shadow: 0 8px 24px rgba(0,0,0,0.3);
        animation: slideInRight 0.3s ease;
    `;
    document.body.appendChild(bubble);
    setTimeout(() => {
        bubble.style.opacity = '0';
        bubble.style.transition = 'opacity 0.3s';
        setTimeout(() => bubble.remove(), 300);
    }, 2500);
}


document.addEventListener('DOMContentLoaded', init);
