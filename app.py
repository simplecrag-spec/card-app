import json, os, io, asyncio, time, requests
from datetime import datetime, timedelta

import streamlit as st
import edge_tts
import firebase_admin
from firebase_admin import credentials as fb_creds, firestore
from supabase import create_client

# ================= PAGE SETUP (must be first Streamlit call) =================
st.set_page_config(page_title="Voice Flashcards", layout="centered", page_icon="🗣️")

# ================= SECRETS =================
ELEVEN_KEY = st.secrets.get("ELEVEN_API_KEY", os.getenv("ELEVEN_API_KEY", ""))
FIREBASE_CRED = st.secrets.get("FIREBASE_CRED", os.getenv("FIREBASE_CRED", "{}"))
SUPA_URL = st.secrets.get("SUPABASE_URL", os.getenv("SUPABASE_URL", ""))
SUPA_KEY = st.secrets.get("SUPABASE_KEY", os.getenv("SUPABASE_KEY", ""))

# ================= TTS: edge-tts (free, unlimited, Edge neural voices) =================
# Same voices as Microsoft Edge "Read Aloud". No API key, no character limits.
EDGE_VOICES = {
    "Aria — female, clear": "en-US-AriaNeural",
    "Guy — male, calm": "en-US-GuyNeural",
    "Jenny — female, warm": "en-US-JennyNeural",
    "Ryan — male, friendly": "en-US-RyanNeural",
    "Eric — male, deep": "en-US-EricNeural",
    "Christopher — UK male, deep": "en-GB-ChristopherNeural",
    "Sonia — UK female, formal": "en-GB-SoniaNeural",
    "Mia — child-friendly": "en-US-MiaNeural",
}

def stt_component(key):
    html = f"""
    <div style="margin-top:8px;">
      <button onclick="window._startSTTFeb('stt-out-{key}')" style="padding:12px 18px;background:#2d6a4f;color:#fff;border:none;border-radius:8px;font-size:1rem;cursor:pointer;">🎙 Listen</button>
      <button onclick="window._copyText('stt-out-{key}')" style="margin-left:6px;padding:12px 16px;background:#42566b;color:#fff;border:none;border-radius:8px;font-size:0.9rem;cursor:pointer;">📋 Copy</button>
      <div id="stt-out-{key}" style="margin-top:8px;background:#1a1a1a;color:#8be19a;padding:8px 12px;border-radius:6px;font-size:0.9rem;min-height:20px;">(tap 🎙 and speak)</div>
    </div>
    <script>
      function startSTTFeb(outId) {{
        if(!(window.SpeechRecognition||window.webkitSpeechRecognition)){{ alert('Speech recognition not supported in this browser.'); return; }}
        var out = document.getElementById(outId);
        out.textContent = 'Listening...';
        var rec = new (window.SpeechRecognition||window.webkitSpeechRecognition)();
        rec.lang = 'en-US'; rec.continuous = false; rec.interimResults = false;
        rec.onresult = function(e){{
          var text = e.results[0][0].transcript;
          out.textContent = text;
          if(navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).catch(()=>{{}});
        }};
        rec.onerror = function(){{ out.textContent = 'Mic blocked or error. Check browser permission.'; }};
        rec.start();
      }}
      function copyTextFeb(outId){{
        var t = document.getElementById(outId).textContent;
        if(t && t !== '(tap 🎙 and speak)') navigator.clipboard.writeText(t);
      }}
      window._startSTTFeb = startSTTFeb;
      window._copyText = copyTextFeb;
    </script>
    """
    st.components.v1.html(html, height=100)

VOICE_CACHE = {}
def edge_speak(text: str, voice: str, rate: str = "-10%"):
    """Synthesize speech via Edge neural voices. Returns mp3 bytes."""
    if not text.strip():
        return b""
    key = (text, voice, rate)
    if key in VOICE_CACHE:
        return VOICE_CACHE[key]
    try:
        async def _gen():
            com = edge_tts.Communicate(text, voice, rate=rate)
            buf = io.BytesIO()
            async for chunk in com.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            return buf.getvalue()
        out = asyncio.run(_gen())
        VOICE_CACHE[key] = out
        return out
    except Exception as e:
        st.warning(f"TTS error: {e}")
        return b""

def eleven_speak(text: str, voice_id: str = "Rachel"):
    """Optional ElevenLabs if a key is set. Returns mp3 bytes or None."""
    if not ELEVEN_KEY:
        return None
    try:
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json"},
            json={"text": text, "model_id": "eleven_monolingual_v1",
                  "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
            timeout=30,
        )
        return r.content if r.status_code == 200 else None
    except Exception:
        return None

