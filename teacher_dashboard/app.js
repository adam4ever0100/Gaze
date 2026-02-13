/**
 * Teacher Dashboard - JavaScript
 * 
 * Handles:
 * - Real-time data polling
 * - Chart.js visualization
 * - Student table updates
 * - UI state management
 */

// Configuration
const CONFIG = {
    pollInterval: 2000,  // 2 seconds
    apiBase: window.location.origin,
    maxHistoryPoints: 60
};

// Application state
const state = {
    meetingId: 'default',
    polling: false,
    pollTimer: null,
    chart: null
};

// DOM Elements
const elements = {
    // Status
    serverDot: document.getElementById('serverDot'),
    serverStatus: document.getElementById('serverStatus'),

    // Stats
    classAverage: document.getElementById('classAverage'),
    classBadge: document.getElementById('classBadge'),
    studentCount: document.getElementById('studentCount'),
    sessionDuration: document.getElementById('sessionDuration'),
    focusedCount: document.getElementById('focusedCount'),

    // Distribution
    focusedBar: document.getElementById('focusedBar'),
    partialBar: document.getElementById('partialBar'),
    distractedBar: document.getElementById('distractedBar'),
    focusedPercent: document.getElementById('focusedPercent'),
    partialPercent: document.getElementById('partialPercent'),
    distractedPercent: document.getElementById('distractedPercent'),

    // Table
    studentsTableBody: document.getElementById('studentsTableBody'),
    searchInput: document.getElementById('searchInput'),
    meetingSelect: document.getElementById('meetingSelect')
};

// ============================================================
// Chart Initialization
// ============================================================

function initChart() {
    const ctx = document.getElementById('engagementChart').getContext('2d');

    const gradient = ctx.createLinearGradient(0, 0, 0, 250);
    gradient.addColorStop(0, 'rgba(99, 102, 241, 0.3)');
    gradient.addColorStop(1, 'rgba(99, 102, 241, 0.0)');

    state.chart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'Attention %',
                data: [],
                borderColor: '#6366f1',
                backgroundColor: gradient,
                fill: true,
                tension: 0.4,
                pointRadius: 0,
                pointHoverRadius: 6,
                pointHoverBackgroundColor: '#6366f1',
                pointHoverBorderColor: '#fff',
                borderWidth: 2
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                mode: 'index',
                intersect: false
            },
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    backgroundColor: '#1a1a2e',
                    titleColor: '#fff',
                    bodyColor: '#a0a0b8',
                    borderColor: 'rgba(255, 255, 255, 0.1)',
                    borderWidth: 1,
                    padding: 12,
                    displayColors: false,
                    callbacks: {
                        label: function (context) {
                            return `Attention: ${context.parsed.y}%`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    grid: {
                        color: 'rgba(255, 255, 255, 0.05)',
                        drawBorder: false
                    },
                    ticks: {
                        color: '#6b6b80',
                        maxTicksLimit: 8
                    }
                },
                y: {
                    min: 0,
                    max: 100,
                    grid: {
                        color: 'rgba(255, 255, 255, 0.05)',
                        drawBorder: false
                    },
                    ticks: {
                        color: '#6b6b80',
                        callback: function (value) {
                            return value + '%';
                        }
                    }
                }
            }
        }
    });
}

function updateChart(labels, data) {
    if (!state.chart) return;

    state.chart.data.labels = labels;
    state.chart.data.datasets[0].data = data;
    state.chart.update('none');
}

// ============================================================
// Data Fetching
// ============================================================

async function fetchDashboardData() {
    try {
        const response = await fetch(`${CONFIG.apiBase}/dashboard?meeting_id=${state.meetingId}`);
        const data = await response.json();

        if (data.success) {
            updateStats(data);
            updateDistribution(data);
            updateStudentsTable(data.students);
            updateServerStatus(true);
        }
    } catch (error) {
        console.error('Failed to fetch dashboard data:', error);
        updateServerStatus(false);
    }
}

async function fetchHistoryData() {
    try {
        const response = await fetch(`${CONFIG.apiBase}/dashboard/history?meeting_id=${state.meetingId}&limit=${CONFIG.maxHistoryPoints}`);
        const data = await response.json();

        if (data.success) {
            updateChart(data.labels, data.data);
        }
    } catch (error) {
        console.error('Failed to fetch history data:', error);
    }
}

async function fetchMeetings() {
    try {
        const response = await fetch(`${CONFIG.apiBase}/meetings`);
        const data = await response.json();

        if (data.success && data.meetings.length > 0) {
            updateMeetingSelect(data.meetings);
        }
    } catch (error) {
        console.error('Failed to fetch meetings:', error);
    }
}

// ============================================================
// UI Updates
// ============================================================

