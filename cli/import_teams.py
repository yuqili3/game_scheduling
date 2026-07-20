#!/usr/bin/env python3
"""导入线下已公布的分队结果(如 PDF 名单): 追加"指定分队"事件。

读取仓库根目录 players.csv(列: team_id,team_captain,name,role,gender),
按姓名匹配初始人员表得到序号,写入事件日志并导出快照。

用法:
    python3 cli/import_teams.py [--roster players.csv] [--actor 主办方] [--date 20260612]
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import io_utils
from core.models import Event
from core.replay import replay


def main() -> None:
    ap = argparse.ArgumentParser(description="导入线下分队结果")
    ap.add_argument("--roster", default=str(io_utils.REPO_ROOT / "players.csv"))
    ap.add_argument("--actor", default="主办方")
    ap.add_argument("--date", default=None)
    args = ap.parse_args()

    cfg = io_utils.load_config()
    players = io_utils.load_players()
    by_name = {p.姓名: p.序号 for p in players.values()}
    if len(by_name) != len(players):
        raise SystemExit("人员表存在重名,无法按姓名导入")

    teams: dict = {}
    with open(args.roster, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = row["name"].strip()
            if name not in by_name:
                raise SystemExit(f"名单中的 {name} 不在初始人员表里")
            tid = int(row["team_id"])
            pid = by_name[name]
            # 队长放首位
            if row["role"].strip() == "captain":
                teams.setdefault(tid, []).insert(0, pid)
            else:
                teams.setdefault(tid, []).append(pid)

    ev = Event(
        seq=io_utils.next_seq(),
        ts=datetime.now().isoformat(timespec="seconds"),
        type="指定分队",
        actor=args.actor,
        payload={"队伍": {str(t): ms for t, ms in sorted(teams.items())}},
        seed=None,
    )
    io_utils.append_event(ev)

    state = replay(players, io_utils.read_events(), cfg["人员"]["队伍数量"])
    state.validate(cfg["人员"]["每队女生数"], cfg["人员"]["每队男生数"])

    date_str = args.date or datetime.now().strftime("%Y%m%d")
    out = io_utils.snapshot_path(date_str)
    io_utils.export_teams_csv(state, out)
    print(f"导入完成,共 {len(teams)} 队,快照: {out}")


if __name__ == "__main__":
    main()
