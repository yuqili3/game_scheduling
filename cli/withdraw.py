#!/usr/bin/env python3
"""退赛重抽: 追加"退赛重抽"事件 → 重放 → 校验 → 导出带日期戳的新快照。

可多次调用;每次必须显式提供随机种子,同种子完全可重现。

用法:
    python3 cli/withdraw.py --withdrawn 33 --substitute "Xin Wang" --seed 777
    python3 cli/withdraw.py --withdrawn 17 --new-captain 25 --substitute "Xin Wang" --seed 778
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
    ap = argparse.ArgumentParser(description="退赛重抽")
    ap.add_argument("--withdrawn", type=int, required=True, help="退赛者序号")
    ap.add_argument("--substitute", required=True, help="候补人员姓名(用户录入)")
    ap.add_argument("--seed", type=int, required=True, help="随机种子(用户输入,记入日志)")
    ap.add_argument("--new-captain", type=int, default=None, help="退赛者为队长时必填: 新队长序号")
    ap.add_argument("--actor", default="主办方")
    ap.add_argument("--date", default=None, help="快照日期 YYYYMMDD,默认今天")
    args = ap.parse_args()

    cfg = io_utils.load_config()
    players = io_utils.load_players()
    events = io_utils.read_events()
    if not any(e.type in ("初始抽签", "指定分队") for e in events):
        raise SystemExit("尚未分队,请先运行 draw_teams.py 或 import_teams.py")

    payload = {"退赛者序号": args.withdrawn, "候补姓名": args.substitute}
    if args.new_captain is not None:
        payload["新队长序号"] = args.new_captain
    ev = Event(
        seq=io_utils.next_seq(),
        ts=datetime.now().isoformat(timespec="seconds"),
        type="退赛重抽",
        actor=args.actor,
        payload=payload,
        seed=args.seed,
    )
    io_utils.append_event(ev)

    state = replay(players, io_utils.read_events(), cfg["人员"]["队伍数量"])
    state.validate(cfg["人员"]["每队女生数"], cfg["人员"]["每队男生数"])

    date_str = args.date or datetime.now().strftime("%Y%m%d")
    out = io_utils.snapshot_path(date_str)
    io_utils.export_teams_csv(state, out)

    print(f"重抽完成 seed={args.seed},快照: {out}")
    print("本次变更:")
    n = len([l for l in state.changelog if l])  # noqa: E741
    for line in state.changelog[-18:]:
        print("  " + line)


if __name__ == "__main__":
    main()
