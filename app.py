import json, os, io, asyncio, time, requests, base64
from datetime import datetime, timedelta

import streamlit as st
import edge_tts
import firebase_admin
from firebase_admin import credentials as fb_creds, firestore
from supabase import create_client

# ================= PAGE SETUP =================
st.set_page_config(page_title="Flashcards", layout="centered", page_icon="📚")

# ================= SECRETS =================
ELEVEN_KEY = st.secrets.get("ELEVEN_API_KEY", os.getenv("ELEVEN_API_KEY", ""))
FIREBASE_CRED = st.secrets.get("FIREBASE_CRED", os.getenv("FIREBASE_CRED", "{}"))
SUPA_URL = st.secrets.get("SUPABASE_URL", os.getenv("SUPABASE_URL", ""))
SUPA_KEY = st.secrets.get("SUPABASE_KEY", os.getenv("SUPABASE_KEY", ""))

# ================= TTS: edge-tts =================
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

VOICE_CACHE = {}
def edge_speak(text: str, voice: str, rate: str = "-10%"):
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

def get_audio_bytes(text: str, voice: str, rate: str = "-10%"):
    """Get audio bytes for text — tries ElevenLabs first, then edge-tts."""
    if ELEVEN_KEY:
        eleven = eleven_speak(text)
        if eleven:
            return eleven, "audio/mp3"
    edge = edge_speak(text, voice, rate)
    if edge:
        return edge, "audio/mp3"
    return None, None