def browser_tts_button(text):
    html = f"""
    <button onclick="
        if('speechSynthesis' in window){{
            window.speechSynthesis.cancel();
            var u = new SpeechSynthesisUtterance({json.dumps(text)});
            u.lang='en-US'; u.rate=0.95;
            window.speechSynthesis.speak(u);
        }} else {{ alert('TTS not supported'); }}
    " style="padding:10px 20px;background:#52b788;color:white;border:none;
    border-radius:8px;font-size:1rem;cursor:pointer;margin-top:10px;">
        🔊 Fallback Speak
    </button>
    """
    st.markdown(html, unsafe_allow_html=True)

def play_voice(text: str, voice: str, rate: str = "-10%"):
    """Play best available voice: Edge (default) or ElevenLabs (if key set)."""
    if ELEVEN_KEY:
        eleven = eleven_speak(text)
        if eleven:
            st.audio(eleven, format="audio/mp3")
            return
    edge = edge_speak(text, voice, rate)
    if edge:
        st.audio(edge, format="audio/mp3")
    else:
        browser_tts_button(text)

# ================= STORAGE (Firebase OR Supabase OR local) =================
def backend():
    if FIREBASE_CRED.startswith("{"):
        return "firebase"
    if SUPA_URL and SUPA_KEY:
        return "supabase"
    return "local"

_supa = None
def get_supa():
    global _supa
    if _supa is None:
        _supa = create_client(SUPA_URL, SUPA_KEY)
    return _supa

_db = None
def get_firestore():
    global _db
    if _db is None:
        if not firebase_admin._apps:
            cred = fb_creds.Certificate(json.loads(FIREBASE_CRED))
            firebase_admin.initialize_app(cred)
        _db = firestore.client()
    return _db

SUPA_TABLE = "flashcards"

def _fields(card):
    """Keep only DB columns (drop internal _id)."""
    cols = ["front", "back", "interval", "repetitions", "ease", "next_review", "created_at"]
    return {k: v for k, v in card.items() if k in cols}

def load_cards():
    b = backend()
    try:
        if b == "firebase":
            cards = []
            for d in get_firestore().collection("flashcards").stream():
                c = d.to_dict(); c["_id"] = d.id; cards.append(c)
            cards.sort(key=lambda x: x.get("next_review", ""))
            return cards
        if b == "supabase":
            res = get_supa().table(SUPA_TABLE).select("*").order("created_at").execute()
            cards = []
            for row in (res.data or []):
                row["_id"] = str(row["id"]); cards.append(row)
            cards.sort(key=lambda x: x.get("next_review", ""))
            return cards
    except Exception as e:
        st.sidebar.warning(f"Cloud load failed ({b}): {e} — using local file.")
    path = "flashcards.json"
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return []

def save_card(card):
    b = backend()
    card.setdefault("created_at", datetime.utcnow().isoformat())
    card["updated_at"] = datetime.utcnow().isoformat()
    try:
        if b == "firebase":
            if "_id" in card and card["_id"]:
                get_firestore().collection("flashcards").document(card["_id"]).set(card, merge=True)
            else:
                ref = get_firestore().collection("flashcards").add(card)
                card["_id"] = ref[1].id
            return
        if b == "supabase":
            fields = _fields(card)
            if "_id" in card and card["_id"]:
                get_supa().table(SUPA_TABLE).update(fields).eq("id", int(card["_id"])).execute()
            else:
                res = get_supa().table(SUPA_TABLE).insert(fields).execute()
                if res.data:
                    card["_id"] = str(res.data[0]["id"])
            return
    except Exception as e:
        st.sidebar.warning(f"Cloud save failed ({b}): {e} — saving locally.")
    cards = []
    if os.path.exists("flashcards.json"):
        with open("flashcards.json", encoding="utf-8") as f:
            cards = json.load(f)
    cards.append(card)
    with open("flashcards.json", "w", encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False, indent=2)

def delete_all_cards():
    b = backend()
    try:
        if b == "firebase":
            for d in get_firestore().collection("flashcards").stream():
                d.reference.delete()
            return
        if b == "supabase":
            get_supa().table(SUPA_TABLE).delete().neq("id", 0).execute()
            return
    except Exception as e:
        st.sidebar.warning(f"Cloud clear failed: {e}")
    if os.path.exists("flashcards.json"):
        os.remove("flashcards.json")

