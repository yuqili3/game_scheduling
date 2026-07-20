"""M2 unit tests: bracket rules, match-day state, duration model, scheduler."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import rules
from core.draw import blind_draw_pick, draw_teams
from core.io_utils import load_config, load_players
from core.matchday import MatchDayState, replay_matchday
from core.models import Event, TournamentState
from core.scheduler import game_minutes, plan, utilization

CFG = load_config()


def base_state() -> TournamentState:
    players = load_players()
    teams = draw_teams(players, 8, seed=1)
    return TournamentState(players=players, teams=teams)


def ev(seq, type_, payload, seed=None):
    return Event(seq, f"2026-07-19T17:{seq:02d}:00", type_, "test", payload, seed)


def group_draw_event(seq=1):
    return ev(seq, "group_draw", {"groups": {f"G{i}": i for i in range(1, 9)}})


def md_with_groups() -> MatchDayState:
    return replay_matchday(base_state(), [group_draw_event()], CFG)


def lineup_events(md, node_id, seq0):
    """Generate valid lineup + blind-draw events for both sides of a node."""
    t_a, t_b = md.node_teams(node_id)
    evs = []
    for tid in (t_a, t_b):
        members = md.base.teams[tid]
        women = [p for p in members if md.base.players[p].gender == "F"]
        men = [p for p in members
               if md.base.players[p].gender == "M" and not md.base.players[p].is_captain]
        cap = [p for p in members if md.base.players[p].is_captain]
        evs.append(ev(seq0, "lineup_submit", {
            "node": node_id, "team": tid,
            "lineup": {"WD": women,
                       "MD1": [men[0], men[1]],
                       "MD2": [men[2], men[3]],
                       "MD3": [men[4], cap[0]]},
        }))
        seq0 += 1
        evs.append(ev(seq0, "blind_draw_result",
                      {"node": node_id, "team": tid, "players": [women[0], men[0]]}))
        seq0 += 1
    return evs


def score_events(node_id, seq0, a_wins=True, score=(21, 15)):
    """Side a (or b) wins every match in straight games."""
    evs = []
    for slot in rules.MATCH_SLOTS:
        for game in (1, 2):
            s = score if a_wins else (score[1], score[0])
            evs.append(ev(seq0, "game_finished",
                          {"node": node_id, "slot": slot, "game": game, "score": list(s)}))
            seq0 += 1
    return evs


class TestRules(unittest.TestCase):
    def test_bracket_shape(self):
        nodes = rules.bracket()
        self.assertEqual(len(nodes), 12)
        self.assertEqual(sum(1 for n in nodes.values() if n.round == 1), 4)

    def test_match_winner(self):
        self.assertEqual(rules.match_winner([(21, 10), (21, 12)]), "a")
        self.assertIsNone(rules.match_winner([(21, 10), (10, 21)]))
        self.assertEqual(rules.match_winner([(21, 10), (10, 21), (15, 21)]), "b")

    def test_full_bracket_ranking(self):
        """Lower G label always wins -> ranking follows the 1-5-3-7 pattern."""
        nodes = rules.bracket()
        winners = {}
        for nid in sorted(nodes, key=lambda i: (nodes[i].round, i)):
            gs = rules.resolve_groups(nodes[nid], winners, nodes)
            winners[nid] = min(gs, key=lambda g: int(g[1:]))
        ranking = rules.final_ranking(winners, nodes)
        self.assertEqual(ranking[1], "G1")
        self.assertEqual(ranking[2], "G5")
        self.assertEqual(ranking[3], "G3")
        self.assertEqual(ranking[4], "G7")
        self.assertEqual(ranking[8], "G8")


class TestMatchDay(unittest.TestCase):
    def test_group_draw_must_be_complete(self):
        with self.assertRaises(ValueError):
            replay_matchday(
                base_state(),
                [ev(1, "group_draw", {"groups": {"G1": 1}})],
                CFG,
            )

    def test_lineup_and_scores_decide_node(self):
        md = md_with_groups()
        evs = [group_draw_event()] + lineup_events(md, "R1-1", 2) + score_events("R1-1", 10)
        md2 = replay_matchday(base_state(), evs, CFG)
        winners = md2.node_winners()
        self.assertEqual(winners.get("R1-1"), "G1")
        self.assertTrue(md2.node_finished("R1-1"))

    def test_male_cannot_play_two_md(self):
        md = md_with_groups()
        t_a, _ = md.node_teams("R1-1")
        members = md.base.teams[t_a]
        men = [p for p in members
               if md.base.players[p].gender == "M" and not md.base.players[p].is_captain]
        women = [p for p in members if md.base.players[p].gender == "F"]
        bad = ev(2, "lineup_submit", {
            "node": "R1-1", "team": t_a,
            "lineup": {"WD": women,
                       "MD1": [men[0], men[1]],
                       "MD2": [men[0], men[2]],  # men[0] fielded twice
                       "MD3": [men[3], men[4]]},
        })
        with self.assertRaises(ValueError):
            replay_matchday(base_state(), [group_draw_event(), bad], CFG)

    def test_substitute_no_repeat_across_rounds(self):
        md = md_with_groups()
        tid = md.node_teams("R1-1")[0]
        pid = md.base.teams[tid][3]
        evs = [
            group_draw_event(),
            ev(2, "absence_registered", {"team": tid, "absent_id": md.base.teams[tid][4]}),
            ev(3, "substitute_assigned", {"team": tid, "round": 1, "substitute_id": pid}),
            ev(4, "substitute_assigned", {"team": tid, "round": 2, "substitute_id": pid}),
        ]
        with self.assertRaises(ValueError):
            replay_matchday(base_state(), evs, CFG)

    def test_short_team_uses_reduced_points(self):
        md = md_with_groups()
        tid = md.node_teams("R1-1")[0]
        evs = [group_draw_event(),
               ev(2, "absence_registered", {"team": tid, "absent_id": md.base.teams[tid][4]})]
        md2 = replay_matchday(base_state(), evs, CFG)
        self.assertEqual(md2.points_for("R1-1"), CFG["format"]["shorthanded_points"])
        self.assertEqual(md2.points_for("R1-3"), CFG["format"]["points_per_game"])


class TestBlindDraw(unittest.TestCase):
    def _md_after_r1(self):
        md = md_with_groups()
        evs = [group_draw_event()]
        seq = 2
        for nid in ("R1-1", "R1-2", "R1-3", "R1-4"):
            les = lineup_events(md, nid, seq)
            seq += len(les)
            evs += les
            ses = score_events(nid, seq)
            seq += len(ses)
            evs += ses
        return replay_matchday(base_state(), evs, CFG)

    def test_seeded_pick_deterministic_and_valid(self):
        md = md_with_groups()
        fem, mal = md.blind_eligible("R1-1", 1)
        p1 = blind_draw_pick("XD", fem, mal, seed=42)
        p2 = blind_draw_pick("XD", fem, mal, seed=42)
        self.assertEqual(p1, p2)
        genders = sorted(md.base.players[p].gender for p in p1)
        self.assertEqual(genders, ["F", "M"])
        self.assertNotEqual(p1, blind_draw_pick("XD", fem, mal, seed=43))
        md_pick = blind_draw_pick("MD", fem, mal, seed=7)
        self.assertEqual(len(set(md_pick)), 2)
        self.assertTrue(all(md.base.players[p].gender == "M" for p in md_pick))

    def test_eligibility_excludes_prior_round_picks(self):
        md = self._md_after_r1()
        # R1-1 blind for team 1 used women[0] and men[0] (see lineup_events)
        used = set(md.matches[("R1-1", "BLIND")].a)
        fem, mal = md.blind_eligible("R2-WU", 1)
        self.assertFalse(used & set(fem) or used & set(mal))

    def test_event_rejects_prior_round_repeat(self):
        md = self._md_after_r1()
        repeat = md.matches[("R1-1", "BLIND")].a  # team 1's round-1 pair
        with self.assertRaises(ValueError):
            md.apply(ev(99, "blind_draw_result",
                        {"node": "R2-WU", "team": 1, "players": repeat}))


class TestCorrection(unittest.TestCase):
    def _md_with_games(self, games):
        md = md_with_groups()
        evs = [group_draw_event()] + lineup_events(md, "R1-1", 2)
        seq = 10
        for i, score in enumerate(games, start=1):
            evs.append(ev(seq, "game_finished",
                          {"node": "R1-1", "slot": "WD", "game": i, "score": list(score)}))
            seq += 1
        return replay_matchday(base_state(), evs, CFG), seq

    def test_correction_replaces_score(self):
        md, seq = self._md_with_games([(21, 15)])
        md.apply(ev(seq, "game_corrected",
                    {"node": "R1-1", "slot": "WD", "game": 1, "score": [15, 21]}))
        self.assertEqual(md.matches[("R1-1", "WD")].games, [(15, 21)])

    def test_correction_drops_games_after_decision(self):
        """21:15 / 10:21 / 21:10 = a wins 2:1; correcting game 2 to 21:10 makes
        the match decided after two games, so game 3 is dropped."""
        md, seq = self._md_with_games([(21, 15), (10, 21), (21, 10)])
        md.apply(ev(seq, "game_corrected",
                    {"node": "R1-1", "slot": "WD", "game": 2, "score": [21, 10]}))
        m = md.matches[("R1-1", "WD")]
        self.assertEqual(m.games, [(21, 15), (21, 10)])
        self.assertEqual(m.winner(), "a")

    def test_correction_rejects_missing_game(self):
        md, seq = self._md_with_games([(21, 15)])
        with self.assertRaises(ValueError):
            md.apply(ev(seq, "game_corrected",
                        {"node": "R1-1", "slot": "WD", "game": 2, "score": [21, 5]}))

    def test_correction_rejects_tie(self):
        md, seq = self._md_with_games([(21, 15)])
        with self.assertRaises(ValueError):
            md.apply(ev(seq, "game_corrected",
                        {"node": "R1-1", "slot": "WD", "game": 1, "score": [21, 21]}))


class TestDurationModel(unittest.TestCase):
    def _md_after_r1(self, score=(21, 15)):
        md = md_with_groups()
        evs = [group_draw_event()]
        seq = 2
        for nid in ("R1-1", "R1-2", "R1-3", "R1-4"):
            les = lineup_events(md, nid, seq)
            seq += len(les)
            evs += les
            ses = score_events(nid, seq, a_wins=True, score=score)
            seq += len(ses)
            evs += ses
        return replay_matchday(base_state(), evs, CFG)

    def test_r1_no_overtime(self):
        md = md_with_groups()
        self.assertAlmostEqual(game_minutes(md, "R1-1", "WD"), 10.0)
        self.assertAlmostEqual(game_minutes(md, "R1-1", "MD1"), 13.0)

    def test_strong_pair_by_margin(self):
        """A pair that won 21:10 in R1 (margin 11 >= 6/game) is strong in R2."""
        md = self._md_after_r1(score=(21, 10))
        m = md.matches[("R1-1", "WD")]
        self.assertTrue(md.pair_strong(m.a, round_no=2))

    def test_weak_loser_pair(self):
        md = self._md_after_r1()
        m = md.matches[("R1-1", "WD")]
        self.assertFalse(md.pair_strong(m.b, round_no=2))  # side b lost everything

    def test_shorthanded_scaling(self):
        md = md_with_groups()
        tid = md.node_teams("R1-1")[0]
        evs = [group_draw_event(),
               ev(2, "absence_registered", {"team": tid, "absent_id": md.base.teams[tid][4]})]
        md2 = replay_matchday(base_state(), evs, CFG)
        self.assertAlmostEqual(game_minutes(md2, "R1-1", "MD1"), 13 * 15 / 21)


class TestScheduler(unittest.TestCase):
    def test_plan_from_scratch(self):
        md = md_with_groups()
        slots = plan(md)
        self.assertTrue(slots)
        # no court double-booking
        by_court = {}
        for s in slots:
            for o in by_court.get(s.court, []):
                self.assertFalse(s.start < o.end and o.start < s.end,
                                 f"court {s.court} overlap: {s} vs {o}")
            by_court.setdefault(s.court, []).append(s)
        # bank isolation: R1-1/R1-2 on courts 1-5, R1-3/R1-4 on 6-10
        for s in slots:
            if s.node in ("R1-1", "R1-2"):
                self.assertLessEqual(s.court, 5)
            if s.node in ("R1-3", "R1-4"):
                self.assertGreaterEqual(s.court, 6)

    def test_blind_waits_for_main_games_not_thirds(self):
        """Blind starts after the first-four matches' games 1-2 (+ rest), and may
        run in parallel with deferred conditional third games (template rule)."""
        md = md_with_groups()
        slots = plan(md)
        rest = CFG["duration"]["rest_minutes"]
        for nid in ("R1-1", "R1-2", "R1-3", "R1-4"):
            mains = [s for s in slots
                     if s.node == nid and s.match_slot != "BLIND" and s.game <= 2]
            thirds = [s for s in slots
                      if s.node == nid and s.match_slot != "BLIND" and s.game == 3]
            blind = [s for s in slots if s.node == nid and s.match_slot == "BLIND"]
            self.assertTrue(blind)
            blind_start = min(b.start for b in blind)
            self.assertGreaterEqual(blind_start, max(f.end for f in mains) + rest - 1e-9)
            # utilization win: blind does NOT wait for the conditional thirds
            self.assertLess(blind_start, max(t.end for t in thirds))

    def test_round_dependency(self):
        md = md_with_groups()
        slots = plan(md)
        r1_end = max(s.end for s in slots if s.node.startswith("R1"))
        r3_start = min(s.start for s in slots if s.node.startswith("R3"))
        r2_start = min(s.start for s in slots if s.node.startswith("R2"))
        self.assertGreater(r2_start, 0)
        self.assertGreaterEqual(r3_start, r2_start)
        self.assertGreater(r1_end, 0)

    def test_deterministic(self):
        md = md_with_groups()
        self.assertEqual(plan(md), plan(md))

    def test_finished_matches_not_replanned(self):
        md = md_with_groups()
        evs = [group_draw_event()] + lineup_events(md, "R1-1", 2) + score_events("R1-1", 10)
        md2 = replay_matchday(base_state(), evs, CFG)
        slots = plan(md2, now=40)
        self.assertFalse([s for s in slots if s.node == "R1-1"])

    def test_utilization_positive(self):
        md = md_with_groups()
        slots = plan(md)
        u = utilization(slots, CFG["courts"]["total"])
        self.assertGreater(u, 0.3)
        self.assertLessEqual(u, 1.0)


if __name__ == "__main__":
    unittest.main()
