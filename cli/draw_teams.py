#!/usr/bin/env python3
"""初始抽签分队: 追加"初始抽签"事件 → 重放 → 校验 → 导出快照。

用法:
    python3 cli/draw_teams.py --seed 20260719 [--actor 主办方] [--date 20260612]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import io_utils
from core.models import Event
from core.replay import replay


def main() -> None:
    ap = argparse.ArgumentParser(description="初始抽签分队")
    ap.add_argument("--seed", type=int, required=True, help="随机种子(记入事件日志,可复现)")
    ap.add_argument("--actor", default="主办方")
    ap.add_argument("--date", default=None, help="快照日期 YYYYMMDD,默认今天")
    args = ap.parse_args()

    cfg = io_utils.load_config()
    players = io_utils.load_players()
    events = io_utils.read_events()
    if any(e.type in ("初始抽签", "指定分队") for e in events):
        print("警告: 事件日志中已有分队事件,本次抽签将覆盖之前的分队结果")

    ev = Event(
        seq=io_utils.next_seq(),
        ts=datetime.now().isoformat(timespec="seconds"),
        type="初始抽签",
        actor=args.actor,
        payload={},
        seed=args.seed,
    )
    io_utils.append_event(ev)

    state = replay(players, io_utils.read_events(), cfg["人员"]["队伍数量"])
    state.validate(cfg["人员"]["每队女生数"], cfg["人员"]["每队男生数"])

    date_str = args.date or datetime.now().strftime("%Y%m%d")
    out = io_utils.snapshot_path(date_str)
    io_utils.export_teams_csv(state, out)

    print(f"抽签完成 seed={args.seed},快照: {out}")
    for tid in sorted(state.teams):
        names = [state.players[m].姓名 for m in state.teams[tid]]
        print(f"  队伍{tid}: 队长 {names[0]} | " + ", ".join(names[1:]))


if __name__ == "__main__":
    main()
