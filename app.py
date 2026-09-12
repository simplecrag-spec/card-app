import json, os, time, streamlit as st, requests
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, firestore

# --- Config / Secrets ---
# In Streamlit Cloud: set these in app secrets
ELEVEN_KEY = st.secrets.get("ELEVEN_API_KEY", os.getenv("ELEVEN_API_KEY", ""))
FIREBASE_CRED = st.secrets.get("FIREBASE_CRED", os.getenv("FIREBASE_CRED", "{}"))

# --- Firebase init ---
DB = None
def init_firebase():
    global DB
    if DB is not None: return DB
    try:
        cred_dict = json.loads(FIREBASE_CRED) if FIREBASE_CRED.startswith("{") else None
        if not firebase_admin._apps:
            if cred_dict:
                firebase_admin.initialize_app(credentials.Certificate(cred_dict))
            else:
                # Try service account from env
                firebase_admin.initialize_app()
        DB = firestore.client()
        return DB
    except Exception as e:
        st.sidebar.error(f"Firebase init: {e}")
        return None

# --- Voice TTS (ElevenLabs) ---
def eleven_tts(text, voice_id="Rachel"):
    if not ELEVEN_KEY:
        return None  # fall back to silent / browser
    try:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json"}
        payload = {"text": text, "model_id": "eleven_monolingual_v1", "voice_settings":{"stability":0.5,"similarity_boost":0.75}}
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        if r.status_code == 200:
            return r.content  # audio bytes
        else:
            return None
    except Exception:
        return None

# --- Card storage (Firebase) ---
COLLECTION = "flashcards"

def load_cards():
    db = init_firebase()
    if db is None:
        # Fallback to JSON for dev
        path = "flashcards.json"
        if os.path.exists(path):
            with open(path) as f: return json.load(f)
        return []
    docs = db.collection(COLLECTION).stream()
    cards = []
    for d in docs:
        c = d.to_dict()
        c["_id"] = d.id
        cards.append(c)
    # Order by creation time
    cards.sort(key=lambda x: x.get("next_review", ""))
    return cards

def save_card(card):
    db = init_firebase()
    if db is not None:
        card["updated_at"] = datetime.utcnow().isoformat()
        if "_id" in card:
            db.collection(COLLECTION).document(card["_id"]).set(card, merge=True)
        else:
            ref = db.collection(COLLECTION).add(card)
            card["_id"] = ref[1].id
    else:
        # JSON fallback
        cards = load_cards()
        cards.append(card)
        with open("flashcards.json","w") as f: json.dump(cards, f)

def delete_cards():
    cards = load_cards()
    for c in cards:
        if "_id" in c:
            db = init_firebase()
            if db: db.collection(COLLECTION).document(c["_id"]).delete()
    # Also clear JSON fallback for simplicity
    if os.path.exists("flashcards.json"):
        os.remove("flashcards.json")

def save_all_cards(cards):
    for c in cards: save_card(c)

# --- Session state init ---
if "cards" not in st.session_state:
    st.session_state.cards = load_cards()
if "idx" not in st.session_state:
    st.session_state.idx = 0 if st.session_state.cards else None
if "show_answer" not in st.session_state:
    st.session_state.show_answer = False
if "voice_mode" not in st.session_state:
    st.session_state.voice_mode = False  # for wake-word flow
if "stt_listening" not in st.session_state:
    st.session_state.stt_listening = False
if "stt_result" not in st.session_state:
    st.session_state.stt_result = ""
if "created_q" not in st.session_state:
    st.session_state.created_q = ""