function updateServerStatus(connected) {
    if (connected) {
        elements.serverDot.classList.add('connected');
        elements.serverStatus.textContent = 'Connected';
    } else {
        elements.serverDot.classList.remove('connected');
        elements.serverStatus.textContent = 'Disconnected';
    }
}

function updateStats(data) {
    // Class average
    const avgPercent = Math.round(data.class_average * 100);
    elements.classAverage.textContent = `${avgPercent}%`;
    elements.classBadge.textContent = data.class_status;

    // Student count
    elements.studentCount.textContent = data.active_students;

    // Session duration
    const duration = data.session_duration;
    const minutes = Math.floor(duration / 60).toString().padStart(2, '0');
    const seconds = (duration % 60).toString().padStart(2, '0');
    elements.sessionDuration.textContent = `${minutes}:${seconds}`;

    // Focused count
    elements.focusedCount.textContent = data.status_counts.focused;
}

function updateDistribution(data) {
    const total = data.active_students || 1;
    const counts = data.status_counts;

    const focusedPct = Math.round((counts.focused / total) * 100);
    const partialPct = Math.round((counts.partial / total) * 100);
    const distractedPct = Math.round((counts.distracted / total) * 100);

    elements.focusedBar.style.width = `${focusedPct}%`;
    elements.partialBar.style.width = `${partialPct}%`;
    elements.distractedBar.style.width = `${distractedPct}%`;

    elements.focusedPercent.textContent = `${focusedPct}%`;
    elements.partialPercent.textContent = `${partialPct}%`;
    elements.distractedPercent.textContent = `${distractedPct}%`;
}

function updateStudentsTable(students) {
    const searchTerm = elements.searchInput.value.toLowerCase();

    const filteredStudents = students.filter(s =>
        s.name.toLowerCase().includes(searchTerm)
    );

    if (filteredStudents.length === 0) {
        elements.studentsTableBody.innerHTML = `
            <tr class="empty-row">
                <td colspan="4">No students ${searchTerm ? 'matching search' : 'connected yet'}</td>
            </tr>
        `;
        return;
    }

    const rows = filteredStudents.map(student => {
        const scorePercent = Math.round(student.score * 100);
        const statusClass = student.status.toLowerCase().replace(' ', '-');
        const statusBadgeClass = student.status === 'Focused' ? 'focused' :
            student.status === 'Partially Attentive' ? 'partial' : 'distracted';
        const fillColor = student.status === 'Focused' ? '#22c55e' :
            student.status === 'Partially Attentive' ? '#f59e0b' : '#ef4444';

        return `
            <tr>
                <td>
                    <span class="student-name">${escapeHtml(student.name)}</span>
                </td>
                <td>
                    <div class="score-bar">
                        <div class="score-progress">
                            <div class="score-fill" style="width: ${scorePercent}%; background: ${fillColor}"></div>
                        </div>
                        <span class="score-value">${scorePercent}%</span>
                    </div>
                </td>
                <td>
                    <span class="status-badge ${statusBadgeClass}">${student.status}</span>
                </td>
                <td>
                    <div class="activity-status">
                        <span class="activity-dot ${student.active ? '' : 'inactive'}"></span>
                        <span>${student.active ? 'Active' : 'Inactive'}</span>
                    </div>
                </td>
            </tr>
        `;
    }).join('');

    elements.studentsTableBody.innerHTML = rows;
}

function updateMeetingSelect(meetings) {
    elements.meetingSelect.innerHTML = meetings.map(m =>
        `<option value="${escapeHtml(m.meeting_id)}">${escapeHtml(m.meeting_id)} (${m.student_count} students)</option>`
    ).join('');
}

// ============================================================
// Polling
// ============================================================

function startPolling() {
    if (state.polling) return;

    state.polling = true;

    // Immediate fetch
    fetchDashboardData();
    fetchHistoryData();

    // Start polling
    state.pollTimer = setInterval(() => {
        fetchDashboardData();
        fetchHistoryData();
    }, CONFIG.pollInterval);
}

function stopPolling() {
    state.polling = false;
    if (state.pollTimer) {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
    }
}

// ============================================================
// Utility Functions
// ============================================================

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ============================================================
// Event Handlers
// ============================================================

function handleMeetingChange(e) {
    state.meetingId = e.target.value;
    fetchDashboardData();
    fetchHistoryData();
}

function handleSearch() {
    // Table will be updated on next poll
    // For immediate feedback, we could re-render with cached data
}

// ============================================================
// Initialize
// ============================================================

function init() {
    // Initialize chart
    initChart();

    // Event listeners
    elements.meetingSelect.addEventListener('change', handleMeetingChange);
    elements.searchInput.addEventListener('input', handleSearch);

    // Start polling
    startPolling();

    // Fetch meetings list
    fetchMeetings();
}

// Start when DOM is ready
document.addEventListener('DOMContentLoaded', init);
