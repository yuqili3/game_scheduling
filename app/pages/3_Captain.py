"""Captain page: sign in with the team PIN, then per current round:

- submit the WD / MD1-3 lineup for the team's matchup;
- run the blind draw for the OPPOSING team (per tournament rules) by entering
  a random seed — the players are fully determined by the seed among those
  eligible (right gender mix, non-captain, not drawn in earlier rounds);
- register an absence and assign the per-round substitute (must differ across
  the three rounds; the team switches to short-handed scoring).

Lineups lock once the matchup has any recorded score — changes after that go
through the admin desk.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import get_store, load_state, names, team_label  # noqa: E402
from core.draw import blind_draw_pick  # noqa: E402
from core.models import Event  # noqa: E402

st.set_page_config(page_title="Captain", page_icon="🧢", layout="wide")
st.title("🧢 Captain Desk")

cfg, md, slots, now = load_state()
store = get_store()
pins = {int(k): str(v) for k, v in cfg["broadcast"].get("captain_pins", {}).items()}


def team_name(tid: int) -> str:
    return f"Team {tid} — {md.base.players[md.base.teams[tid][0]].name}"


def precheck_and_append(type_: str, payload: dict, actor: str, seed=None) -> bool:
    try:
        md.apply(Event(seq=0, ts="precheck", type=type_, actor=actor,
                       payload=payload, seed=seed))
        store.append(type_, actor, payload, seed=seed)
        return True
    except ValueError as exc:
        st.error(str(exc))
        return False


with st.sidebar:
    tid = st.selectbox("Your team", sorted(md.base.teams), format_func=team_name)
    pin = st.text_input("Team PIN", type="password")

if pin != pins.get(tid):
    st.warning("Enter your team PIN to continue.")
    st.stop()

actor = f"captain-T{tid}"
st.success(f"Signed in: {team_name(tid)}")

if not md.group_of:
    st.info("The group draw has not happened yet.")
    st.stop()

unfinished_rounds = [n.round for n in md.nodes.values() if not md.node_finished(n.id)]
current_round = min(unfinished_rounds) if unfinished_rounds else None
if current_round is None:
    st.success("All rounds are finished 🎉")
    st.stop()

node_id = None
for nid, node in md.nodes.items():
    if node.round == current_round:
        teams = md.node_teams(nid)
        if teams and tid in teams:
            node_id = nid
            break

label = lambda p: f"#{p} {md.base.players[p].name}"  # noqa: E731
members = md.base.teams[tid]

tab_lineup, tab_blind, tab_absence = st.tabs(
    ["Lineup", "Blind draw (seeded)", "Absence / substitute"]
)

if node_id is None:
    st.info(f"Round {current_round}: your opponent is not decided yet — "
            "waiting for the previous round to finish. Absence registration "
            "below is still available.")
else:
    gs = md.node_groups(node_id)
    st.markdown(f"**Round {current_round}: `{node_id}` — "
                f"{team_label(md, gs[0])} vs {team_label(md, gs[1])}**")

side = None
opp_tid = None
if node_id is not None:
    t_a, t_b = md.node_teams(node_id)
    side = "a" if t_a == tid else "b"
    opp_tid = t_b if t_a == tid else t_a
locked = node_id is not None and any(
    m.games for (n, _), m in md.matches.items() if n == node_id
)

with tab_lineup:
    if node_id is None:
        st.info("No matchup yet.")
    else:
        current = {}
        for slot_name in ("WD", "MD1", "MD2", "MD3"):
            m = md.matches.get((node_id, slot_name))
            current[slot_name] = list(getattr(m, side)) if m else []
        if any(current.values()):
            st.markdown("**Submitted:** " + "  ·  ".join(
                f"`{s}` {names(md, current[s])}" for s in ("WD", "MD1", "MD2", "MD3")))
        if locked:
            st.info("Scores are already recorded — the lineup is locked. "
                    "Contact the admin desk for changes.")
        else:
            women = [p for p in members if md.base.players[p].gender == "F"]
            men = [p for p in members if md.base.players[p].gender == "M"]
            with st.form("captain_lineup"):
                wd = st.multiselect("WD (both women play)", women,
                                    default=current["WD"] or women, format_func=label)
                md1 = st.multiselect("MD1", men, default=current["MD1"],
                                     format_func=label, max_selections=2)
                md2 = st.multiselect("MD2", men, default=current["MD2"],
                                     format_func=label, max_selections=2)
                md3 = st.multiselect("MD3 (each man plays at most one MD)", men,
                                     default=current["MD3"], format_func=label,
                                     max_selections=2)
                if st.form_submit_button("Submit lineup"):
                    payload = {"node": node_id, "team": tid,
                               "lineup": {"WD": wd, "MD1": md1, "MD2": md2, "MD3": md3}}
                    if precheck_and_append("lineup_submit", payload, actor):
                        st.success("Lineup submitted.")
                        st.rerun()

with tab_blind:
    if node_id is None:
        st.info("No matchup yet.")
    else:
        blind = md.matches.get((node_id, "BLIND"))
        category = cfg["format"]["blind_match_types"][current_round - 1]
        st.caption(
            f"Per the rules, you draw your OPPONENT's blind-match pair. "
            f"Round {current_round} is **{category}** "
            f"({'1 female + 1 non-captain male' if category == 'XD' else '2 non-captain males'}, "
            "players drawn in earlier rounds are excluded). The pick is fully "
            "determined by the seed you enter — same seed, same result."
        )
        own = list(getattr(blind, side)) if blind else []
        opp_side = "b" if side == "a" else "a"
        opp = list(getattr(blind, opp_side)) if blind else []
        if own:
            st.markdown(f"**Your team's blind pair (drawn by opponent):** {names(md, own)}")
        if opp:
            st.markdown(f"**Opponent's blind pair (you drew):** {names(md, opp)}")
        if locked:
            st.info("Scores are already recorded — blind draw is locked.")
        elif opp:
            st.info("You already drew the opponent's pair. Redraw requires the admin desk.")
        else:
            fem, mal = md.blind_eligible(node_id, opp_tid)
            st.markdown(
                f"Opponent eligible pool — females: {names(md, fem) or 'none'}; "
                f"males: {names(md, mal) or 'none'}"
            )
            with st.form("blind_seed"):
                seed = st.number_input("Random seed (announce it out loud, then enter)",
                                       min_value=0, max_value=10**9, step=1)
                if st.form_submit_button(f"Draw opponent's {category} pair"):
                    try:
                        picked = blind_draw_pick(category, fem, mal, int(seed))
                    except ValueError as exc:
                        st.error(str(exc))
                        picked = None
                    if picked:
                        payload = {"node": node_id, "team": opp_tid, "players": picked}
                        if precheck_and_append("blind_draw_result", payload, actor,
                                               seed=int(seed)):
                            st.success(f"Drawn with seed {int(seed)}: {names(md, picked)}")
                            st.rerun()

with tab_absence:
    st.caption(
        f"Registering an absence switches the whole team to "
        f"{cfg['format']['shorthanded_points']}-point scoring. Each round the "
        "captain assigns a substitute; the three rounds must use three "
        "different substitutes."
    )
    short = md.shorthanded.get(tid)
    if short:
        subs = ", ".join(f"R{r}: {md.base.players[p].name}"
                         for r, p in sorted(short["substitutes"].items())) or "none yet"
        st.markdown(f"**Absent:** {md.base.players[short['absent_id']].name} · "
                    f"**Substitutes:** {subs}")
    c1, c2 = st.columns(2)
    with c1, st.form("cap_absence"):
        absent = st.selectbox("Absent player", members, format_func=label)
        if st.form_submit_button("Register absence"):
            if precheck_and_append("absence_registered",
                                   {"team": tid, "absent_id": absent}, actor):
                st.success("Absence registered.")
                st.rerun()
    with c2, st.form("cap_substitute"):
        round_no = st.selectbox("Round", [1, 2, 3], index=current_round - 1)
        sub = st.selectbox("Substitute", members, format_func=label)
        if st.form_submit_button("Assign substitute"):
            if precheck_and_append(
                "substitute_assigned",
                {"team": tid, "round": round_no, "substitute_id": sub}, actor,
            ):
                st.success("Substitute recorded.")
                st.rerun()