# --- STT Component (Web Speech API) ---
def stt_component(key):
    # Recognized speech is auto-copied to clipboard + shown on screen.
    # User then taps the matching "Use listened text" button in Streamlit.
    html = f"""
    <div style="margin-top:8px;">
      <button onclick="window._startSTTFeb('stt-out-{key}')" style="padding:12px 18px;background:#2d6a4f;color:#fff;border:none;border-radius:8px;font-size:1rem;cursor:pointer;">🎙 Listen</button>
      <button onclick="window._copyText('stt-out-{key}')" style="margin-left:6px;padding:12px 16px;background:#42566b;color:#fff;border:none;border-radius:8px;font-size:0.9rem;cursor:pointer;">📋 Copy</button>
      <div id="stt-out-{key}" style="margin-top:8px;background:#1a1a1a;color:#8be19a;padding:8px 12px;border-radius:6px;font-size:0.9rem;min-height:20px;">(tap 🎙 and speak)</div>
    </div>
    <script>
      function startSTTFeb(outId) {{
        if(!(window.SpeechRecognition||window.webkitSpeechRecognition)){{ alert('Speech recognition not supported in this browser.'); return; }}
        var stored = sessionStorage.getItem('sttFebText') || '';
        var out = document.getElementById(outId);
        out.textContent = 'Listening...';
        var rec = new (window.SpeechRecognition||window.webkitSpeechRecognition)();
        rec.lang = 'en-US'; rec.continuous = false; rec.interimResults = false;
        rec.onresult = function(e){{
          var text = e.results[0][0].transcript;
          out.textContent = text;
          sessionStorage.setItem('sttFebText', text);
          navigator.clipboard && navigator.clipboard.writeText(text).catch(()=>{{}});
        }};
        rec.onerror = function(){{
          out.textContent = 'Mic blocked or error. Check browser permission.';
          sessionStorage.setItem('sttFebText','');
        }};
        rec.start();
      }}
      function copyTextFeb(outId){{
        var t = document.getElementById(outId).textContent;
        if(t && t !== '(tap 🎙 and speak)'){{ navigator.clipboard.writeText(t); sessionStorage.setItem('sttFebText', t); }}
      }}
      window._startSTTFeb = startSTTFeb;
      window._copyText = copyTextFeb;
    </script>
    """
    st.components.v1.html(html, height=100)

def last_stt_text():
    # Streamlit can't read iframe storage; user confirms via button and pastes if needed.
    return ""

# --- Voice Card Creation Flow ---
def voice_create_flow():
    st.markdown("### 🗣️ Voice Create — wake word **'hey fresco'** detected")
    # Hey fresco -> activate. Since browser STT is one-shot, the user triggers it once;
    # from there it's a guided Listen Q -> Listen A flow with clipboard paste helper.
    if st.session_state.get("voice_mode"):
        # Step 1: Question
        if not st.session_state.get("created_q_done"):
            st.markdown("**Step 1 — tap 🎙 and say your QUESTION:**")
            stt_component(key="vcreate_q")
            q_input = st.text_input("Question (edit or paste from 🎙):", value=st.session_state.get("created_q",""), key="vq")
            if st.button("Confirm Question →"):
                if q_input.strip():
                    st.session_state.created_q = q_input.strip()
                    st.session_state.created_q_done = True
                    st.rerun()
                else:
                    st.warning("Say or type a question first.")
        # Step 2: Answer
        elif st.session_state.get("created_q_done"):
            st.info(f"Q: {st.session_state.created_q}")
            st.markdown("**Step 2 — tap 🎙 and say your ANSWER:**")
            stt_component(key="vcreate_a")
            a_input = st.text_input("Answer (edit or paste from 🎙):", value=st.session_state.get("created_a",""), key="va")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("⬅️ Back"):
                    st.session_state.created_q_done = False
                    st.rerun()
            with c2:
                if st.button("✅ Create Card"):
                    q = st.session_state.created_q
                    a = a_input.strip()
                    if q and a:
                        new_card = {
                            "front": q,
                            "back": a,
                            "interval": 1,
                            "repetitions": 0,
                            "ease": 2.5,
                            "next_review": datetime.utcnow().isoformat(),
                            "voice_created": True,
                            "created_at": datetime.utcnow().isoformat()
                        }
                        st.session_state.cards.append(new_card); save_card(new_card)
                        st.session_state.created_q = ""; st.session_state.created_a = ""
                        st.session_state.created_q_done = False
                        st.session_state.stt_listening = False
                        st.session_state.voice_mode = False
                        st.session_state.idx = len(st.session_state.cards) - 1
                        st.session_state.show_answer = False
                        st.success("🎉 Voice card created!")
                        st.rerun()
                    else:
                        st.warning("Need an answer too.")
            return st.session_state.created_q, a_input

    # Not activated yet: show "say hey fresco" hint and an activation button
    st.caption("Alternately: say **'hey fresco'** then start. (Currently the app listens on tap since continuous wake-word needs background mic permission.)")
    if st.button("🎤 Activate: hey fresco → voice create"):
        st.session_state.voice_mode = True
        st.session_state.created_q_done = False
        st.session_state.created_q = ""
        st.session_state.stt_listening = False
        st.rerun()

