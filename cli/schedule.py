#!/usr/bin/env python3
"""比赛日排程查看: 重放事件 → 输出场地×时间计划表、对阵树状态、实时排名。

用法:
    python3 cli/schedule.py [--now 45]   # now = 距开赛的分钟数,默认 0
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import io_utils, rules
from core.matchday import replay_matchday
from core.replay import replay
from core.scheduler import plan, utilization


def fmt_t(minutes: float, start="17:10") -> str:
    h, m = int(start[:2]), int(start[3:])
    total = h * 60 + m + int(round(minutes))
    return f"{total // 60:02d}:{total % 60:02d}"


def main() -> None:
    ap = argparse.ArgumentParser(description="比赛日排程")
    ap.add_argument("--now", type=float, default=0.0, help="距开赛分钟数")
    args = ap.parse_args()

    cfg = io_utils.load_config()
    players = io_utils.load_players()
    events = io_utils.read_events()
    base = replay(players, [e for e in events if e.type in ("初始抽签", "指定分队", "退赛重抽")],
                  cfg["人员"]["队伍数量"])
    md = replay_matchday(base, events, cfg)

    if not md.group_of:
        print("尚未进行对阵抽签(缺少 对阵抽签 事件),以下按 G 编号规划:")

    slots = plan(md, now=args.now)
    print(f"\n=== 排程表(共 {len(slots)} 个时段,now={args.now}min) ===")
    print(f"{'开始':>6} {'结束':>6} {'场地':>4}  {'节点':<7} {'场次':<5} 局  说明")
    for s in slots:
        print(
            f"{fmt_t(s.开始):>6} {fmt_t(s.结束):>6} {s.场地:>4}  "
            f"{s.节点:<7} {s.场次:<5} {s.局号}  {s.说明}"
        )
    print(f"\n场地利用率: {utilization(slots, cfg['场地']['场地总数'], args.now):.1%}")

    winners = md.node_winners()
    print("\n=== 对阵树 ===")
    for nid in sorted(md.nodes, key=lambda i: (md.nodes[i].轮次, i)):
        gs = md.node_groups(nid)
        teams = md.node_teams(nid)
        desc = f"{gs[0]} vs {gs[1]}" if gs else "待定"
        if teams:
            desc += f"(队伍{teams[0]} vs 队伍{teams[1]})"
        state = f"胜方 {winners[nid]}" if nid in winners else (
            "已结束" if md.node_finished(nid) else "未结束")
        print(f"  {nid:<7} [{md.nodes[nid].tag}] {desc:<30} {state}")

    ranking = rules.final_ranking(winners, md.nodes)
    if ranking:
        print("\n=== 当前名次 ===")
        for rank in sorted(ranking):
            g = ranking[rank]
            tid = md.group_of.get(g)
            print(f"  第{rank}名: {g}" + (f"(队伍{tid})" if tid else ""))


if __name__ == "__main__":
    main()
