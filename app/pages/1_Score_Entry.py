"""Volunteer score entry: per-volunteer PIN, only their assigned courts.

Each court card shows the matchup, the four players on court, the game number
and the scoring cap, so the volunteer can verify with the players before
submitting.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import court_status, fmt_clock, get_store, load_state, match_desc  # noqa: E402

st.set_page_config(page_title="Score Entry", page_icon="✍️", layout="wide")
st.title("✍️ Score Entry")

cfg, md, slots, now = load_state()
volunteers = {v["name"]: v for v in cfg["broadcast"]["volunteers"]}

with st.sidebar:
    name = st.selectbox("Volunteer", list(volunteers))
    pin = st.text_input("PIN", type="password")

if pin != str(volunteers[name]["pin"]):
    st.warning("Enter your PIN to unlock your courts.")
    st.stop()

st.success(f"Signed in as {name} — courts {volunteers[name]['courts']}")

if not md.group_of:
    st.info("Group draw has not happened yet; nothing to score.")
    st.stop()

store = get_store()

for court in volunteers[name]["courts"]:
    status, slot = court_status(md, slots, court, now)
    st.divider()
    if slot is None:
        st.markdown(f"### Court {court} — idle, no matches planned")
        continue
    d = match_desc(md, slot)
    m = md.matches.get((slot.node, slot.match_slot))
    played = len(m.games) if m else 0
    next_game = played + 1
    st.markdown(
        f"### Court {court} — {d['matchup']}\n"
        f"**{d['match']}** · next: game {next_game} · to **{d['points']} points** · "
        f"planned {fmt_clock(slot.start, cfg)}\n\n"
        f"players: **{d['players']}**\n\n"
        f"games so far: {d['score']}"
    )
    if m and (not m.a or not m.b):
        st.warning("Lineup/blind draw not recorded yet — ask the admin desk.")
    with st.form(f"score_{court}_{slot.node}_{slot.match_slot}"):
        c1, c2 = st.columns(2)
        a = c1.number_input("Side A points", min_value=0, max_value=40, step=1,
                            key=f"a_{court}")
        b = c2.number_input("Side B points", min_value=0, max_value=40, step=1,
                            key=f"b_{court}")
        submitted = st.form_submit_button(f"Submit game {next_game}")
    if submitted:
        try:
            payload = {"node": slot.node, "slot": slot.match_slot,
                       "game": next_game, "score": [int(a), int(b)]}
            # validate against current state before persisting; md is rebuilt
            # from the store on the next rerun either way
            from core.models import Event

            md.apply(Event(seq=0, ts="precheck", type="game_finished",
                           actor=name, payload=payload))
            store.append("game_finished", name, payload)
            st.success(f"Recorded {int(a)}:{int(b)} — schedule updates automatically.")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