# ================= SESSION STATE =================
if "cards" not in st.session_state:
    st.session_state.cards = load_cards()
if "idx" not in st.session_state:
    st.session_state.idx = 0 if st.session_state.cards else None
if "show_answer" not in st.session_state:
    st.session_state.show_answer = False
if "voice" not in st.session_state:
    st.session_state.voice = "en-US-GuyNeural"
if "rate" not in st.session_state:
    st.session_state.rate = "-10%"
if "voice_create" not in st.session_state:
    st.session_state.voice_create = False
if "created_q" not in st.session_state:
    st.session_state.created_q = ""
if "created_q_done" not in st.session_state:
    st.session_state.created_q_done = False

# ================= PAGE =================
st.title("🗣️ Voice Flashcards — AI Voice")

# ---------- Sidebar ----------
with st.sidebar:
    st.markdown("### 🔊 Voice")
    voice_label = st.selectbox("AI Voice (Edge, unlimited & free)",
        list(EDGE_VOICES.keys()), index=1)
    st.session_state.voice = EDGE_VOICES[voice_label]
    rate_pct = st.slider("Speed  (slower = better for studying)", -30, 30, -10)
    st.session_state.rate = f"{rate_pct}%"

    st.markdown("---")
    st.markdown("### 🌩 Storage")
    b = backend()
    if b == "firebase":
        st.success("Firebase Firestore connected ☁️")
    elif b == "supabase":
        st.success("Supabase connected ☁️")
    else:
        st.warning("⚠️ No cloud connected — cards saved to a temp file. "
                   "Set Supabase (easy) or Firebase secrets to save forever.")

    st.markdown("---")
    if st.button("🗣️ Voice Create (wake: 'hey fresco')"):
        st.session_state.voice_create = True
        st.session_state.created_q_done = False
        st.rerun()

    if st.button("⚠️ Clear all cards"):
        for c in st.session_state.cards:
            save_card_placeholder = c  # noop for linters
        n = len(st.session_state.cards)
        delete_all_cards()
        st.session_state.cards = []
        st.session_state.idx = None
        st.info(f"Deleted {n} cards.")
        st.rerun()


# ---------- Add card (manual) ----------
with st.expander("➕ Add New Flashcard", expanded=False):
    front = st.text_input("Front (question)", key="new_front")
    back = st.text_input("Back (answer)", key="new_back")
    if st.button("Add Card"):
        if front.strip() and back.strip():
            save_card({
                "front": front.strip(), "back": back.strip(),
                "interval": 1, "repetitions": 0, "ease": 2.5,
                "next_review": datetime.utcnow().isoformat(),
                "created_at": datetime.utcnow().isoformat(),
            })
            st.session_state.cards = load_cards()
            st.session_state.idx = len(st.session_state.cards) - 1
            st.session_state.show_answer = False
            st.success("Card added!")
            st.rerun()
        else:
            st.warning("Both fields required.")

    if st.session_state.voice_create:
        st.markdown("### 🗣️ Voice Create — wake word **'hey fresco'**")
        if not st.session_state.created_q_done:
            st.markdown("**Step 1 — tap 🎙 and say your QUESTION:**")
            stt_component(key="vcreate_q")
            q_input = st.text_input("Question:", value=st.session_state.created_q, key="vq")
            if st.button("Confirm Question →"):
                if q_input.strip():
                    st.session_state.created_q = q_input.strip()
                    st.session_state.created_q_done = True
                    st.rerun()
                else:
                    st.warning("Say or type a question first.")
        else:
            st.info(f"**Q:** {st.session_state.created_q}")
            st.markdown("**Step 2 — tap 🎙 and say your ANSWER:**")
            stt_component(key="vcreate_a")
            a_input = st.text_input("Answer:", value="", key="va")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("⬅️ Back"):
                    st.session_state.created_q_done = False
                    st.rerun()
            with c2:
                if st.button("✅ Finish Card"):
                    a = a_input.strip()
                    if a:
                        save_card({
                            "front": st.session_state.created_q, "back": a,
                            "interval": 1, "repetitions": 0, "ease": 2.5,
                            "next_review": datetime.utcnow().isoformat(),
                            "created_at": datetime.utcnow().isoformat(),
                            "voice_created": True,
                        })
                        st.session_state.cards = load_cards()
                        st.session_state.created_q = ""
                        st.session_state.created_q_done = False
                        st.session_state.voice_create = False
                        st.session_state.idx = len(st.session_state.cards) - 1
                        st.session_state.show_answer = False
                        st.success("🎉 Voice card created!")
                        st.rerun()
                    else:
                        st.warning("Need the answer too.")

