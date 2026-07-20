"""比赛日状态: 由事件重放构建。纯函数(除 dataclass 可变容器外无副作用)。

处理事件类型: 对阵抽签 / 名单提交 / 盲抽结果 / 局结束 / 缺席登记 / 顶替指定。
赛前事件(初始抽签/指定分队/退赛重抽)由 core.replay 处理,此模块跳过。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import rules
from .models import Event, TournamentState

PRE_MATCH_EVENTS = ("初始抽签", "指定分队", "退赛重抽")


@dataclass
class Match:
    场次: str  # 女双/男双1/男双2/男双3/盲抽
    类别: str  # 女双/男双/混双
    a: List[int] = field(default_factory=list)  # a 侧选手序号
    b: List[int] = field(default_factory=list)
    局分: List[Tuple[int, int]] = field(default_factory=list)

    def 胜方(self, 需胜局: int = 2) -> Optional[str]:
        return rules.match_winner(self.局分, 需胜局)


@dataclass
class MatchDayState:
    base: TournamentState  # 赛前重放结果(最终队伍名单)
    cfg: dict
    group_of: Dict[str, int] = field(default_factory=dict)  # "G1" -> 队伍编号
    nodes: Dict[str, rules.Node] = field(default_factory=rules.bracket)
    # (node_id, 场次) -> Match
    matches: Dict[Tuple[str, str], Match] = field(default_factory=dict)
    # 缺人登记: 队伍编号 -> {"缺席者": pid, "顶替": {轮次: pid}}
    缺人: Dict[int, dict] = field(default_factory=dict)
    changelog: List[str] = field(default_factory=list)

    # ---------- 查询 ----------

    def node_groups(self, node_id: str) -> Optional[Tuple[str, str]]:
        """节点两侧的 G 编号;上游未决出返回 None。"""
        return rules.resolve_groups(self.nodes[node_id], self.node_winners(), self.nodes)

    def node_teams(self, node_id: str) -> Optional[Tuple[int, int]]:
        gs = self.node_groups(node_id)
        if gs is None or not self.group_of:
            return None
        return self.group_of[gs[0]], self.group_of[gs[1]]

    def node_winners(self) -> Dict[str, str]:
        """已决出胜负的节点: node_id -> 胜方 G 编号。"""
        winners: Dict[str, str] = {}
        需胜场 = self.cfg["赛制"]["获胜所需场次"]
        # 按轮次顺序解析,保证上游先于下游
        for nid in sorted(self.nodes, key=lambda i: self.nodes[i].轮次):
            局分表 = {
                场次: m.局分
                for (n, 场次), m in self.matches.items()
                if n == nid and m.局分
            }
            if not 局分表:
                continue
            w = rules.matchup_winner(局分表, 需胜场)
            if w is None:
                continue
            gs = rules.resolve_groups(self.nodes[nid], winners, self.nodes)
            if gs is not None:
                winners[nid] = gs[0] if w == "a" else gs[1]
        return winners

    def node_finished(self, node_id: str) -> bool:
        """5 场都决出即对抗结束(即便中途已锁定胜负也打满)。"""
        done = 0
        for 场次 in rules.MATCH_SLOTS:
            m = self.matches.get((node_id, 场次))
            if m and m.胜方(self.cfg["赛制"]["每场局数"] // 2 + 1) is not None:
                done += 1
        return done == len(rules.MATCH_SLOTS)

    def 分制(self, node_id: str) -> int:
        """该对抗采用的每局分数: 任一侧队伍缺人则整场对抗用缺人分制。"""
        teams = self.node_teams(node_id)
        if teams and any(t in self.缺人 for t in teams):
            return self.cfg["赛制"]["缺人队伍分数"]
        return self.cfg["赛制"]["每局分数"]

    def player_round_results(self, pid: int, 轮次: int) -> List[dict]:
        """某选手在某轮的全部对局: [{"胜": bool, "总净胜": int, "局数": int, "搭档": [...]}]"""
        out = []
        for (nid, 场次), m in self.matches.items():
            if self.nodes[nid].轮次 != 轮次 or not m.局分:
                continue
            side = "a" if pid in m.a else ("b" if pid in m.b else None)
            if side is None:
                continue
            w = m.胜方()
            if w is None:
                continue
            sign = 1 if side == "a" else -1
            margin = sum(sign * (g[0] - g[1]) for g in m.局分)
            out.append(
                {
                    "胜": w == side,
                    "总净胜": margin,
                    "局数": len(m.局分),
                    "搭档": list(m.a if side == "a" else m.b),
                }
            )
        return out

    def pair_strong(self, pids: List[int], 轮次: int) -> bool:
        """组合是否为"强"(用于强强对话加时判定)。第一轮一律不强。

        (a) 两人上一轮各自的对局全部获胜;或
        (b) 两人上一轮曾搭档且该场大比分获胜:
            平均每局净胜 ≥ 每局净胜阈值,或总净胜 ≥ 每场净胜阈值。
        """
        if 轮次 <= 1 or len(pids) < 2:
            return False
        prev = 轮次 - 1
        t = self.cfg["时长模型"]
        recs = {p: self.player_round_results(p, prev) for p in pids}
        # (a) 全胜
        if all(r and all(x["胜"] for x in r) for r in recs.values()):
            return True
        # (b) 搭档大胜
        for r in recs[pids[0]]:
            if set(pids) <= set(r["搭档"]) and r["胜"]:
                if (
                    r["总净胜"] / max(r["局数"], 1) >= t["每局净胜阈值"]
                    or r["总净胜"] >= t["每场净胜阈值"]
                ):
                    return True
        return False

    # ---------- 事件应用 ----------

    def apply(self, ev: Event) -> None:
        handler = {
            "对阵抽签": self._对阵抽签,
            "名单提交": self._名单提交,
            "盲抽结果": self._盲抽结果,
            "局结束": self._局结束,
            "缺席登记": self._缺席登记,
            "顶替指定": self._顶替指定,
        }.get(ev.type)
        if handler is None:
            raise ValueError(f"未知比赛日事件: {ev.type} (seq={ev.seq})")
        handler(ev)

    def _match(self, node_id: str, 场次: str) -> Match:
        key = (node_id, 场次)
        if key not in self.matches:
            if 场次 == "盲抽":
                轮次 = self.nodes[node_id].轮次
                类别 = self.cfg["赛制"]["盲抽局类型"][轮次 - 1]
            else:
                类别 = "女双" if 场次 == "女双" else "男双"
            self.matches[key] = Match(场次=场次, 类别=类别)
        return self.matches[key]

    def _side_of_team(self, node_id: str, tid: int) -> str:
        teams = self.node_teams(node_id)
        if teams is None:
            raise ValueError(f"{node_id} 双方队伍尚未确定")
        if tid == teams[0]:
            return "a"
        if tid == teams[1]:
            return "b"
        raise ValueError(f"队伍{tid}不在 {node_id} 中")

    def _对阵抽签(self, ev: Event) -> None:
        分组 = {g: int(t) for g, t in ev.payload["分组"].items()}
        if sorted(分组) != [f"G{i}" for i in range(1, 9)] or sorted(
            分组.values()
        ) != sorted(self.base.teams):
            raise ValueError("对阵抽签必须覆盖 G1-G8 与全部 8 支队伍")
        self.group_of = 分组
        self.changelog.append(f"[{ev.ts}] 对阵抽签: {分组}")

    def _名单提交(self, ev: Event) -> None:
        p = ev.payload
        node_id, tid = p["节点"], int(p["队伍"])
        side = self._side_of_team(node_id, tid)
        for 场次, pids in p["名单"].items():
            if 场次 == "盲抽":
                raise ValueError("盲抽局人员须通过 盲抽结果 事件写入")
            m = self._match(node_id, 场次)
            setattr(m, side, [int(x) for x in pids])
        self._validate_lineup(node_id, tid, side)
        self.changelog.append(f"[{ev.ts}] {node_id} 队伍{tid} 提交名单")

    def _validate_lineup(self, node_id: str, tid: int, side: str) -> None:
        """女双=2女;男双1-3 每人最多出战一次且为男性。"""
        males_used: List[int] = []
        for 场次 in ("男双1", "男双2", "男双3"):
            m = self.matches.get((node_id, 场次))
            pids = getattr(m, side) if m else []
            males_used += pids
            for pid in pids:
                if self.base.players[pid].性别 != "男":
                    raise ValueError(f"{场次} 含非男性选手 #{pid}")
        if len(males_used) != len(set(males_used)):
            raise ValueError(f"{node_id} 队伍{tid}: 有男生在三场男双中出战超过一次")
        wd = self.matches.get((node_id, "女双"))
        for pid in getattr(wd, side) if wd else []:
            if self.base.players[pid].性别 != "女":
                raise ValueError(f"女双含非女性选手 #{pid}")

    def _盲抽结果(self, ev: Event) -> None:
        p = ev.payload
        node_id, tid = p["节点"], int(p["队伍"])
        side = self._side_of_team(node_id, tid)
        m = self._match(node_id, "盲抽")
        pids = [int(x) for x in p["队员"]]
        for pid in pids:
            if self.base.players[pid].是否队长:
                raise ValueError(f"盲抽不能抽中队长 #{pid}")
        if m.类别 == "混双":
            genders = sorted(self.base.players[x].性别 for x in pids)
            if genders != ["女", "男"]:
                raise ValueError("混双盲抽必须为 1 男 1 女")
        setattr(m, side, pids)
        self.changelog.append(f"[{ev.ts}] {node_id} 队伍{tid} 盲抽: {pids}")

    def _局结束(self, ev: Event) -> None:
        p = ev.payload
        node_id, 场次 = p["节点"], p["场次"]
        m = self._match(node_id, 场次)
        局号 = int(p["局号"])
        if 局号 != len(m.局分) + 1:
            raise ValueError(
                f"{node_id} {场次} 期望第{len(m.局分) + 1}局,收到第{局号}局"
            )
        if m.胜方() is not None:
            raise ValueError(f"{node_id} {场次} 已决出胜负,不能再录入")
        比分 = (int(p["比分"][0]), int(p["比分"][1]))
        上限 = self.分制(node_id)
        if max(比分) < 上限:
            raise ValueError(f"比分 {比分} 与 {上限} 分制不符")
        m.局分.append(比分)
        self.changelog.append(f"[{ev.ts}] {node_id} {场次} 第{局号}局 {比分[0]}:{比分[1]}")

    def _缺席登记(self, ev: Event) -> None:
        p = ev.payload
        tid = int(p["队伍"])
        self.缺人[tid] = {"缺席者": int(p["缺席者序号"]), "顶替": {}}
        self.changelog.append(
            f"[{ev.ts}] 队伍{tid} 缺席登记 #{p['缺席者序号']},该队改 {self.cfg['赛制']['缺人队伍分数']} 分制"
        )

    def _顶替指定(self, ev: Event) -> None:
        p = ev.payload
        tid, 轮次, pid = int(p["队伍"]), int(p["轮次"]), int(p["顶替者序号"])
        if tid not in self.缺人:
            raise ValueError(f"队伍{tid}未登记缺席,不能指定顶替")
        used = self.缺人[tid]["顶替"]
        for r, existing in used.items():
            if r != 轮次 and existing == pid:
                raise ValueError(
                    f"#{pid} 已在第{r}轮顶替过,三轮顶替队员不能重复"
                )
        used[轮次] = pid
        self.changelog.append(f"[{ev.ts}] 队伍{tid} 第{轮次}轮由 #{pid} 顶替出战")


def replay_matchday(
    base: TournamentState, events: List[Event], cfg: dict
) -> MatchDayState:
    """在赛前状态之上重放比赛日事件。"""
    md = MatchDayState(base=base, cfg=cfg)
    for ev in events:
        if ev.type in PRE_MATCH_EVENTS:
            continue
        md.apply(ev)
    return md
