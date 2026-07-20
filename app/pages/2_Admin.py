"""Admin desk: group draw, lineup submission, blind draw results,
absence registration and per-round substitutes.

Every action appends an event; invalid input is rejected by the same
validation that the replay uses, so the log can never go inconsistent.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import get_store, load_state, team_label  # noqa: E402
from core.models import Event  # noqa: E402

st.set_page_config(page_title="Admin", page_icon="🗂️", layout="wide")
st.title("🗂️ Admin Desk")

cfg, md, slots, now = load_state()
store = get_store()

admin_pin = str(cfg["broadcast"].get("admin_pin", "admin"))
pin = st.sidebar.text_input("Admin PIN", type="password")
if pin != admin_pin:
    st.warning("Enter the admin PIN (config: broadcast.admin_pin).")
    st.stop()


def precheck_and_append(type_: str, payload: dict) -> bool:
    """Validate against the current state, then persist. Returns success."""
    try:
        md.apply(Event(seq=0, ts="precheck", type=type_, actor="admin", payload=payload))
        store.append(type_, "admin", payload)
        return True
    except ValueError as exc:
        st.error(str(exc))
        return False


team_options = {
    f"T{tid} ({md.base.players[members[0]].name})": tid
    for tid, members in sorted(md.base.teams.items())
}

tab_draw, tab_lineup, tab_blind, tab_absence = st.tabs(
    ["Group draw", "Lineups", "Blind draw", "Absence / substitute"]
)

with tab_draw:
    if md.group_of:
        st.info(f"Group draw recorded: {md.group_of}")
    with st.form("group_draw"):
        cols = st.columns(4)
        picks = {}
        for i in range(1, 9):
            with cols[(i - 1) % 4]:
                picks[f"G{i}"] = st.selectbox(
                    f"G{i}", list(team_options), index=i - 1, key=f"g{i}"
                )
        if st.form_submit_button("Record group draw"):
            payload = {"groups": {g: team_options[v] for g, v in picks.items()}}
            if precheck_and_append("group_draw", payload):
                st.success("Group draw recorded.")
                st.rerun()

if not md.group_of:
    st.stop()

nodes_ready = [nid for nid in sorted(md.nodes) if md.node_teams(nid) is not None]

with tab_lineup:
    node = st.selectbox("Matchup", nodes_ready, key="lineup_node")
    t_a, t_b = md.node_teams(node)
    tid = st.radio("Team", [t_a, t_b], format_func=lambda t: team_label(
        md, next(g for g, x in md.group_of.items() if x == t)), horizontal=True)
    members = md.base.teams[tid]
    women = [p for p in members if md.base.players[p].gender == "F"]
    men = [p for p in members if md.base.players[p].gender == "M"]
    label = lambda p: f"#{p} {md.base.players[p].name}"  # noqa: E731
    with st.form("lineup"):
        wd = st.multiselect("WD (2 women)", women, default=women, format_func=label)
        md1 = st.multiselect("MD1", men, format_func=label, max_selections=2)
        md2 = st.multiselect("MD2", men, format_func=label, max_selections=2)
        md3 = st.multiselect("MD3", men, format_func=label, max_selections=2)
        if st.form_submit_button("Submit lineup"):
            payload = {"node": node, "team": tid,
                       "lineup": {"WD": wd, "MD1": md1, "MD2": md2, "MD3": md3}}
            if precheck_and_append("lineup_submit", payload):
                st.success("Lineup recorded.")
                st.rerun()

with tab_blind:
    node = st.selectbox("Matchup", nodes_ready, key="blind_node")
    t_a, t_b = md.node_teams(node)
    tid = st.radio("Team drawn from", [t_a, t_b], format_func=lambda t: team_label(
        md, next(g for g, x in md.group_of.items() if x == t)),
        horizontal=True, key="blind_team")
    members = [p for p in md.base.teams[tid] if not md.base.players[p].is_captain]
    label = lambda p: f"#{p} {md.base.players[p].name} ({md.base.players[p].gender})"  # noqa: E731
    with st.form("blind"):
        picked = st.multiselect("Players drawn", members, format_func=label,
                                max_selections=2)
        if st.form_submit_button("Record blind draw"):
            payload = {"node": node, "team": tid, "players": picked}
            if precheck_and_append("blind_draw_result", payload):
                st.success("Blind draw recorded.")
                st.rerun()

with tab_absence:
    tid = st.selectbox("Team", list(team_options), key="abs_team")
    tid = team_options[tid]
    members = md.base.teams[tid]
    label = lambda p: f"#{p} {md.base.players[p].name}"  # noqa: E731
    c1, c2 = st.columns(2)
    with c1, st.form("absence"):
        absent = st.selectbox("Absent player", members, format_func=label)
        if st.form_submit_button("Register absence (switch team to "
                                 f"{cfg['format']['shorthanded_points']} pts)"):
            if precheck_and_append("absence_registered",
                                   {"team": tid, "absent_id": absent}):
                st.success("Absence registered.")
                st.rerun()
    with c2, st.form("substitute"):
        round_no = st.selectbox("Round", [1, 2, 3])
        sub = st.selectbox("Substitute (captain's pick, must differ per round)",
                           members, format_func=label)
        if st.form_submit_button("Assign substitute"):
            if precheck_and_append(
                "substitute_assigned",
                {"team": tid, "round": round_no, "substitute_id": sub},
            ):
                st.success("Substitute recorded.")
                st.rerun()

st.divider()
st.subheader("Event log (latest 20)")
for e in store.events()[-20:][::-1]:
    st.text(f"[{e.seq}] {e.ts} {e.type} by {e.actor}: {e.payload}")
