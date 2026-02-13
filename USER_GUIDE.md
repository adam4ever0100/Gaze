# Attention Monitor - User Guide

---

## 🎓 For Students

### Option 1: Web App (Easiest)
1. Get the app URL from your teacher
2. Open it in **Chrome** or **Safari**
3. Enter your name, check consent, click **Start Monitoring**
4. Allow camera access → Join your video call normally

### Option 2: Native macOS App (Best Accuracy)

**How to Get It:**
1. Your teacher will share `AttentionMonitor-Student.zip`
2. Unzip the file
3. Double-click `start_monitoring.command`
4. Enter your name when prompted
5. **Allow camera access** when macOS asks

**First-Time Setup:**
- If macOS blocks the app: Go to **System Settings → Privacy & Security** and click "Allow Anyway"
- Grant camera permission when prompted

### Tips for High Scores

| ✅ Do This | ❌ Avoid This |
|-----------|---------------|
| Face camera directly | Looking at phone |
| Good lighting on face | Dark room |
| Stay relatively still | Moving around |
| Keep eyes open | Drowsy/half-closed eyes |

### Privacy
- 🔒 **No video is recorded or shared**
- Only a **number (0-100%)** goes to your teacher
- All processing happens on YOUR computer

---

## 👩‍🏫 For Teachers

### Setup Steps

```bash
# Terminal 1: Backend server (receives scores)
cd /path/to/Gaze
python3 backend/server.py

# Terminal 2: Web app for students
python3 main.py --port 5001

# Terminal 3: HTTPS tunnel
ngrok http 5001
```

### Share with Students

**Web Option:** Share your ngrok URL (e.g., `https://abc123.ngrok-free.dev`)

**Native App:** Run this to create a zip file for students:
```bash
cd native_client
./package_for_students.sh
# → Creates AttentionMonitor-Student.zip
```

### Reading the Dashboard

Open http://127.0.0.1:5002 to see:

| Indicator | Meaning |
|-----------|---------|
| 🟢 **Focused** (≥70%) | Student engaged |
| 🟡 **Partial** (40-69%) | Some attention issues |
| 🔴 **Distracted** (<40%) | Needs help |

### Best Practices
- If class average drops below 60% → Time for a break
- Individual low scores → Check for tech issues
- Review trends in the live chart

---

## ❓ FAQ

**Q: Can my teacher see my video?**
A: No! Only a number (0-100%) is shared.

**Q: macOS says the app is from an unidentified developer?**
A: Go to System Settings → Privacy & Security → Click "Open Anyway"

**Q: My score is always low?**
A: Check lighting, face camera directly, keep still.

**Q: Does it work with Zoom/Meet/Teams?**
A: Yes! Works with any video call app.