# --- Page Config ---
st.set_page_config(page_title="Voice Flashcards — AI Voice", layout="centered")
st.title("🗣️ Voice Flashcards — AI Voice")

# --- Sidebar: Settings / Voice Mode ---
with st.sidebar:
    st.markdown("### Settings")
    # Wake-word trigger: "hey fresco"
    if st.button("🎤 Voice Card Creation (wake: 'hey fresco')"):
        st.session_state.voice_mode = True
        st.rerun()

    st.markdown("---")
    st.markdown("**AI Voice:** ElevenLabs (free 10k chars/mo)")
    st.markdown("**STT:** Browser Speech API (free, mobile)")
    st.markdown("**Storage:** Firebase Firestore (free 1GB)")
    st.markdown("**Wake word:** `hey fresco`")

# --- Add flashcard (traditional + voice) ---
with st.expander("➕ Add New Flashcard", expanded=False):
    front = st.text_input("Front (question)", key="new_front")
    back = st.text_input("Back (answer)", key="new_back")
    if st.button("Add Card"):
        if front.strip() and back.strip():
            card = {
                "front": front.strip(),
                "back": back.strip(),
                "interval": 1,
                "repetitions": 0,
                "ease": 2.5,
                "next_review": datetime.utcnow().isoformat()
            }
            st.session_state.cards.append(card)
            save_card(card)
            st.session_state.idx = len(st.session_state.cards) - 1
            st.session_state.show_answer = False
            st.success("Card added!")
            st.rerun()
        else:
            st.warning("Both fields required.")

    # Voice creation inside expander when activated
    if st.session_state.get("voice_mode"):
        st.divider()
        voice_create_flow()

# --- Main card viewer ---
if not st.session_state.cards:
    st.info("No flashcards. Add one above or use Voice Mode to create by speech.")
    st.stop()

# Navigation
col_prev, col_next = st.columns([1, 1])
with col_prev:
    if st.button("⟨ Prev", disabled=st.session_state.idx == 0):
        st.session_state.idx -= 1
        st.session_state.show_answer = False
        st.rerun()
with col_next:
    if st.button("Next ⟩", disabled=st.session_state.idx >= len(st.session_state.cards) - 1):
        st.session_state.idx += 1
        st.session_state.show_answer = False
        st.rerun()

card = st.session_state.cards[st.session_state.idx]
if card is None:
    st.error("Card missing.")
    st.stop()

st.progress((st.session_state.idx + 1) / len(st.session_state.cards))

