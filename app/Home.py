"""Spectator page: live court grid, bracket, ranking and upcoming matches.

Run:  streamlit run app/Home.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import court_status, fmt_clock, load_state, match_desc, team_label  # noqa: E402

st.set_page_config(page_title="2026 Badminton Melee", page_icon="🏸", layout="wide")

cfg, md, slots, now = load_state()
refresh = int(cfg["broadcast"].get("refresh_seconds", 5))
colors = cfg["visual"]["court_colors"]
STATUS_LABEL = {
    "idle": "Idle",
    "warmup": "Warm-up",
    "game1": "Game 1",
    "game2": "Game 2",
    "game3": "Game 3",
}


@st.fragment(run_every=refresh)
def live_view() -> None:
    cfg, md, slots, now = load_state()
    st.caption(
        f"Now {fmt_clock(now, cfg)} · auto-refresh {refresh}s · "
        f"{len([s for s in slots])} planned slots remaining"
    )

    # ---- court grid ----
    st.subheader("Courts")
    total = cfg["courts"]["total"]
    for row_start in range(1, total + 1, 5):
        cols = st.columns(5)
        for i, court in enumerate(range(row_start, min(row_start + 5, total + 1))):
            status, slot = court_status(md, slots, court, now)
            color = colors[status if status.startswith("game") else status]
            with cols[i]:
                if slot is not None:
                    d = match_desc(md, slot)
                    when = (
                        "in play"
                        if status.startswith("game")
                        else f"next {fmt_clock(slot.start, cfg)}"
                    )
                    body = (
                        f"<b>{d['matchup']}</b><br>{d['match']} · game {d['game']} · "
                        f"to {d['points']} pts<br>{d['players']}<br>score: {d['score']}<br>{when}"
                    )
                else:
                    body = "no matches planned"
                st.markdown(
                    f"""<div style="background:{color};border-radius:10px;padding:10px;
                    min-height:150px;color:#111;font-size:0.82rem;line-height:1.35">
                    <b>Court {court}</b> — {STATUS_LABEL[status]}<br>{body}</div>""",
                    unsafe_allow_html=True,
                )

    # ---- bracket ----
    st.subheader("Bracket")
    winners = md.node_winners()
    cols = st.columns(3)
    for r, col in zip((1, 2, 3), cols):
        with col:
            st.markdown(f"**Round {r}**")
            for nid in sorted(n for n in md.nodes if md.nodes[n].round == r):
                gs = md.node_groups(nid)
                desc = f"{team_label(md, gs[0])} vs {team_label(md, gs[1])}" if gs else "TBD"
                if nid in winners:
                    status = f"✅ winner {winners[nid]}"
                elif any(k[0] == nid and m.games for k, m in md.matches.items()):
                    status = "🟠 in progress"
                else:
                    starts = [s.start for s in slots if s.node == nid]
                    status = f"est. {fmt_clock(min(starts), cfg)}" if starts else "pending"
                st.markdown(f"- `{nid}` [{md.nodes[nid].tag}] {desc} — {status}")

    # ---- ranking ----
    from core import rules

    ranking = rules.final_ranking(winners, md.nodes)
    if ranking:
        st.subheader("Ranking")
        st.table(
            [
                {"place": p, "group": ranking[p],
                 "team": team_label(md, ranking[p])}
                for p in sorted(ranking)
            ]
        )

    # ---- blind draw results ----
    blind = [
        (nid, m) for (nid, s), m in md.matches.items() if s == "BLIND" and (m.a or m.b)
    ]
    if blind:
        st.subheader("Blind draw results")
        for nid, m in sorted(blind):
            gs = md.node_groups(nid)
            a = ", ".join(md.base.players[p].name for p in m.a) or "TBD"
            b = ", ".join(md.base.players[p].name for p in m.b) or "TBD"
            st.markdown(f"- `{nid}` ({m.category}): {gs[0] if gs else '?'}: {a} — "
                        f"{gs[1] if gs else '?'}: {b}")

    # ---- upcoming ----
    st.subheader("Next up")
    upcoming = [s for s in slots if s.start >= now][:12]
    if upcoming:
        st.table(
            [
                {
                    "time": fmt_clock(s.start, cfg),
                    "court": s.court,
                    "node": s.node,
                    "match": s.match_slot,
                    "game": s.game,
                    "players": match_desc(md, s)["players"],
                }
                for s in upcoming
            ]
        )
    else:
        st.write("Nothing left to play 🎉")


st.title(f"🏸 {cfg['event']['name']} — Live")
if not md.group_of:
    st.info("Waiting for the group draw (admin page).")
live_view()
