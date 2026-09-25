"""Admin desk, four tabs:

- Live: the host console — everything the spectator page shows (court map,
  bracket, ranking, blind results, next-up) plus the announce-and-confirm
  flow: for each free court the host reads out who plays what where, watches
  the players walk on, then presses Confirm (a match_started event). Only
  then does the volunteer's score entry unlock for that match, and the
  schedule pins the match to that court.

- Group draw: seed-driven random assignment of the 8 teams to G1-G8; the seed
  is logged with the event, so the draw is reproducible.
- Lineups: read-only status board — per round and matchup, whether each
  captain has submitted a lineup, whether it is compliant (issues listed),
  whether the blind draw is done (with result and seed), and each team's
  absence/substitute status. Entry itself happens on the captain page.
- Score correction: override a mis-entered score via a game_corrected
  compensating event; the original entry stays in the log.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import (  # noqa: E402
    env_badge,
    get_store,
    load_state,
    match_desc,
    names,
    render_blind_board,
    render_bracket,
    render_court_grid,
    render_next_up,
    render_ranking,
    team_label,
)
from core.draw import group_draw_assign  # noqa: E402
from core.models import Event, parse_seed  # noqa: E402

st.set_page_config(page_title="Admin", page_icon="🗂️", layout="wide")
st.title("🗂️ Admin Desk")
env_badge()

cfg, md, slots, now = load_state()
store = get_store()

admin_pin = str(cfg["broadcast"].get("admin_pin", "admin"))
pin = st.sidebar.text_input("Admin PIN", type="password")
if pin != admin_pin:
    st.warning("Enter the admin PIN (config: broadcast.admin_pin).")
    st.stop()


def precheck_and_append(type_: str, payload: dict, seed=None) -> bool:
    """Validate against the current state, then persist. Returns success."""
    try:
        md.apply(Event(seq=0, ts="precheck", type=type_, actor="admin",
                       payload=payload, seed=seed))
        store.append(type_, "admin", payload, seed=seed)
        return True
    except ValueError as exc:
        st.error(str(exc))
        return False


tab_live, tab_draw, tab_lineups, tab_fix = st.tabs(
    ["Live (host console)", "Group draw", "Lineups", "Score correction"]
)

with tab_live:
    if not md.group_of:
        st.info("Run the group draw first (next tab).")
    else:
        render_court_grid(cfg, md, slots, now)
        st.subheader("Announce & confirm")
        st.caption(
            "Read the announcement out loud, watch the players walk onto the "
            "court, then press Confirm. The match becomes officially in play "
            "and the volunteer's score entry unlocks."
        )
        total = cfg["courts"]["total"]
        for court in range(1, total + 1):
            busy = md.started_on_court(court)
            if busy is not None:
                nid, mslot = busy
                m = md.matches[(nid, mslot)]
                score = "  ".join(f"{a}:{b}" for a, b in m.games) or "no games yet"
                st.markdown(
                    f"**Court {court}** — 🔴 in play: `{nid} {mslot}` "
                    f"{names(md, m.a)} vs {names(md, m.b)} ({score}) — "
                    "waiting for the volunteer's score"
                )
                continue
            cand = next(
                (sl for sl in slots
                 if sl.court == court and (sl.node, sl.match_slot) not in md.started),
                None,
            )
            if cand is None:
                st.markdown(f"**Court {court}** — 🟢 free, nothing to call")
                continue
            d = match_desc(md, cand)
            c1, c2 = st.columns([4, 1])
            with c1:
                st.markdown(
                    f"**Court {court}** — 📣 announce: {d['matchup']} · "
                    f"`{d['match']}` · **{d['players']}** → Court {court}"
                )
            with c2:
                if st.button("Confirm started", key=f"start_{court}"):
                    payload = {"node": cand.node, "slot": cand.match_slot,
                               "court": court}
                    if precheck_and_append("match_started", payload):
                        st.rerun()
        st.divider()
        render_bracket(cfg, md, slots)
        render_ranking(md)
        render_blind_board(md)
        render_next_up(cfg, md, slots, now)

with tab_draw:
    if md.group_of:
        st.info("Group draw recorded: " + "  ·  ".join(
            f"**{g}** → {team_label(md, g)}" for g in sorted(md.group_of)))
    st.caption("Enter a random seed (announce it out loud first). The 8 teams "
               "are shuffled into G1–G8 deterministically — same seed, same draw.")
    with st.form("group_draw"):
        seed_text = st.text_input("Random seed (a number or any text)")
        label = "Redraw groups" if md.group_of else "Draw groups"
        if st.form_submit_button(label):
            try:
                seed = parse_seed(seed_text)
            except ValueError as exc:
                st.error(str(exc))
                seed = None
            if seed is not None:
                groups = group_draw_assign(sorted(md.base.teams), seed)
                if precheck_and_append("group_draw", {"groups": groups}, seed=seed):
                    st.success(f"Drawn with seed {seed!r}.")
                    st.rerun()

with tab_lineups:
    if not md.group_of:
        st.info("Waiting for the group draw.")
    else:
        st.caption("Read-only status board. Captains submit lineups, run blind "
                   "draws and register absences on the Captain page.")
        for round_no in (1, 2, 3):
            st.markdown(f"#### Round {round_no}")
            for nid in sorted(n for n, node in md.nodes.items()
                              if node.round == round_no):
                teams = md.node_teams(nid)
                if teams is None:
                    st.markdown(f"`{nid}` — opponents not decided yet")
                    continue
                gs = md.node_groups(nid)
                st.markdown(f"`{nid}` **{team_label(md, gs[0])} vs "
                            f"{team_label(md, gs[1])}**")
                blind = md.matches.get((nid, "BLIND"))
                blind_seeds = {
                    e.payload["team"]: e.seed
                    for e in store.events()
                    if e.type == "blind_draw_result" and e.payload.get("node") == nid
                }
                for side, tid in zip(("a", "b"), teams):
                    status = md.lineup_status(nid, tid)
                    if not status["submitted"]:
                        lineup_txt = "❌ lineup not submitted"
                    elif status["complete"]:
                        lineup_txt = "✅ lineup complete"
                    else:
                        lineup_txt = "⚠️ lineup incomplete: " + "; ".join(status["issues"])
                    bpids = list(getattr(blind, side)) if blind else []
                    if bpids:
                        seed_txt = (f", seed {blind_seeds[tid]}"
                                    if blind_seeds.get(tid) is not None else "")
                        blind_txt = f"✅ blind: {names(md, bpids)}{seed_txt}"
                    else:
                        blind_txt = "❌ blind not drawn"
                    short = md.shorthanded.get(tid)
                    if short:
                        subs = ", ".join(
                            f"R{r}: {md.base.players[p].name}"
                            for r, p in sorted(short["substitutes"].items())
                        ) or "no substitute assigned"
                        absent_txt = (f"🚑 absent {md.base.players[short['absent_id']].name} "
                                      f"({subs}, {cfg['format']['shorthanded_points']}-pt scoring)")
                    else:
                        absent_txt = "full squad"
                    st.markdown(f"- Team {tid}: {lineup_txt} · {blind_txt} · {absent_txt}")

with tab_fix:
    st.caption("Override a mis-entered score. The original entry stays in the "
               "event log; a correction event is appended on top.")
    scored = sorted({k for k, m in md.matches.items() if m.games})
    if not scored:
        st.info("No recorded games yet.")
    else:
        key = st.selectbox("Match", scored,
                           format_func=lambda k: f"{k[0]} {k[1]}", key="fix_match")
        m = md.matches[key]
        st.markdown("recorded: " + "  ·  ".join(f"game {i + 1}: **{a} : {b}**"
                                                for i, (a, b) in enumerate(m.games)))
        with st.form("fix"):
            game_no = st.selectbox("Game to correct", list(range(1, len(m.games) + 1)))
            c1, c2 = st.columns(2)
            a = c1.number_input("Corrected side A", min_value=0, max_value=40, step=1)
            b = c2.number_input("Corrected side B", min_value=0, max_value=40, step=1)
            if st.form_submit_button("Apply correction"):
                payload = {"node": key[0], "slot": key[1], "game": int(game_no),
                           "score": [int(a), int(b)]}
                if precheck_and_append("game_corrected", payload):
                    st.success("Correction applied — all pages update on next refresh.")
                    st.rerun()

st.divider()
st.subheader("Event log (latest 20)")
for e in store.events()[-20:][::-1]:
    st.text(f"[{e.seq}] {e.ts} {e.type} by {e.actor}: {e.payload}")
