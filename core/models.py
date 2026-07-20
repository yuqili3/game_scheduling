"""数据模型: 纯数据结构,不含任何 IO / 时钟 / 随机数。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Player:
    序号: int
    姓名: str
    性别: str  # "男" / "女"
    是否队长: bool = False


@dataclass(frozen=True)
class Event:
    """事件日志的一行。所有状态都是事件重放的结果。"""

    seq: int
    ts: str
    type: str
    actor: str
    payload: dict
    seed: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "type": self.type,
            "actor": self.actor,
            "payload": self.payload,
            "seed": self.seed,
        }

    @staticmethod
    def from_dict(d: dict) -> "Event":
        return Event(
            seq=int(d["seq"]),
            ts=d["ts"],
            type=d["type"],
            actor=d.get("actor", ""),
            payload=d.get("payload", {}),
            seed=d.get("seed"),
        )


@dataclass
class TournamentState:
    players: Dict[int, Player] = field(default_factory=dict)
    # 队伍编号 -> 成员序号列表,约定队长恒在首位
    teams: Dict[int, List[int]] = field(default_factory=dict)
    changelog: List[str] = field(default_factory=list)

    def team_of(self, pid: int) -> Optional[int]:
        for tid, members in self.teams.items():
            if pid in members:
                return tid
        return None

    def validate(self, 每队女生数: int = 2, 每队男生数: int = 6) -> None:
        """校验每队结构: 1 队长 + (每队男生数) 男(含队长) + 每队女生数 女,且无人跨队。"""
        seen: List[int] = []
        for tid, members in self.teams.items():
            ps = [self.players[m] for m in members]
            captains = [p for p in ps if p.是否队长]
            males = [p for p in ps if p.性别 == "男"]
            females = [p for p in ps if p.性别 == "女"]
            if len(captains) != 1:
                raise ValueError(f"队伍{tid}队长数={len(captains)},应为1")
            if len(males) != 每队男生数 or len(females) != 每队女生数:
                raise ValueError(
                    f"队伍{tid}结构异常: 男{len(males)}/{每队男生数} 女{len(females)}/{每队女生数}"
                )
            if not self.players[members[0]].是否队长:
                raise ValueError(f"队伍{tid}首位成员不是队长")
            seen += members
        if len(seen) != len(set(seen)):
            raise ValueError("有人同时出现在两支队伍")