# --- Black card display ---
st.markdown(
    f"""
    <div style="
        border:3px solid #52b788;
        border-radius:14px;
        padding:2rem;
        min-height:220px;
        font-size:1.35rem;
        text-align:center;
        background:#1a1a1a;
        color:#ffffff;
        box-shadow: 0 10px 30px rgba(0,0,0,0.5);
        display:flex;
        align-items:center;
        justify-content:center;">
        <div style="line-height:1.6;">{card.get('front', '') if not st.session_state.show_answer else card.get('back', '')}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

# --- AI Voice (ElevenLabs) ---
voice_text = card.get('front', '') if not st.session_state.show_answer else card.get('back', '')
audio_bytes = eleven_tts(voice_text)
if audio_bytes:
    st.audio(audio_bytes, format="audio/mp3")
else:
    # Fallback to browser TTS button
    html_btn = f"""
    <button onclick="
        if('speechSynthesis' in window){{
            window.speechSynthesis.cancel();
            var u = new SpeechSynthesisUtterance({json.dumps(voice_text)});
            u.lang='en-US';
            u.rate=0.95;
            window.speechSynthesis.speak(u);
        }}else{{alert('TTS not supported')}}
    " style="padding:10px 20px;background:#52b788;color:white;border:none;border-radius:8px;font-size:1rem;cursor:pointer;margin-top:10px;">
        🔊 Speak (Browser TTS)
    </button>
    """
    st.markdown(html_btn, unsafe_allow_html=True)

# --- Answer reveal ---
b1, b2, b3 = st.columns([1, 1, 1])
with b1:
    if st.button("💡 Show Answer"):
        st.session_state.show_answer = not st.session_state.show_answer
        st.rerun()
with b2:
    if st.session_state.show_answer:
        if not st.session_state.get('spoken_answer'):
            stt_component(key=f"answer_{st.session_state.idx}")
            spoken_text = st.text_input("Spoken answer (paste from 🎙 or type):", value="", key=f"spoken_{st.session_state.idx}", placeholder="Tap 🎙 above, then paste")
            st.session_state['_spoken_pending'] = spoken_text
            if st.button("✅ Check answer", key=f"check_{st.session_state.idx}"):
                spoken_text = st.session_state.get('_spoken_pending','').strip()
                correct = card.get('back','').strip()
                if spoken_text:
                    is_correct = spoken_text.lower().strip() in correct.lower() or correct.lower() in spoken_text.lower()
                    st.session_state['last_check'] = (is_correct, correct, spoken_text)
                    st.rerun()
                else:
                    st.warning("Speak or type an answer first.")
            # Last check result (if any)
            last = st.session_state.get('last_check')
            if last:
                ok, corr, heard = last
                if ok:
                    st.success(f"✅ You said: **{heard}** — close enough to **{corr}**")
                    audio_bytes = eleven_tts("Correct! Excellent.", voice_id="Rachel")
                    if audio_bytes: st.audio(audio_bytes, format="audio/mp3")
                else:
                    st.error(f"❌ You said: **{heard}** → The answer is: **{corr}**")

# --- Grading ---
with b3:
    if st.session_state.show_answer:
        g1, g2, g3, g4 = st.columns(4)

        def update_card(quality: int):
            c = st.session_state.cards[st.session_state.idx]
            if quality >= 3:
                if c["repetitions"] == 0:
                    c["interval"] = 1
                elif c["repetitions"] == 1:
                    c["interval"] = 6
                else:
                    c["interval"] = round(c["interval"] * c["ease"])
                c["repetitions"] += 1
                c["ease"] = max(1.3, c["ease"] + (0.1 - (5 - quality) * 0.08))
            else:
                c["repetitions"] = 0
                c["interval"] = 1
                c["ease"] = max(1.3, c["ease"] - 0.2)
            c["next_review"] = (datetime.utcnow() + timedelta(days=c["interval"])).isoformat()
            save_card(c)
            if st.session_state.idx < len(st.session_state.cards) - 1:
                st.session_state.idx += 1
            st.session_state.show_answer = False
            st.rerun()

        with g1:
            if st.button("Again", use_container_width=True): update_card(0)
        with g2:
            if st.button("Hard", use_container_width=True): update_card(1)
        with g3:
            if st.button("Good", use_container_width=True): update_card(2)
        with g4:
            if st.button("Easy", use_container_width=True): update_card(3)

st.caption(f"Next review in: {card.get('interval', 1)} day(s) • Ease: {card.get('ease', 2.5):.2f}")
