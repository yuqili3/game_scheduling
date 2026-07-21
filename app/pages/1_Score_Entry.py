"""Volunteer score entry: per-volunteer PIN, only their assigned courts.

Layout per court:
- every match this court has handled in the CURRENT round stays on the page
  with its recorded game scores (completed matches keep showing until the
  whole round is over — only then does the court section reset);
- the active match shows its recorded games as read-only chips plus one fresh
  input row for the next game.

Submitted scores carry the court number in the event payload, which is what
pins a match to this court's history.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import (  # noqa: E402
    court_status,
    env_badge,
    fmt_clock,
    get_store,
    load_state,
    match_desc,
    names,
    render_court_grid,
)
from core.models import Event  # noqa: E402

st.set_page_config(page_title="Score Entry", page_icon="✍️", layout="wide")
st.title("✍️ Score Entry")
env_badge()

cfg, md, slots, now = load_state()
volunteers = {v["name"]: v for v in cfg["broadcast"]["volunteers"]}

with st.sidebar:
    vol_name = st.selectbox("Volunteer", list(volunteers))
    pin = st.text_input("PIN", type="password")

if pin != str(volunteers[vol_name]["pin"]):
    st.warning("Enter your PIN to unlock your courts.")
    st.stop()

st.success(f"Signed in as {vol_name} — courts {volunteers[vol_name]['courts']}")

if not md.group_of:
    st.info("Group draw has not happened yet; nothing to score.")
    st.stop()

store = get_store()
events = store.events()

# the round currently being played; history resets only when it completes
unfinished_rounds = [n.round for n in md.nodes.values() if not md.node_finished(n.id)]
current_round = min(unfinished_rounds) if unfinished_rounds else None
if current_round is None:
    st.success("All rounds are finished 🎉")
    st.stop()
st.caption(f"Round {current_round} in progress — completed matches stay listed "
           "until the round ends.")

with st.expander("Live court map (same as the spectator page) — verify the "
                 "right people are on court", expanded=True):
    render_court_grid(cfg, md, slots, now)


def score_chips(games) -> str:
    return "  ·  ".join(f"**{a} : {b}**" for a, b in games) if games else "—"


for court in volunteers[vol_name]["courts"]:
    st.divider()
    st.markdown(f"## Court {court}")

    status, slot = court_status(md, slots, court, now)
    active_key = (slot.node, slot.match_slot) if slot else None

    # ---- history: matches recorded from this court in the current round ----
    hist_keys = []
    for e in events:
        if e.type != "game_finished" or e.payload.get("court") != court:
            continue
        nid = e.payload["node"]
        if md.nodes[nid].round != current_round:
            continue
        k = (nid, e.payload["slot"])
        if k != active_key and k not in hist_keys:
            hist_keys.append(k)
    for nid, mslot in hist_keys:
        m = md.matches.get((nid, mslot))
        if m is None:
            continue
        icon = "✅" if m.winner() is not None else "🟠"
        st.markdown(
            f"{icon} `{nid} {mslot}` {names(md, m.a)} vs {names(md, m.b)} — "
            f"{score_chips(m.games)}"
        )

    # ---- active match with fresh input for the next game ----
    if slot is None:
        st.caption("No more matches planned for this court.")
        continue
    d = match_desc(md, slot)
    m = md.matches.get(active_key)
    played = len(m.games) if m else 0
    next_game = played + 1
    st.markdown(
        f"### ▶ {d['matchup']}\n"
        f"`{d['match']}` · to **{d['points']} points** · planned {fmt_clock(slot.start, cfg)}\n\n"
        f"on court: **{d['players']}**"
    )
    if m and m.games:
        st.markdown("recorded: " + score_chips(m.games))
    if m and (not m.a or not m.b):
        st.warning("Lineup/blind draw not recorded yet — ask the admin desk.")

    with st.form(f"score_{court}_{slot.node}_{slot.match_slot}_{next_game}",
                 clear_on_submit=True):
        c1, c2 = st.columns(2)
        a = c1.number_input(f"Game {next_game} — side A", min_value=0, max_value=40,
                            step=1, key=f"a_{court}_{next_game}")
        b = c2.number_input(f"Game {next_game} — side B", min_value=0, max_value=40,
                            step=1, key=f"b_{court}_{next_game}")
        submitted = st.form_submit_button(f"Submit game {next_game}")
    if submitted:
        try:
            payload = {"node": slot.node, "slot": slot.match_slot,
                       "game": next_game, "score": [int(a), int(b)], "court": court}
            # validate against current state before persisting; md is rebuilt
            # from the store on the next rerun either way
            md.apply(Event(seq=0, ts="precheck", type="game_finished",
                           actor=vol_name, payload=payload))
            store.append("game_finished", vol_name, payload)
            st.success(f"Recorded {int(a)}:{int(b)} — score kept above, "
                       "next game input is ready.")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
