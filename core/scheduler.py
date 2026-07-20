"""排程器: 时长模型 + 贪心排程。纯函数,事件驱动重排 = 每次事件后重新调用 plan()。

规则:
- 对抗内: 女双 + 男双1-3 并行(人员天然不重叠);盲抽局等 4 场全部结束
  且选手休息 ≥ 连续出场休息 分钟后开打
- 跨轮(按对阵粒度): 节点的两个上游对抗都结束后即可开打(+ 休息间隔)
- 场地分上下两个半区各 每组场地数 块,组内贪心取最早空闲场地
- 时长: 基础(女双/男双/混双) × 15分制缩放(缺人队伍) + 强强对话加时;
  未打的第三局按满时长预留(时长瓶颈按最坏情况),2:0 结束后重排自动释放
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import rules
from .matchday import MatchDayState


@dataclass(frozen=True)
class Slot:
    开始: float  # 距比赛开始的分钟数
    结束: float
    场地: int
    节点: str
    场次: str
    局号: int  # 1/2;3 表示条件第三局
    说明: str = ""


def game_minutes(md: MatchDayState, node_id: str, 场次: str) -> float:
    """单局预估时长(分钟)。"""
    t = md.cfg["时长模型"]
    m = md.matches.get((node_id, 场次))
    类别 = m.类别 if m else (
        "女双" if 场次 == "女双"
        else md.cfg["赛制"]["盲抽局类型"][md.nodes[node_id].轮次 - 1] if 场次 == "盲抽"
        else "男双"
    )
    base = float(t[f"{类别}单局时长"])
    # 缺人队伍 → 15 分制缩放
    if t.get("分制缩放", True):
        分制 = md.分制(node_id)
        base = base * 分制 / md.cfg["赛制"]["每局分数"]
    # 强强对话加时: 双方组合都"强"(需要双方名单已知)
    if m and m.a and m.b:
        轮次 = md.nodes[node_id].轮次
        if md.pair_strong(m.a, 轮次) and md.pair_strong(m.b, 轮次):
            base += float(t["强强对话加时"])
    return base


def _remaining_games(md: MatchDayState, node_id: str, 场次: str) -> List[Tuple[int, bool]]:
    """未打完的局: [(局号, 是否条件局)]。

    第三局即使尚不确定是否发生,也按满时长预留场地——排程是时长瓶颈,
    必须按最坏情况估计;实际 2:0 结束后重排,该时段自动释放。
    """
    m = md.matches.get((node_id, 场次))
    played = len(m.局分) if m else 0
    if m and m.胜方() is not None:
        return []
    每场局数 = md.cfg["赛制"]["每场局数"]
    out: List[Tuple[int, bool]] = []
    for g in range(played + 1, 每场局数 + 1):
        必打 = g < 每场局数 or (m is not None and len(m.局分) == 每场局数 - 1)
        out.append((g, not 必打))
    return out


def plan(md: MatchDayState, now: float = 0.0) -> List[Slot]:
    """从 now(距开赛分钟数)起规划所有未完成对局。确定性贪心,无随机。"""
    cfg = md.cfg
    每组 = cfg["场地"]["每组场地数"]
    总数 = cfg["场地"]["场地总数"]
    休息 = float(cfg["时长模型"]["连续出场休息"])
    间隔 = float(cfg["时长模型"]["换场间隔"])
    banks = {
        0: list(range(1, 每组 + 1)),
        1: list(range(每组 + 1, 总数 + 1)),
    }
    court_free: Dict[int, float] = {c: now for c in range(1, 总数 + 1)}
    slots: List[Slot] = []
    node_end: Dict[str, float] = {}

    def node_ready(nid: str) -> float:
        node = md.nodes[nid]
        if node.轮次 == 1:
            return now
        t = now
        for feeder in node.feeders:
            fid = feeder[0]
            if md.node_finished(fid):
                t = max(t, now)
            else:
                t = max(t, node_end.get(fid, now) + 休息)
        return t

    # 按轮次顺序处理 12 个节点(同轮内按 id 稳定排序 → 确定性)
    for nid in sorted(md.nodes, key=lambda i: (md.nodes[i].轮次, i)):
        node = md.nodes[nid]
        if md.node_finished(nid):
            node_end[nid] = now
            continue
        ready = node_ready(nid)
        bank = banks[node.半区]
        场末: Dict[str, float] = {}

        def schedule_match(场次: str, earliest: float) -> Optional[float]:
            games = _remaining_games(md, nid, 场次)
            if not games:
                return None
            dur = game_minutes(md, nid, 场次)
            court = min(bank, key=lambda c: (court_free[c], c))
            start = max(court_free[court], earliest)
            t = start
            for 局号, 条件局 in games:
                标注 = "第三局(条件)" if 条件局 else ""
                slots.append(Slot(t, t + dur, court, nid, 场次, 局号, 标注))
                t += dur
            court_free[court] = t + 间隔
            return t

        # 前 4 场并行
        for 场次 in ("女双", "男双1", "男双2", "男双3"):
            end = schedule_match(场次, ready)
            if end is not None:
                场末[场次] = end
        # 盲抽局: 等前 4 场全部结束 + 休息
        前四末 = max(场末.values()) if 场末 else ready
        blind_ready = (前四末 + 休息) if 场末 else ready
        end = schedule_match("盲抽", blind_ready)
        if end is not None:
            场末["盲抽"] = end
        node_end[nid] = max(场末.values()) if 场末 else now

    return sorted(slots, key=lambda s: (s.开始, s.场地))


def utilization(slots: List[Slot], 场地总数: int, now: float = 0.0) -> float:
    """场地利用率 = 占用分钟 / (场地数 × 总时长)。"""
    if not slots:
        return 0.0
    span = max(s.结束 for s in slots) - now
    used = sum(s.结束 - s.开始 for s in slots)
    return used / (场地总数 * span) if span > 0 else 0.0
