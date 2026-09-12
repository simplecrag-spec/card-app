# 🗣️ Voice Flashcards — AI Voice Edition

Voice-enabled flashcard app with spaced repetition, AI text-to-speech, voice answers, and voice card creation.

## Features
- 🔊 **AI Voice (ElevenLabs)** — natural voices, free 10k chars/mo
- 🎙 **Voice Answer Checking (Web Speech API)** — speak your answer, app hears it
- 🧠 **SM-2 Spaced Repetition** — Again / Hard / Good / Easy grading
- 🔥 **"hey fresco" wake word** — voice card creation: say question, then answer
- ☁️ **Firebase Firestore** — persistent cloud storage, works with PC off
- 📱 **Mobile-ready** — works on iOS Safari / Android Chrome

## Setup (one time)

### 1. ElevenLabs voice (free)
1. Sign up at https://elevenlabs.io (free tier: 10k characters/month)
2. Copy your API key from Profile → API Keys

### 2. Firebase storage (free)
1. Go to https://console.firebase.google.com → Create project
2. Build → Firestore Database → Create database (start in "Production mode")
3. Project Settings → **Service accounts** → Generate new private key
   - Download the JSON file — this is your service account credential

### 3. Streamlit secrets
In your Streamlit Cloud app dashboard:
1. Open **Advanced settings** → **Secrets**
2. Paste:

```toml
ELEVEN_API_KEY = "your-elevenlabs-key"

FIREBASE_CRED = '{"type":"service_account","project_id":"your-project","private_key":"-----BEGIN ... -----","client_email":"firebase-adminsdk@your-project.iam.gserviceaccount.com"}'
```

> Tip: get the service account JSON into one line with: `cat your-firebase-key.json | python -c "import sys,json;print(json.dumps(json.load(sys.stdin)))"`

## Deploy
1. Push to GitHub
2. Streamlit Cloud: link repo → `main` → `app.py`
3. Set secrets (above)
4. **First run**: In Firebase console → Firestore → Rules → set read/write to `true` (for personal use):

```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /{document=**} {
      allow read, write: if true;
    }
  }
}
```

5. Done! Open on your phone → Add to Home Screen

## Usage
- **Review** — flip card, grade Again/Hard/Good/Easy
- **Voice answer** — after showing answer, tap 🎙 and speak your answer
- **Voice create** — tap *Voice Card Creation* → "hey fresco" → say question → say answer → card saved