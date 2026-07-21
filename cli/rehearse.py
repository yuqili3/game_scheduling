#!/usr/bin/env python3
"""Accelerated match-day rehearsal driver.

Feeds a full simulated tournament into the event store step by step so the
live Streamlit app can be watched while it happens: group draw, per-round
lineups and seeded blind draws, then every game score at a fixed interval.

Run it against a COPY of the repo (it writes real events):

    streamlit run app/Home.py            # terminal 1, in the copy
    python3 cli/rehearse.py --interval 3 # terminal 2, same copy

Options:
    --seed N        master seed for the whole rehearsal (default 20260719)
    --interval S    seconds between games (default 3.0; 0 = instant)
    --keep-clock    do NOT rewrite broadcast.session_start to "now" (by
                    default it is rewritten so the live court map lines up
                    with the rehearsal wall clock)
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import io_utils, rules
from core.draw import blind_draw_pick, group_draw_assign
from core.eventstore import EventStore
from core.matchday import replay_matchday
from core.replay import PRE_MATCH_EVENTS, replay
from core.scheduler import plan


def rebuild(store, players, cfg):
    events = store.events()
    base = replay(players, [e for e in events if e.type in PRE_MATCH_EVENTS],
                  cfg["players"]["num_teams"])
    return base, replay_matchday(base, events, cfg)


def volunteer_for_court(cfg, court):
    for v in cfg["broadcast"]["volunteers"]:
        if court in v["courts"]:
            return v["name"]
    return "VolunteerA"


def main() -> None:
    ap = argparse.ArgumentParser(description="accelerated match-day rehearsal")
    ap.add_argument("--seed", type=int, default=20260719)
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--keep-clock", action="store_true")
    ap.add_argument("--env", default="sim",
                    help="data environment under data/ (default: sim)")
    ap.add_argument("--force-prod", action="store_true",
                    help="required to run the rehearsal against the prod environment")
    args = ap.parse_args()

    io_utils.set_env(args.env)
    if io_utils.ENV == "prod" and not args.force_prod:
        raise SystemExit("refusing to rehearse in the prod environment "
                         "(pass --force-prod if you really mean it)")
    print(f"environment: {io_utils.ENV} ({io_utils.data_dir()})")

    rng = random.Random(args.seed)
    cfg_path = io_utils.config_path()
    if not args.keep_clock:
        text = cfg_path.read_text(encoding="utf-8")
        now_hm = datetime.now().strftime("%H:%M")
        import re

        text = re.sub(r'session_start: "\d{2}:\d{2}"',
                      f'session_start: "{now_hm}"', text)
        cfg_path.write_text(text, encoding="utf-8")
        print(f"session_start rewritten to {now_hm} so the live map matches the clock")

    cfg = io_utils.load_config()
    players = io_utils.load_players()
    store = EventStore()
    start = time.time()

    def pause(msg):
        print(f"[{time.time() - start:6.0f}s] {msg}")
        if args.interval:
            time.sleep(args.interval)

    base, md = rebuild(store, players, cfg)
    if not md.group_of:
        groups = group_draw_assign(sorted(base.teams), args.seed)
        store.append("group_draw", "admin", {"groups": groups}, seed=args.seed)
        pause(f"group draw (seed {args.seed}): {groups}")

    for round_no in (1, 2, 3):
        # lineups + seeded blind draws for every resolved node of the round
        base, md = rebuild(store, players, cfg)
        for nid in sorted(n for n, node in md.nodes.items() if node.round == round_no):
            teams = md.node_teams(nid)
            if teams is None:
                continue
            for tid, opp in ((teams[0], teams[1]), (teams[1], teams[0])):
                if not md.lineup_status(nid, tid)["submitted"]:
                    members = base.teams[tid]
                    women = [p for p in members if players[p].gender == "F"]
                    men = [p for p in members
                           if players[p].gender == "M" and not players[p].is_captain]
                    cap = [p for p in members if players[p].is_captain]
                    rng.shuffle(men)
                    store.append("lineup_submit", f"captain-T{tid}", {
                        "node": nid, "team": tid,
                        "lineup": {"WD": women, "MD1": men[:2], "MD2": men[2:4],
                                   "MD3": [men[4], cap[0]]}})
                    pause(f"round {round_no} {nid}: team {tid} lineup submitted")
                blind = md.matches.get((nid, "BLIND"))
                opp_side = "b" if md.node_teams(nid)[0] == opp else "a"
                if not (blind and getattr(blind, "a" if opp_side == "a" else "b")):
                    category = cfg["format"]["blind_match_types"][round_no - 1]
                    fem, mal = md.blind_eligible(nid, opp)
                    draw_seed = rng.randrange(10**6)
                    picked = blind_draw_pick(category, fem, mal, draw_seed)
                    store.append("blind_draw_result", f"captain-T{tid}",
                                 {"node": nid, "team": opp, "players": picked},
                                 seed=draw_seed)
                    base, md = rebuild(store, players, cfg)
                    pause(f"round {round_no} {nid}: captain T{tid} drew T{opp}'s "
                          f"blind pair (seed {draw_seed})")

        # play every game of the round
        playing = True
        while playing:
            base, md = rebuild(store, players, cfg)
            playing = False
            for nid in sorted(n for n, node in md.nodes.items()
                              if node.round == round_no):
                if md.node_teams(nid) is None or md.node_finished(nid):
                    continue
                for slot in rules.MATCH_SLOTS:
                    m = md.matches.get((nid, slot))
                    if m is None or m.winner() is not None:
                        continue
                    playing = True
                    points = md.points_for(nid)
                    loser_pts = rng.randrange(points // 3, points - 1)
                    a_wins = rng.random() < 0.5
                    score = [points, loser_pts] if a_wins else [loser_pts, points]
                    game_no = len(m.games) + 1
                    elapsed = (time.time() - start) / 60.0
                    court = None
                    for s in plan(md, now=elapsed):
                        if s.node == nid and s.match_slot == slot and s.game == game_no:
                            court = s.court
                            break
                    payload = {"node": nid, "slot": slot, "game": game_no,
                               "score": score}
                    if court:
                        payload["court"] = court
                    actor = volunteer_for_court(cfg, court) if court else "VolunteerA"
                    store.append("game_finished", actor, payload)
                    pause(f"round {round_no} {nid} {slot} game {game_no}: "
                          f"{score[0]}:{score[1]} (court {court})")
                    break  # rebuild state, move to the next pending game
                if playing:
                    break
        print(f"=== round {round_no} finished ===")

    base, md = rebuild(store, players, cfg)
    ranking = rules.final_ranking(md.node_winners(), md.nodes)
    print("final ranking:")
    for place in sorted(ranking):
        g = ranking[place]
        tid = md.group_of[g]
        captain = base.players[base.teams[tid][0]].name
        print(f"  #{place}: {g} (team {tid}, captain {captain})")


if __name__ == "__main__":
    main()