# ---------- Main card view ----------
if not st.session_state.cards:
    st.info("No flashcards yet. Add one above, or use 🗣️ Voice Create.")
    st.stop()

card = st.session_state.cards[st.session_state.idx]
text_now = card["front"] if not st.session_state.show_answer else card["back"]

# Navigation
col_prev, col_next = st.columns([1, 1])
with col_prev:
    if st.button("⟨ Prev", disabled=st.session_state.idx <= 0):
        st.session_state.idx -= 1
        st.session_state.show_answer = False
        st.rerun()
with col_next:
    if st.button("Next ⟩", disabled=st.session_state.idx >= len(st.session_state.cards) - 1):
        st.session_state.idx += 1
        st.session_state.show_answer = False
        st.rerun()

st.progress((st.session_state.idx + 1) / len(st.session_state.cards))

# Black card
st.markdown(f"""
    <div style="
        border:3px solid #52b788; border-radius:14px;
        padding:2rem; min-height:220px; font-size:1.35rem;
        text-align:center; background:#1a1a1a; color:#ffffff;
        box-shadow:0 10px 30px rgba(0,0,0,0.5);
        display:flex; align-items:center; justify-content:center;">
        <div style="line-height:1.6;">{text_now}</div>
    </div>
    """, unsafe_allow_html=True)

# Auto AI voice for the card text
play_voice(text_now, st.session_state.voice, st.session_state.rate)

# ---------- Answer + grading ----------
b1, b2, b3 = st.columns([1, 1, 1])
with b1:
    if st.button("💡 Show Answer"):
        st.session_state.show_answer = not st.session_state.show_answer
        st.rerun()

with b2:
    if st.session_state.show_answer:
        sttt_key = f"answer_{st.session_state.idx}"
        st.markdown("**🎙 Say your answer — check it below:**")
        stt_component(key=sttt_key)
        spoken = st.text_input("Heard / typed answer:", value="", key=f"spoken_{st.session_state.idx}",
                               placeholder="Tap 🎙 above, then paste")
        if st.button("✅ Check Answer", key=f"check_{st.session_state.idx}"):
            user = spoken.strip().lower()
            correct = card.get("back", "").strip().lower()
            if user:
                ok = user in correct or correct in user
                st.session_state.last_check = (ok, card.get("back", ""), spoken.strip())
                st.rerun()
            else:
                st.warning("Speak or type your answer first.")
        if st.session_state.get("last_check"):
            ok, corr, heard = st.session_state.last_check
            if ok:
                play_voice("Correct! Excellent work.", st.session_state.voice, st.session_state.rate)
                st.success(f"✅ You said: **{heard}** → matches **{corr}**")
            else:
                play_voice("Not quite. Let's review the correct answer.", st.session_state.voice, st.session_state.rate)
                st.error(f"❌ You said: **{heard}** — correct answer: **{corr}**")

with b3:
    if st.session_state.show_answer:
        def update_card(q):
            c = st.session_state.cards[st.session_state.idx]
            if q >= 3:
                if c["repetitions"] == 0: c["interval"] = 1
                elif c["repetitions"] == 1: c["interval"] = 6
                else: c["interval"] = round(c["interval"] * c["ease"])
                c["repetitions"] += 1
                c["ease"] = max(1.3, c["ease"] + (0.1 - (5 - q) * 0.08))
            else:
                c["repetitions"] = 0
                c["interval"] = 1
                c["ease"] = max(1.3, c["ease"] - 0.2)
            c["next_review"] = (datetime.utcnow() + timedelta(days=c["interval"])).isoformat()
            save_card(c)
            st.session_state.pop("last_check", None)
            if st.session_state.idx < len(st.session_state.cards) - 1:
                st.session_state.idx += 1
            st.session_state.show_answer = False
            st.rerun()

        g1, g2, g3, g4 = st.columns(4)
        with g1:
            if st.button("Again", use_container_width=True): update_card(0)
        with g2:
            if st.button("Hard", use_container_width=True): update_card(1)
        with g3:
            if st.button("Good", use_container_width=True): update_card(2)
        with g4:
            if st.button("Easy", use_container_width=True): update_card(3)

st.caption(f"Next review: {card.get('interval', 1)} day(s) • Ease: {card.get('ease', 2.5):.2f}")