def play_voice(text: str, voice: str, rate: str = "-10%"):
    audio, fmt = get_audio_bytes(text, voice, rate)
    if audio:
        st.audio(audio, format=fmt)
    else:
        # browser fallback
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
            🔊 Speak
        </button>
        """
        st.markdown(html, unsafe_allow_html=True)

# ================= STORAGE =================
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
    cols = ["front", "back", "interval", "repetitions", "ease", "next_review",
            "created_at", "subject", "chapter"]
    return {k: v for k, v in card.items() if k in cols}

# ---------- metadata helpers ----------
META_KEY = "vf_meta.json"

def load_meta():
    """Load {subjects: {subj: [chapters]}} from file or defaults."""
    if os.path.exists(META_KEY):
        with open(META_KEY, encoding="utf-8") as f:
            m = json.load(f)
        m.setdefault("subjects", {})
        return m
    return {"subjects": {}}

def save_meta(meta):
    with open(META_KEY, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

# ---------- card storage ----------
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
            cards = json.load(f)
        for c in cards:
            c.setdefault("subject", "General")
            c.setdefault("chapter", "")
        return cards
    return []

def save_cards(cards):
    with open("flashcards.json", "w", encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False, indent=2)

def save_card(card):
    b = backend()
    card.setdefault("created_at", datetime.utcnow().isoformat())
    card["updated_at"] = datetime.utcnow().isoformat()
    card.setdefault("subject", "General")
    card.setdefault("chapter", "")
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
    cards = load_cards()
    cards.append(card)
    save_cards(cards)

def update_card_in_list(card):
    """Update a card in session and persist."""
    b = backend()
    card["updated_at"] = datetime.utcnow().isoformat()
    try:
        if b == "firebase" and "_id" in card and card["_id"]:
            get_firestore().collection("flashcards").document(card["_id"]).set(card, merge=True)
            return
        if b == "supabase" and "_id" in card and card["_id"]:
            get_supa().table(SUPA_TABLE).update(_fields(card)).eq("id", int(card["_id"])).execute()
            return
    except Exception:
        pass
    save_cards(st.session_state.cards)

def delete_card(card_id):
    """Delete a single card by ID."""
    b = backend()
    try:
        if b == "firebase" and "_id" in card_id:
            get_firestore().collection("flashcards").document(card_id["_id"]).delete()
            return
        if b == "supabase" and "_id" in card_id:
            get_supa().table(SUPA_TABLE).delete().eq("id", int(card_id["_id"])).execute()
            return
    except Exception as e:
        st.warning(f"Delete failed: {e}")
    # fallback: remove from local
    cards_list = load_cards()
    cards_list = [c for c in cards_list if c.get("_id") != card_id.get("_id")]
    save_cards(cards_list)

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
if "meta" not in st.session_state:
    st.session_state.meta = load_meta()
if "browse_mode" not in st.session_state:
    st.session_state.browse_mode = False
if "browse_subject" not in st.session_state:
    st.session_state.browse_subject = ""
if "browse_chapter" not in st.session_state:
    st.session_state.browse_chapter = ""

def _reload():
    st.session_state.cards = load_cards()

# ================= PAGE =================
st.title("📚 Flashcards")

# ---------- Sidebar ----------
with st.sidebar:
    st.markdown("### 🔊 Voice (Reading)")
    voice_label = st.selectbox("Select voice",
        list(EDGE_VOICES.keys()), index=1, label_visibility="collapsed")
    st.session_state.voice = EDGE_VOICES[voice_label]
    rate_pct = st.slider("Speed", -30, 30, -10)
    st.session_state.rate = f"{rate_pct}%"

    st.markdown("---")
    st.markdown("### 🌩 Storage")
    b = backend()
    if b == "firebase":
        st.success("Firebase connected ☁️")
    elif b == "supabase":
        st.success("Supabase connected ☁️")
    else:
        st.warning("⚠️ Local storage only")

    # ---- SUBJECTS & CHAPTERS ----
    st.markdown("---")
    st.markdown("### 📚 Subjects & Chapters")
    meta = st.session_state.meta
    subjects = meta.get("subjects", {})

    subj_options = ["All Subjects"] + sorted(subjects.keys())
    sel_subj = st.selectbox("Filter by Subject", subj_options, key="filter_subj")

    chap_options = ["All Chapters"]
    if sel_subj and sel_subj != "All Subjects" and sel_subj in subjects:
        chap_options += sorted(subjects[sel_subj])
    sel_chap = st.selectbox("Filter by Chapter", chap_options, key="filter_chap")

    # Add subject / chapter
    with st.expander("➕ Add Subject / Chapter", expanded=False):
        new_subj = st.text_input("Subject name", key="new_subj")
        new_chap = st.text_input("Chapter name (inside subject)", key="new_chap")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Add Subject", use_container_width=True):
                if new_subj.strip() and new_subj.strip() not in meta["subjects"]:
                    meta["subjects"][new_subj.strip()] = []
                    save_meta(meta)
                    st.rerun()
                elif new_subj.strip() in meta["subjects"]:
                    st.warning("Subject already exists.")
        with c2:
            if st.button("Add Chapter", use_container_width=True):
                s = sel_subj if (sel_subj and sel_subj != "All Subjects") else new_subj.strip()
                ch = new_chap.strip()
                if s and ch and ch not in meta["subjects"].get(s, []):
                    meta["subjects"].setdefault(s, []).append(ch)
                    save_meta(meta)
                    st.rerun()
                elif ch in meta["subjects"].get(s, []):
                    st.warning("Chapter already exists.")

    # quick add chapter inside selected subject
    if sel_subj and sel_subj != "All Subjects":
        quick_chap = st.text_input(f"Chapter for '{sel_subj}'", key="quick_chap")
        if st.button(f"➕ Add Chapter to {sel_subj}", use_container_width=True):
            if quick_chap.strip() and quick_chap.strip() not in meta["subjects"].get(sel_subj, []):
                meta["subjects"][sel_subj].append(quick_chap.strip())
                save_meta(meta)
                st.rerun()

    
    # Subject / chapter summary with browse links
    with st.expander("📋 Browse Cards", expanded=False):
        for s in sorted(subjects.keys()):
            sc = [c for c in st.session_state.cards if c.get("subject") == s]
            chapters = subjects[s]
            col1, col2 = st.columns([0.7, 0.3])
            with col1:
                st.markdown(f"**{s}** — {len(sc)} card(s)")
            with col2:
                if st.button(f"📋 Browse", key=f"browse_{s}", help=f"View all cards in {s}"):
                    st.session_state.browse_mode = True
                    st.session_state.browse_subject = s
                    st.session_state.browse_chapter = ""
                    st.rerun()

            for ch in sorted(chapters):
                cc = [c for c in sc if c.get("chapter") == ch]
                col1, col2 = st.columns([0.7, 0.3])
                with col1:
                    st.markdown(f"  └ {ch} — {len(cc)} card(s)")
                with col2:
                    if st.button(f"📋", key=f"browse_{s}_{ch}", help=f"View all cards in {ch}"):
                        st.session_state.browse_mode = True
                        st.session_state.browse_subject = s
                        st.session_state.browse_chapter = ch
                        st.rerun()

    
# ---------- Add card ----------
with st.expander("➕ Add New Flashcard", expanded=False):
    meta = st.session_state.meta
    subjects = meta.get("subjects", {})

    add_subj_options = sorted(subjects.keys())
    if add_subj_options:
        subj = st.selectbox("Subject", add_subj_options, key="add_subj_sel")
    else:
        subj = st.text_input("Subject", value="General", key="add_subj_txt")

    # chapter dropdown if subject has chapters, else free text
    subj_val = subj.strip() if isinstance(subj, str) else subj
    available_chapters = subjects.get(subj_val, [])
    if available_chapters:
        chap = st.selectbox("Chapter", ["(none)"] + sorted(available_chapters), key="add_chap_sel")
        if chap == "(none)":
            chap = ""
    else:
        chap = st.text_input("Chapter (optional)", value="", key="add_chap_txt")

    front = st.text_input("Front (question)", key="new_front")
    back = st.text_input("Back (answer)", key="new_back")

    if st.button("Add Card"):
        if front.strip() and back.strip():
            s = subj_val or "General"
            ch = chap.strip() if isinstance(chap, str) else chap
            # auto-create subject if new
            if s not in meta["subjects"]:
                meta["subjects"][s] = []
            # add chapter if new
            if ch and ch not in meta["subjects"].get(s, []):
                meta["subjects"].setdefault(s, []).append(ch)
            save_meta(meta)

            save_card({
                "front": front.strip(), "back": back.strip(),
                "interval": 1, "repetitions": 0, "ease": 2.5,
                "next_review": datetime.utcnow().isoformat(),
                "created_at": datetime.utcnow().isoformat(),
                "subject": s, "chapter": ch,
            })
            _reload()
            st.session_state.idx = len(st.session_state.cards) - 1
            st.session_state.show_answer = False
            st.success("Card added!")
            st.rerun()
        else:
            st.warning("Both front and back are required.")

# ---------- Filter helpers ----------
meta = st.session_state.meta
subjects = meta.get("subjects", {})

filtered = st.session_state.cards
if sel_subj and sel_subj != "All Subjects":
    filtered = [c for c in filtered if c.get("subject") == sel_subj]
if sel_chap and sel_chap != "All Chapters":
    filtered = [c for c in filtered if c.get("chapter") == sel_chap]

# ---------- Sort by priority (due cards first) ----------
now_iso = datetime.utcnow().isoformat()
due_cards = [c for c in filtered if c.get("next_review", "") <= now_iso]
future_cards = [c for c in filtered if c.get("next_review", "") > now_iso]
due_cards.sort(key=lambda x: x.get("next_review", ""))
future_cards.sort(key=lambda x: x.get("next_review", ""))
filtered = due_cards + future_cards

# ---------- Main card view or browser view ----------
if st.session_state.browse_mode:
    # ===== CARD BROWSER VIEW =====
    st.markdown("---")
    col1, col2 = st.columns([0.8, 0.2])
    with col1:
        browse_title = f"📋 {st.session_state.browse_subject}"
        if st.session_state.browse_chapter:
            browse_title += f" › {st.session_state.browse_chapter}"
        st.subheader(browse_title)
    with col2:
        if st.button("← Study", use_container_width=True):
            st.session_state.browse_mode = False
            st.rerun()

    # Filter cards for browser view
    browse_cards = [c for c in st.session_state.cards
                   if c.get("subject") == st.session_state.browse_subject]
    if st.session_state.browse_chapter:
        browse_cards = [c for c in browse_cards
                       if c.get("chapter") == st.session_state.browse_chapter]

    st.markdown(f"**{len(browse_cards)} cards**")

    if not browse_cards:
        st.info("No cards in this category.")
    else:
        # Display cards in a grid
        for i, card in enumerate(browse_cards):
            with st.container():
                col1, col2, col3 = st.columns([0.05, 0.8, 0.15])
                with col1:
                    st.markdown(f"**{i+1}**")
                with col2:
                    # Card preview
                    front_preview = card["front"][:60] + ("..." if len(card["front"]) > 60 else "")
                    back_preview = card["back"][:60] + ("..." if len(card["back"]) > 60 else "")

                    with st.expander(f"**Q:** {front_preview}", expanded=False):
                        st.markdown(f"**Question:** {card['front']}")
                        st.markdown(f"**Answer:** {card['back']}")

                        # Card stats
                        nr = card.get("next_review", "")
                        is_due = nr <= now_iso if nr else True
                        due_status = "Due now" if is_due else f"Due {nr[:10]}"
                        st.caption(f"Interval: {card.get('interval', 1)} days • {due_status}")

                with col3:
                    # Delete button
                    if st.button("🗑", key=f"browse_del_{i}", help="Delete this card"):
                        delete_card(card)
                        st.session_state.cards = load_cards()
                        st.rerun()

            st.markdown("---")
    st.stop()

# ===== NORMAL STUDY VIEW =====
if not filtered:
    st.info("No flashcards match current filters. Add one above, or change filters.")
    st.stop()

# map filtered index back to real index
real_indices = [st.session_state.cards.index(c) for c in filtered]

if st.session_state.idx is None or st.session_state.idx >= len(filtered):
    st.session_state.idx = 0

card = filtered[st.session_state.idx]

# Due indicator
nr = card.get("next_review", "")
is_due = nr <= now_iso if nr else True
due_label = "🟢 Due now" if is_due else f"⏳ Due {nr[:10]}"

# subject/chapter breadcrumb with delete button
sc = card.get("subject", "General")
ch = card.get("chapter", "")
col1, col2 = st.columns([0.85, 0.15])
with col1:
    st.caption(f"📚 {sc}" + (f" › {ch}" if ch else "") + f"  •  {due_label}")
with col2:
    if st.button("🗑", key=f"del_{card.get('_id', st.session_state.idx)}", help="Delete this card"):
        idx = real_indices[st.session_state.idx]
        delete_card(st.session_state.cards[idx])
        _reload()
        if st.session_state.idx >= len(filtered) - 1 and st.session_state.idx > 0:
            st.session_state.idx -= 1
        st.session_state.show_answer = False
        st.rerun()

# Stats bar
st.markdown(f"**Card {st.session_state.idx + 1} / {len(filtered)}** — "
            f"📗 Due: {len(due_cards)} · 📘 Later: {len(future_cards)}")

# Navigation
col_prev, col_next = st.columns([1, 1])
with col_prev:
    if st.button("⟨ Prev", disabled=st.session_state.idx <= 0):
        st.session_state.idx -= 1
        st.session_state.show_answer = False
        st.rerun()
with col_next:
    if st.button("Next ⟩", disabled=st.session_state.idx >= len(filtered) - 1):
        st.session_state.idx += 1
        st.session_state.show_answer = False
        st.rerun()

st.progress((st.session_state.idx + 1) / len(filtered))

# ---- Card display ----
text_now = card["front"] if not st.session_state.show_answer else card["back"]
side_label = "QUESTION" if not st.session_state.show_answer else "ANSWER"

st.markdown(f"""
    <div style="
        border:3px solid #52b788; border-radius:14px;
        padding:2rem; min-height:220px; font-size:1.35rem;
        text-align:center; background:#1a1a1a; color:#ffffff;
        box-shadow:0 10px 30px rgba(0,0,0,0.5);
        display:flex; flex-direction:column; align-items:center; justify-content:center;">
        <div style="font-size:0.7rem; color:#52b788; text-transform:uppercase;
                    letter-spacing:0.15em; margin-bottom:0.5rem;">{side_label}</div>
        <div style="line-height:1.6;">{text_now}</div>
    </div>
    """, unsafe_allow_html=True)

# ---- Show Answer button ----
if st.button("💡 Show Answer" if not st.session_state.show_answer else "🔄 Show Question"):
    st.session_state.show_answer = not st.session_state.show_answer
    st.rerun()

# ---- Spaced Repetition Grading (Anki-style with intervals) ----
if st.session_state.show_answer:
    st.markdown("---")
    st.markdown("**Rate this card — when should it come back?**")

    def schedule_card(interval_days):
        """Set the card's next review to interval_days from now."""
        idx = real_indices[st.session_state.idx]
        c = st.session_state.cards[idx]
        c["interval"] = interval_days
        c["repetitions"] = c.get("repetitions", 0) + 1
        if interval_days <= 1:
            c["repetitions"] = 0
            c["ease"] = max(1.3, c.get("ease", 2.5) - 0.2)
        else:
            c["ease"] = min(3.0, c.get("ease", 2.5) + 0.05)
        c["next_review"] = (datetime.utcnow() + timedelta(days=interval_days)).isoformat()
        update_card_in_list(c)
        # advance to next card
        if st.session_state.idx < len(filtered) - 1:
            st.session_state.idx += 1
        st.session_state.show_answer = False
        st.rerun()

    # Calculate suggested intervals based on current card state
    cur_interval = card.get("interval", 1)
    cur_ease = card.get("ease", 2.5)
    reps = card.get("repetitions", 0)

    # Anki-like intervals
    again_days = 0  # ~10 min (same session)
    hard_days = max(1, int(cur_interval * 1.2)) if reps > 0 else 1
    good_days = max(1, int(cur_interval * cur_ease)) if reps > 0 else 3
    easy_days = max(good_days + 1, int(cur_interval * cur_ease * 1.3)) if reps > 0 else 7

    def _label(days):
        if days == 0: return "< 10 min"
        if days == 1: return "1 day"
        if days < 30: return f"{days} days"
        if days < 365: return f"{days // 30} mo"
        return f"{days // 365}y"

    g1, g2, g3, g4 = st.columns(4)
    with g1:
        if st.button(f"🔴 Again\n{_label(again_days)}", use_container_width=True):
            schedule_card(again_days)
    with g2:
        if st.button(f"🟠 Hard\n{_label(hard_days)}", use_container_width=True):
            schedule_card(hard_days)
    with g3:
        if st.button(f"🟢 Good\n{_label(good_days)}", use_container_width=True):
            schedule_card(good_days)
    with g4:
        if st.button(f"🔵 Easy\n{_label(easy_days)}", use_container_width=True):
            schedule_card(easy_days)

    # Custom interval
    with st.expander("⏱ Custom interval"):
        custom_days = st.number_input("Repeat after (days)", min_value=0, max_value=365, value=1, step=1)
        if st.button("Set custom interval"):
            schedule_card(custom_days)

