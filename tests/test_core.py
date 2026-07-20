"""M1 单元测试: 抽签确定性、结构约束、退赛重抽、事件重放。

运行: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.draw import draw_teams, withdraw_redraw
from core.io_utils import load_config, load_players
from core.models import Event, Player, TournamentState
from core.replay import replay

CFG = load_config()
N_TEAMS = CFG["人员"]["队伍数量"]
F_PER = CFG["人员"]["每队女生数"]
M_PER = CFG["人员"]["每队男生数"]


def real_players():
    return load_players()


def make_state(seed=1):
    players = real_players()
    teams = draw_teams(players, N_TEAMS, seed)
    return TournamentState(players=players, teams=teams)


class TestDraw(unittest.TestCase):
    def test_roster_counts(self):
        players = real_players()
        self.assertEqual(len(players), 64)
        self.assertEqual(sum(p.性别 == "女" for p in players.values()), 16)
        self.assertEqual(sum(p.性别 == "男" for p in players.values()), 48)
        self.assertEqual(sum(p.是否队长 for p in players.values()), 8)
        self.assertTrue(all(p.性别 == "男" for p in players.values() if p.是否队长))

    def test_deterministic_same_seed(self):
        players = real_players()
        self.assertEqual(draw_teams(players, N_TEAMS, 42), draw_teams(players, N_TEAMS, 42))

    def test_different_seed_differs(self):
        players = real_players()
        self.assertNotEqual(draw_teams(players, N_TEAMS, 1), draw_teams(players, N_TEAMS, 2))

    def test_structure_valid(self):
        state = make_state(seed=7)
        state.validate(F_PER, M_PER)  # 不抛异常即通过
        self.assertEqual(sum(len(m) for m in state.teams.values()), 64)


class TestWithdraw(unittest.TestCase):
    def test_member_withdraw(self):
        state = make_state(seed=3)
        quitter = next(
            pid for pid, p in state.players.items() if p.性别 == "男" and not p.是否队长
        )
        players, teams, log = withdraw_redraw(state, quitter, "替补甲", seed=99)
        new_state = TournamentState(players=players, teams=teams)
        new_state.validate(F_PER, M_PER)
        self.assertNotIn(quitter, players)
        sub_id = max(players)
        self.assertEqual(players[sub_id].姓名, "替补甲")
        self.assertIsNotNone(new_state.team_of(sub_id))
        self.assertTrue(log)

    def test_female_withdraw_keeps_gender_balance(self):
        state = make_state(seed=3)
        quitter = next(pid for pid, p in state.players.items() if p.性别 == "女")
        players, teams, _ = withdraw_redraw(state, quitter, "替补女", seed=5)
        new_state = TournamentState(players=players, teams=teams)
        new_state.validate(F_PER, M_PER)
        self.assertEqual(players[max(players)].性别, "女")

    def test_captain_withdraw_requires_successor(self):
        state = make_state(seed=3)
        captain = next(pid for pid, p in state.players.items() if p.是否队长)
        with self.assertRaises(ValueError):
            withdraw_redraw(state, captain, "替补乙", seed=1)

    def test_captain_withdraw_with_successor(self):
        state = make_state(seed=3)
        captain = next(pid for pid, p in state.players.items() if p.是否队长)
        tid = state.team_of(captain)
        successor = next(
            m
            for m in state.teams[tid]
            if state.players[m].性别 == "男" and not state.players[m].是否队长
        )
        players, teams, _ = withdraw_redraw(state, captain, "替补丙", seed=11, 新队长序号=successor)
        new_state = TournamentState(players=players, teams=teams)
        new_state.validate(F_PER, M_PER)
        self.assertTrue(players[successor].是否队长)
        self.assertEqual(teams[tid][0], successor)

    def test_deterministic(self):
        state = make_state(seed=3)
        quitter = next(
            pid for pid, p in state.players.items() if p.性别 == "男" and not p.是否队长
        )
        r1 = withdraw_redraw(state, quitter, "替补丁", seed=2024)
        r2 = withdraw_redraw(state, quitter, "替补丁", seed=2024)
        self.assertEqual(r1[1], r2[1])
        r3 = withdraw_redraw(state, quitter, "替补丁", seed=2025)
        self.assertNotEqual(r1[1], r3[1])


class TestReplay(unittest.TestCase):
    def _events(self):
        return [
            Event(1, "2026-06-12T12:00:00", "初始抽签", "主办方", {}, seed=20260612),
            Event(
                2,
                "2026-07-01T09:00:00",
                "退赛重抽",
                "主办方",
                {"退赛者序号": 30, "候补姓名": "替补一"},
                seed=701,
            ),
            Event(
                3,
                "2026-07-10T09:00:00",
                "退赛重抽",
                "主办方",
                {"退赛者序号": 5, "候补姓名": "替补二"},
                seed=710,
            ),
        ]

    def test_replay_reproducible(self):
        players = real_players()
        s1 = replay(players, self._events(), N_TEAMS)
        s2 = replay(players, self._events(), N_TEAMS)
        self.assertEqual(s1.teams, s2.teams)
        self.assertEqual(s1.players, s2.players)
        s1.validate(F_PER, M_PER)

    def test_replay_prefix_is_intermediate_state(self):
        """取事件前缀重放 = 当时的状态(验收标准 3)。"""
        players = real_players()
        mid = replay(players, self._events()[:2], N_TEAMS)
        self.assertNotIn(30, mid.players)
        self.assertIn(5, mid.players)  # 第二次退赛尚未发生
        mid.validate(F_PER, M_PER)

    def test_assigned_teams_event(self):
        players = real_players()
        teams = draw_teams(players, N_TEAMS, 123)
        ev = Event(
            1,
            "2026-06-12T12:00:00",
            "指定分队",
            "主办方",
            {"队伍": {str(t): ms for t, ms in teams.items()}},
        )
        state = replay(players, [ev], N_TEAMS)
        self.assertEqual(state.teams, teams)
        state.validate(F_PER, M_PER)

    def test_unknown_event_rejected(self):
        players = real_players()
        with self.assertRaises(ValueError):
            replay(players, [Event(1, "t", "神秘事件", "x", {})], N_TEAMS)


if __name__ == "__main__":
    unittest.main()
