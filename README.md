# Zoom Attention Monitoring System

A real-time attention monitoring system for online classes that tracks student engagement using AI-powered face and gaze detection.

## 🎯 How It Works

Students open the attention monitor in their browser while attending a Zoom meeting. The app uses MediaPipe Face Mesh to track:
- **Face presence** - Is the student looking at the screen?
- **Eye gaze direction** - Where are they looking?
- **Head pose** - Are they facing the camera?
- **Blink rate** - Natural attention indicator

These metrics combine into an **attention score** that teachers can monitor in real-time.

## 🔒 Privacy First

- ✅ All video processing happens **locally in the browser**
- ✅ No video is stored or transmitted
- ✅ Only numeric attention scores are shared
- ✅ Students can stop monitoring at any time

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- pip
- ngrok (for HTTPS, required for browser camera access)

### 1. Install Dependencies
```bash
cd /path/to/Gaze
pip install -r requirements.txt
```

### 2. Start the Servers

**Terminal 1 - Student App:**
```bash
python3 main.py --port 5001
```

**Terminal 2 - Teacher Dashboard:**
```bash
python3 backend/server.py
```

### 3. Start ngrok (for HTTPS)
```bash
ngrok http 5001
```
Copy the HTTPS URL (e.g., `https://abc123.ngrok-free.dev`)

### 4. Access the Apps

**For Students:**
- Open the ngrok HTTPS URL in Chrome/Safari
- Enter their name, consent, and click "Start Monitoring"
- Join their Zoom meeting normally

**For Teachers:**
- Open http://127.0.0.1:5002
- View real-time attention scores for all students

## 📊 Attention Score Algorithm

| Component | Weight | Description |
|-----------|--------|-------------|
| Gaze Score | 35% | How centered the eye gaze is |
| Head Pose | 30% | Head facing forward vs. turned away |
| Eye Openness | 25% | Eyes open and alert vs. droopy |
| Face Presence | 10% | Face detected in frame |

**Classification:**
- 🟢 **Focused** (≥70%): Student is engaged
- 🟡 **Partially Attentive** (40-69%): Some attention
- 🔴 **Distracted** (<40%): Not paying attention

## 📁 Project Structure

```
Gaze/
├── main.py                 # Student app entry point
├── native_client/          # Native macOS Camera App
│   ├── build/              # Compiled .app bundle
│   └── src/                # Obj-C++ source code
├── zoom_app/
│   ├── index.html          # Student UI
│   └── ...
```

## 🛠️ Native macOS Application

A native macOS application that captures video directly from your webcam for attention monitoring. **Works with ANY video conferencing app** (Zoom, Google Meet, Teams, etc.)

### Prerequisites
- macOS with Xcode Command Line Tools
- CMake 3.15+

### Building
```bash
cd native_client
rm -rf build && mkdir build && cd build
cmake ..
make
```

### Running
```bash
# Basic usage
open build/NativeAttentionMonitor.app

# Or from command line with options
./build/NativeAttentionMonitor.app/Contents/MacOS/NativeAttentionMonitor --name="John Doe" --meeting="12345"
```

### Command Line Options
- `--name=<name>` - Set student name (default: Native Student)
- `--meeting=<id>` - Set meeting ID (default: default)
- `--backend=<url>` - Backend URL (default: http://127.0.0.1:5002)
- `--test` - Run in test mode (5 seconds then exit)

## 🛠️ Development

### Environment Variables (.env)
```
ZOOM_CLIENT_ID=your_client_id
ZOOM_CLIENT_SECRET=your_secret
ZOOM_REDIRECT_URI=https://your-ngrok-url.ngrok-free.dev/oauth/callback
```

### Running in Debug Mode
```bash
python3 main.py --port 5001 --debug
```

## 📄 License

MIT License
