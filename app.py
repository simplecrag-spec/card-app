import json
import os
import streamlit as st
from datetime import datetime, timedelta

DATA_FILE = "flashcards.json"

def load_cards():
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_cards(cards):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False, indent=2)

if "cards" not in st.session_state:
    st.session_state.cards = load_cards()
if "idx" not in st.session_state:
    st.session_state.idx = 0 if st.session_state.cards else None
if "show_answer" not in st.session_state:
    st.session_state.show_answer = False

def tts_component(text: str, key: str):
    html = f"""
    <button id="ttsbtn_{key}" style="
        padding:0.6rem 1rem;
        font-size:1rem;
        margin:0.2rem;
        cursor:pointer;
        border:none;
        border-radius:6px;
        background:#4CAF50;
        color:white;">
        🔊 Speak
    </button>
    <script>
        const btn = document.getElementById('ttsbtn_{key}');
        btn.onclick = () => {{
            if ('speechSynthesis' in window) {{
                window.speechSynthesis.cancel();
                const utter = new SpeechSynthesisUtterance({json.dumps(text)});
                utter.lang = 'en-US';
                window.speechSynthesis.speak(utter);
            }} else {{
                alert('Your browser does not support speech synthesis.');
            }}
        }};
    </script>
    """
    st.components.v1.html(html, height=50)

st.set_page_config(page_title="Voice Flashcards", layout="centered")
st.title("🗣️ Voice‑Enabled Flashcards")

with st.expander("➕ Add a new flashcard", expanded=False):
    front = st.text_input("Front (question)", key="new_front")
    back = st.text_input("Back (answer)", key="new_back")
    if st.button("Add Card"):
        if front.strip() and back.strip():
            st.session_state.cards.append({
                "front": front.strip(),
                "back": back.strip(),
                "interval": 1,
                "repetitions": 0,
                "ease": 2.5,
                "next_review": datetime.utcnow().isoformat()
            })
            save_cards(st.session_state.cards)
            st.session_state.idx = len(st.session_state.cards) - 1
            st.session_state.show_answer = False
            st.success("Card added!")
            st.rerun()
        else:
            st.warning("Both fields are required.")

if not st.session_state.cards:
    st.info("No flashcards yet. Add one above to get started.")
    st.stop()

col_prev, col_next = st.columns([1,1])
with col_prev:
    if st.button("⟨ Prev", disabled=st.session_state.idx == 0):
        st.session_state.idx -= 1
        st.session_state.show_answer = False
        st.rerun()
with col_next:
    if st.button("Next ⟩", disabled=st.session_state.idx >= len(st.session_state.cards)-1):
        st.session_state.idx += 1
        st.session_state.show_answer = False
        st.rerun()

card = st.session_state.cards[st.session_state.idx]
st.progress((st.session_state.idx+1)/len(st.session_state.cards))

st.markdown(
    f"""
    <div style="
        border:2px solid #4CAF50;
        border-radius:12px;
        padding:1.5rem;
        min-height:180px;
        font-size:1.2rem;
        text-align:center;
        background:#1a1a1a;
        color:#ffffff;">
        <div>{card['front'] if not st.session_state.show_answer else card['back']}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

speak_text = card['front'] if not st.session_state.show_answer else card['back']
tts_component(speak_text, key=f"tts_{st.session_state.idx}")

btn_col1, btn_col2, btn_col3 = st.columns([1,1,1])
with btn_col1:
    if st.button("💡 Show Answer", use_container_width=True):
        st.session_state.show_answer = not st.session_state.show_answer
        st.rerun()
with btn_col2:
    if st.button("🔊 Speak", use_container_width=True):
        tts_component(speak_text, key=f"tts2_{st.session_state.idx}")
with btn_col3:
    if st.session_state.show_answer:
        g1, g2, g3, g4 = st.columns(4)
        def update_card(quality):
            card = st.session_state.cards[st.session_state.idx]
            if quality >= 3:
                if card['repetitions'] == 0:
                    card['interval'] = 1
                elif card['repetitions'] == 1:
                    card['interval'] = 6
                else:
                    card['interval'] = round(card['interval'] * card['ease'])
                card['repetitions'] += 1
                card['ease'] = max(1.3, card['ease'] + (0.1 - (5-quality)*0.08))
            else:
                card['repetitions'] = 0
                card['interval'] = 1
                card['ease'] = max(1.3, card['ease'] - 0.2)
            card['next_review'] = (datetime.utcnow() + timedelta(days=card['interval'])).isoformat()
            save_cards(st.session_state.cards)
            if st.session_state.idx < len(st.session_state.cards)-1:
                st.session_state.idx += 1
            st.session_state.show_answer = False
            st.rerun()
        with g1:
            if st.button("Again", use_container_width=True):
                update_card(0)
        with g2:
            if st.button("Hard", use_container_width=True):
                update_card(1)
        with g3:
            if st.button("Good", use_container_width=True):
                update_card(2)
        with g4:
            if st.button("Easy", use_container_width=True):
                update_card(3)

st.caption(f"Next review in: {card['interval']} day(s) • Ease: {card['ease']:.2f}")