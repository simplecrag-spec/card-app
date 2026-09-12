# 🗣️ Voice Flashcards — AI Voice Edition

Voice-enabled flashcard app with AI neural TTS, voice answer checking, voice card creation, and cloud storage.

## Features
- 🔊 **AI Voice (Edge TTS, free & unlimited)** — Microsoft neural voices, no API key, no character limits
- 🎙 **Voice Answer Checking (Web Speech API)** — speak your answer, app checks it
- 🧠 **SM-2 Spaced Repetition** — Again / Hard / Good / Easy grading
- 🔥 **Wake word "hey fresco"** — voice card creation flow
- ☁️ **Supabase or Firebase** — persistent cloud storage, works with PC off
- 📱 **Mobile-ready** — works on iOS Safari / Android Chrome

## Setup (one time)

### 1. Supabase cloud storage (recommended — easiest)
1. Go to **[app.supabase.com](https://app.supabase.com)** → Sign up with Google or GitHub
2. Click **New Project** → name it anything → pick a free plan → wait ~1 min
3. Go to **SQL Editor** (left sidebar) → click **New Query**, paste:

```sql
create table if not exists flashcards (
  id bigint generated always as identity primary key,
  front text not null,
  back text not null,
  interval int default 1,
  repetitions int default 0,
  ease float default 2.5,
  next_review text,
  created_at timestamptz default now()
);
alter table flashcards disable row level security;
```

4. Click **Run**
5. Go to **Settings** (gear icon) → **API** → copy:
   - **Project URL** (looks like `https://abc123.supabase.co`)
   - **anon public key** (starts with `eyJ...`)

That's it! Paste both into Streamlit secrets (step below).

### 2. Voice — already free & unlimited (no signup needed!)
Edge TTS uses the same voices as Microsoft Edge's "Read Aloud." No API key, no limits.
- **8 voices** available in the sidebar: Aria, Guy, Jenny, Ryan, Eric, Christopher, Sonia, Mia
- Speed control in sidebar (-30% to +30%)

### 3. Streamlit secrets
In your Streamlit Cloud app → **Manage app** → **Advanced settings** → **Secrets**, paste:

```toml
SUPABASE_URL = "https://abc123.supabase.co"
SUPABASE_KEY = "eyJhbGci..."
```

### 4. Deploy
1. Push to GitHub
2. Streamlit Cloud: link repo → `main` → `app.py`
3. Add secrets (above)
4. Open on phone → **Add to Home Screen**

## Usage
- **Review** — flip card, grade Again/Hard/Good/Easy
- **Voice answer** — after showing answer, tap 🎙, speak your answer, paste it, tap "Check Answer"
- **Voice create** — tap "Voice Create" → say question → say answer → card saved
- **AI voice** — automatically reads each card; speed control in sidebar

## Voice wake word note
"hey fresco" is the activation phrase. Tap the 🎤 button to activate mic (browsers require a tap for security), then say your question/answer as prompted.