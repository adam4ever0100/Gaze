# Gaze — User Guide

---

## 🎓 For Students

### How to Join a Classroom

1. Get the **app URL** and **room code** from your teacher
2. Open the URL in **Chrome** or **Safari**
3. Enter your name and the 6-character room code
4. Check the consent checkbox and click **Join Session**
5. Allow camera/microphone access when prompted

### During the Session

- Your video appears in the meeting grid alongside other students
- The **side panel** shows your real-time attention score and metrics
- Use the bottom controls to **mute mic**, **toggle camera**, or **leave**

### Tips for High Scores

| ✅ Do This | ❌ Avoid This |
|-----------|---------------|
| Face camera directly | Looking at phone |
| Good lighting on face | Dark room |
| Stay relatively still | Moving around |
| Keep eyes open | Drowsy/half-closed eyes |

### Privacy
- 🔒 **No video is recorded or shared** with the server
- Video conferencing is **peer-to-peer** (WebRTC)
- Only a **number (0-100%)** goes to your teacher
- All AI processing happens on **YOUR computer**

---

## 👩‍🏫 For Teachers

### Quick Setup

**Terminal 1 — Backend + Dashboard:**
```bash
cd /path/to/Gaze
python main.py --backend
```

**Terminal 2 — Student App:**
```bash
python main.py
```

### Create a Classroom

1. Open http://127.0.0.1:5002
2. Enter the teacher password (default: `teacher123`)
3. Click **Create Classroom**
4. Share the **room code** (e.g. `ABC123`) with your students

### Using the Dashboard

| Feature | Description |
|---------|-------------|
| **Class Average** | Real-time average attention score |
| **Student Table** | Per-student scores with status badges |
| **Engagement Chart** | Live timeline of class attention |
| **Distribution Bars** | Focused / Partial / Distracted breakdown |
| **Alerts** | Toast notifications when students become distracted |
| **Export** | Download session data as CSV |
| **Past Sessions** | Review and export previous sessions |

### Attention Indicators

| Indicator | Meaning |
|-----------|---------|
| 🟢 **Focused** (≥70%) | Student engaged |
| 🟡 **Partial** (40-69%) | Some attention issues |
| 🔴 **Distracted** (<40%) | Needs help |

### Best Practices
- If class average drops below 60% → Time for a break
- Monitor the alert toasts for individual distraction notifications
- Use the engagement chart to identify attention drop patterns
- Export CSV reports for post-class analysis

---

## ❓ FAQ

**Q: Can my teacher see my video?**
A: The teacher can see your video through the WebRTC peer-to-peer connection (just like any video call), but no video is recorded or stored on any server.

**Q: What data does the teacher see?**
A: Only your attention score (0-100%), which is calculated from gaze direction, head pose, and eye openness.

**Q: My score is always low?**
A: Check lighting, face camera directly, keep still.

**Q: How do I change the teacher password?**
A: Set `TEACHER_PASSWORD=your_password` in the `.env` file.
