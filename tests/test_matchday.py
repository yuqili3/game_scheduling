"""M2 单元测试: 对阵树规则、比赛日状态、时长模型、排程器。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import rules
from core.draw import draw_teams
from core.io_utils import load_config, load_players
from core.matchday import MatchDayState, replay_matchday
from core.models import Event, TournamentState
from core.replay import replay
from core.scheduler import game_minutes, plan, utilization

CFG = load_config()


def base_state() -> TournamentState:
    players = load_players()
    teams = draw_teams(players, 8, seed=1)
    return TournamentState(players=players, teams=teams)


def ev(seq, type_, payload, seed=None):
    return Event(seq, f"2026-07-19T17:{seq:02d}:00", type_, "测试", payload, seed)


def group_draw_event(seq=1):
    return ev(seq, "对阵抽签", {"分组": {f"G{i}": i for i in range(1, 9)}})


def md_with_groups() -> MatchDayState:
    return replay_matchday(base_state(), [group_draw_event()], CFG)


def lineup_events(md, node_id, seq0):
    """为节点双方生成合法名单 + 盲抽事件。"""
    t_a, t_b = md.node_teams(node_id)
    evs = []
    for tid in (t_a, t_b):
        members = md.base.teams[tid]
        women = [p for p in members if md.base.players[p].性别 == "女"]
        men = [p for p in members if md.base.players[p].性别 == "男" and not md.base.players[p].是否队长]
        cap = [p for p in members if md.base.players[p].是否队长]
        evs.append(ev(seq0, "名单提交", {
            "节点": node_id, "队伍": tid,
            "名单": {"女双": women,
                     "男双1": [men[0], men[1]],
                     "男双2": [men[2], men[3]],
                     "男双3": [men[4], cap[0]]},
        }))
        seq0 += 1
        evs.append(ev(seq0, "盲抽结果", {"节点": node_id, "队伍": tid, "队员": [women[0], men[0]]}))
        seq0 += 1
    return evs


def score_events(node_id, seq0, a_wins=True, 比分=(21, 15)):
    """让 a(或 b)直落两局赢下全部 5 场。"""
    evs = []
    for 场次 in rules.MATCH_SLOTS:
        for 局 in (1, 2):
            s = 比分 if a_wins else (比分[1], 比分[0])
            evs.append(ev(seq0, "局结束", {"节点": node_id, "场次": 场次, "局号": 局, "比分": list(s)}))
            seq0 += 1
    return evs


class TestRules(unittest.TestCase):
    def test_bracket_shape(self):
        nodes = rules.bracket()
        self.assertEqual(len(nodes), 12)
        self.assertEqual(sum(n.轮次 == r for n in nodes.values() for r in [1]), 4)

    def test_match_winner(self):
        self.assertEqual(rules.match_winner([(21, 10), (21, 12)]), "a")
        self.assertIsNone(rules.match_winner([(21, 10), (10, 21)]))
        self.assertEqual(rules.match_winner([(21, 10), (10, 21), (15, 21)]), "b")

    def test_full_bracket_ranking(self):
        """G 编号小的一方全胜 → 名次应为 1-5-3-7 逻辑的展开。"""
        nodes = rules.bracket()
        winners = {}
        for nid in sorted(nodes, key=lambda i: (nodes[i].轮次, i)):
            gs = rules.resolve_groups(nodes[nid], winners, nodes)
            winners[nid] = min(gs, key=lambda g: int(g[1:]))  # 小编号恒胜
        ranking = rules.final_ranking(winners, nodes)
        self.assertEqual(ranking[1], "G1")
        self.assertEqual(ranking[2], "G5")
        self.assertEqual(ranking[3], "G3")
        self.assertEqual(ranking[4], "G7")
        self.assertEqual(ranking[8], "G8")


class TestMatchDay(unittest.TestCase):
    def test_group_draw_required_complete(self):
        with self.assertRaises(ValueError):
            replay_matchday(
                base_state(),
                [ev(1, "对阵抽签", {"分组": {"G1": 1}})],
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
        men = [p for p in members if md.base.players[p].性别 == "男" and not md.base.players[p].是否队长]
        women = [p for p in members if md.base.players[p].性别 == "女"]
        bad = ev(2, "名单提交", {
            "节点": "R1-1", "队伍": t_a,
            "名单": {"女双": women,
                     "男双1": [men[0], men[1]],
                     "男双2": [men[0], men[2]],  # men[0] 重复出战
                     "男双3": [men[3], men[4]]},
        })
        with self.assertRaises(ValueError):
            replay_matchday(base_state(), [group_draw_event(), bad], CFG)

    def test_substitute_no_repeat_across_rounds(self):
        md = md_with_groups()
        tid = md.node_teams("R1-1")[0]
        pid = md.base.teams[tid][3]
        evs = [
            group_draw_event(),
            ev(2, "缺席登记", {"队伍": tid, "缺席者序号": md.base.teams[tid][4]}),
            ev(3, "顶替指定", {"队伍": tid, "轮次": 1, "顶替者序号": pid}),
            ev(4, "顶替指定", {"队伍": tid, "轮次": 2, "顶替者序号": pid}),
        ]
        with self.assertRaises(ValueError):
            replay_matchday(base_state(), evs, CFG)

    def test_short_team_uses_15(self):
        md = md_with_groups()
        tid = md.node_teams("R1-1")[0]
        evs = [group_draw_event(), ev(2, "缺席登记", {"队伍": tid, "缺席者序号": md.base.teams[tid][4]})]
        md2 = replay_matchday(base_state(), evs, CFG)
        self.assertEqual(md2.分制("R1-1"), CFG["赛制"]["缺人队伍分数"])
        self.assertEqual(md2.分制("R1-3"), CFG["赛制"]["每局分数"])


class TestDurationModel(unittest.TestCase):
    def _md_after_r1(self, 比分=(21, 15)):
        md = md_with_groups()
        evs = [group_draw_event()]
        seq = 2
        for nid in ("R1-1", "R1-2", "R1-3", "R1-4"):
            les = lineup_events(md, nid, seq)
            seq += len(les)
            evs += les
            ses = score_events(nid, seq, a_wins=True, 比分=比分)
            seq += len(ses)
            evs += ses
        return replay_matchday(base_state(), evs, CFG)

    def test_r1_no_overtime(self):
        md = md_with_groups()
        self.assertAlmostEqual(game_minutes(md, "R1-1", "女双"), 10.0)
        self.assertAlmostEqual(game_minutes(md, "R1-1", "男双1"), 13.0)

    def test_strong_pair_by_margin(self):
        """R1 大胜(21:10, 净胜11≥6/局)的组合在 R2 视为强。"""
        md = self._md_after_r1(比分=(21, 10))
        m = md.matches[("R1-1", "女双")]
        self.assertTrue(md.pair_strong(m.a, 轮次=2))

    def test_weak_loser_pair(self):
        md = self._md_after_r1()
        m = md.matches[("R1-1", "女双")]
        self.assertFalse(md.pair_strong(m.b, 轮次=2))  # b 侧全败

    def test_15_scaling(self):
        md = md_with_groups()
        tid = md.node_teams("R1-1")[0]
        evs = [group_draw_event(), ev(2, "缺席登记", {"队伍": tid, "缺席者序号": md.base.teams[tid][4]})]
        md2 = replay_matchday(base_state(), evs, CFG)
        self.assertAlmostEqual(game_minutes(md2, "R1-1", "男双1"), 13 * 15 / 21)


class TestScheduler(unittest.TestCase):
    def test_plan_from_scratch(self):
        md = md_with_groups()
        slots = plan(md)
        self.assertTrue(slots)
        # 场地不冲突
        by_court = {}
        for s in slots:
            for o in by_court.get(s.场地, []):
                self.assertFalse(s.开始 < o.结束 and o.开始 < s.结束,
                                 f"场地{s.场地}时间重叠: {s} vs {o}")
            by_court.setdefault(s.场地, []).append(s)
        # 半区隔离: R1-1/R1-2 只用 1-5,R1-3/R1-4 只用 6-10
        for s in slots:
            if s.节点 in ("R1-1", "R1-2"):
                self.assertLessEqual(s.场地, 5)
            if s.节点 in ("R1-3", "R1-4"):
                self.assertGreaterEqual(s.场地, 6)

    def test_blind_match_waits_for_first_four(self):
        md = md_with_groups()
        slots = plan(md)
        rest = CFG["时长模型"]["连续出场休息"]
        for nid in ("R1-1", "R1-2", "R1-3", "R1-4"):
            firsts = [s for s in slots if s.节点 == nid and s.场次 != "盲抽"]
            blind = [s for s in slots if s.节点 == nid and s.场次 == "盲抽"]
            self.assertTrue(blind)
            self.assertGreaterEqual(
                min(b.开始 for b in blind),
                max(f.结束 for f in firsts) + rest - 1e-9,
            )

    def test_round_dependency(self):
        md = md_with_groups()
        slots = plan(md)
        r1_end = max(s.结束 for s in slots if s.节点.startswith("R1"))
        r3_start = min(s.开始 for s in slots if s.节点.startswith("R3"))
        r2_start = min(s.开始 for s in slots if s.节点.startswith("R2"))
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
        self.assertFalse([s for s in slots if s.节点 == "R1-1"])

    def test_utilization_positive(self):
        md = md_with_groups()
        slots = plan(md)
        u = utilization(slots, CFG["场地"]["场地总数"])
        self.assertGreater(u, 0.3)
        self.assertLessEqual(u, 1.0)


if __name__ == "__main__":
    unittest.main()