st.caption(f"Interval: {card.get('interval', 1)} day(s) • Ease: {card.get('ease', 2.5):.2f} • "
           f"Reps: {card.get('repetitions', 0)}")

# ================= AUTO-PLAY TTS READER =================
st.markdown("---")
st.markdown("### 🔊 Auto Voice Reader")
st.markdown("Reads through all filtered cards: **Question → 3s pause → Answer → 2s pause → next card**")

def _build_autoplay_html(cards_data, voice, rate):
    """Build an HTML component that auto-reads cards using browser TTS.

    Sequence per card:
      1. Speak the question
      2. Wait 3 seconds
      3. Speak the answer
      4. Wait 2 seconds
      5. Move to next card
    """
    cards_json = json.dumps(cards_data)
    html = f"""
    <div id="ap-container" style="padding:16px; background:#111; border-radius:12px; color:#fff; font-family:sans-serif;">
      <div style="display:flex; gap:10px; align-items:center; margin-bottom:12px;">
        <button id="ap-play" onclick="apStart()" style="padding:10px 24px;background:#2d6a4f;color:#fff;border:none;border-radius:8px;font-size:1rem;cursor:pointer;">
          ▶ Play
        </button>
        <button id="ap-pause" onclick="apPause()" style="padding:10px 24px;background:#d4a017;color:#fff;border:none;border-radius:8px;font-size:1rem;cursor:pointer;display:none;">
          ⏸ Pause
        </button>
        <button id="ap-stop" onclick="apStop()" style="padding:10px 24px;background:#c0392b;color:#fff;border:none;border-radius:8px;font-size:1rem;cursor:pointer;display:none;">
          ⏹ Stop
        </button>
      </div>
      <div id="ap-status" style="font-size:0.9rem; color:#8be19a; min-height:24px;">Press Play to start auto-reading.</div>
      <div id="ap-card" style="margin-top:10px; padding:16px; background:#1a1a2e; border-radius:8px; min-height:80px; display:none;">
        <div id="ap-side" style="font-size:0.7rem; color:#52b788; text-transform:uppercase; letter-spacing:0.1em; margin-bottom:6px;"></div>
        <div id="ap-text" style="font-size:1.15rem; line-height:1.5;"></div>
      </div>
      <div id="ap-progress" style="margin-top:8px; font-size:0.8rem; color:#888;"></div>
    </div>
    <script>
    (function() {{
      var cards = {cards_json};
      var idx = 0;
      var running = false;
      var paused = false;
      var timer = null;

      function show(id) {{ document.getElementById(id).style.display = ''; }}
      function hide(id) {{ document.getElementById(id).style.display = 'none'; }}

      function speak(text) {{
        return new Promise(function(resolve) {{
          if (!('speechSynthesis' in window)) {{ resolve(); return; }}
          window.speechSynthesis.cancel();
          var u = new SpeechSynthesisUtterance(text);
          u.lang = 'en-US';
          u.rate = 0.8;  // Slower, more natural
          u.pitch = 0.9; // Slightly lower pitch
          u.volume = 0.9;
          // Try to use a more natural voice
          var voices = window.speechSynthesis.getVoices();
          var naturalVoice = voices.find(v =>
            v.name.includes('Natural') ||
            v.name.includes('Enhanced') ||
            v.name.includes('Premium') ||
            (v.name.includes('Google') && v.lang === 'en-US')
          );
          if (naturalVoice) u.voice = naturalVoice;
          u.onend = function() {{ resolve(); }};
          u.onerror = function() {{ resolve(); }};
          window.speechSynthesis.speak(u);
        }});
      }}

      function wait(ms) {{
        return new Promise(function(resolve) {{
          timer = setTimeout(resolve, ms);
        }});
      }}

      function updateUI(side, text, cardIdx) {{
        document.getElementById('ap-card').style.display = '';
        document.getElementById('ap-side').textContent = side;
        document.getElementById('ap-text').textContent = text;
        document.getElementById('ap-progress').textContent = 'Card ' + (cardIdx + 1) + ' / ' + cards.length;
      }}

      async function playLoop() {{
        running = true;
        hide('ap-play');
        show('ap-pause');
        show('ap-stop');

        while (idx < cards.length && running) {{
          if (paused) {{
            await wait(200);
            continue;
          }}
          var c = cards[idx];
          // Question
          document.getElementById('ap-status').textContent = 'Reading question...';
          updateUI('QUESTION', c.front, idx);
          await speak(c.front);
          if (!running) break;

          // 3 sec pause
          document.getElementById('ap-status').textContent = 'Pause... (3s)';
          await wait(3000);
          if (!running) break;

          // Answer
          document.getElementById('ap-status').textContent = 'Reading answer...';
          updateUI('ANSWER', c.back, idx);
          await speak(c.back);
          if (!running) break;

          // 2 sec pause
          document.getElementById('ap-status').textContent = 'Next card in 2s...';
          await wait(2000);
          if (!running) break;

          idx++;
        }}

        if (running) {{
          document.getElementById('ap-status').textContent = '✅ Finished all ' + cards.length + ' cards!';
        }}
        apReset();
      }}

      function apReset() {{
        running = false;
        paused = false;
        show('ap-play');
        hide('ap-pause');
        hide('ap-stop');
      }}

      window.apStart = function() {{
        if (cards.length === 0) {{
          document.getElementById('ap-status').textContent = 'No cards to read.';
          return;
        }}
        idx = 0;
        paused = false;
        playLoop();
      }};

      window.apPause = function() {{
        if (paused) {{
          paused = false;
          document.getElementById('ap-pause').textContent = '⏸ Pause';
          document.getElementById('ap-status').textContent = 'Resumed...';
        }} else {{
          paused = true;
          window.speechSynthesis.cancel();
          document.getElementById('ap-pause').textContent = '▶ Resume';
          document.getElementById('ap-status').textContent = 'Paused.';
        }}
      }};

      window.apStop = function() {{
        running = false;
        paused = false;
        if (timer) clearTimeout(timer);
        window.speechSynthesis.cancel();
        document.getElementById('ap-status').textContent = 'Stopped.';
        document.getElementById('ap-card').style.display = 'none';
        document.getElementById('ap-progress').textContent = '';
        apReset();
      }};
    }})();
    </script>
    """
    return html

# Prepare card data for auto-play (only filtered, priority-sorted cards)
autoplay_cards = [{"front": c["front"], "back": c["back"]} for c in filtered]
autoplay_html = _build_autoplay_html(autoplay_cards, st.session_state.voice, st.session_state.rate)
st.components.v1.html(autoplay_html, height=280)

# End of else block for browse_mode
