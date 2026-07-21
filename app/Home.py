"""Spectator page: live court grid, bracket, ranking and upcoming matches.

Run:  streamlit run app/Home.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    env_badge,
    fmt_clock,
    load_state,
    render_blind_board,
    render_bracket,
    render_court_grid,
    render_next_up,
    render_ranking,
)

st.set_page_config(page_title="2026 Badminton Melee", page_icon="🏸", layout="wide")

cfg, md, slots, now = load_state()
refresh = int(cfg["broadcast"].get("refresh_seconds", 5))


@st.fragment(run_every=refresh)
def live_view() -> None:
    cfg, md, slots, now = load_state()
    st.caption(
        f"Now {fmt_clock(now, cfg)} · auto-refresh {refresh}s · "
        f"{len([s for s in slots])} planned slots remaining"
    )

    # ---- court grid ----
    st.subheader("Courts")
    render_court_grid(cfg, md, slots, now)

    # ---- shared overview sections ----
    render_bracket(cfg, md, slots)
    render_ranking(md)
    render_blind_board(md)
    render_next_up(cfg, md, slots, now)


st.title(f"🏸 {cfg['event']['name']} — Live")
env_badge()
if not md.group_of:
    st.info("Waiting for the group draw (admin page).")
live_view()
