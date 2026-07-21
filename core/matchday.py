"""Match-day state, built by replaying match-day events. Pure apart from the
mutable dataclass containers.

Handled event types: group_draw / lineup_submit / blind_draw_result /
game_finished / absence_registered / substitute_assigned.
Pre-match events (initial_draw / assign_teams / withdraw_redraw) are handled
by core.replay and skipped here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import rules
from .models import Event, TournamentState
from .replay import PRE_MATCH_EVENTS


@dataclass
class Match:
    slot: str  # WD / MD1 / MD2 / MD3 / BLIND
    category: str  # WD / MD / XD
    a: List[int] = field(default_factory=list)  # player ids, side a
    b: List[int] = field(default_factory=list)
    games: List[Tuple[int, int]] = field(default_factory=list)

    def winner(self, games_to_win: int = 2) -> Optional[str]:
        return rules.match_winner(self.games, games_to_win)


@dataclass
class MatchDayState:
    base: TournamentState  # pre-match replay result (final rosters)
    cfg: dict
    group_of: Dict[str, int] = field(default_factory=dict)  # "G1" -> team id
    nodes: Dict[str, rules.Node] = field(default_factory=rules.bracket)
    # (node_id, slot) -> Match
    matches: Dict[Tuple[str, str], Match] = field(default_factory=dict)
    # short-handed teams: team id -> {"absent_id": pid, "substitutes": {round: pid}}
    shorthanded: Dict[int, dict] = field(default_factory=dict)
    changelog: List[str] = field(default_factory=list)

    # ---------- queries ----------

    def node_groups(self, node_id: str) -> Optional[Tuple[str, str]]:
        """G labels of both sides; None while upstream is undecided."""
        return rules.resolve_groups(self.nodes[node_id], self.node_winners(), self.nodes)

    def node_teams(self, node_id: str) -> Optional[Tuple[int, int]]:
        gs = self.node_groups(node_id)
        if gs is None or not self.group_of:
            return None
        return self.group_of[gs[0]], self.group_of[gs[1]]

    def node_winners(self) -> Dict[str, str]:
        """Decided nodes: node_id -> winning G label."""
        winners: Dict[str, str] = {}
        matches_to_win = self.cfg["format"]["matches_to_win"]
        # resolve in round order so upstream results exist before downstream
        for nid in sorted(self.nodes, key=lambda i: self.nodes[i].round):
            games_by_slot = {
                slot: m.games
                for (n, slot), m in self.matches.items()
                if n == nid and m.games
            }
            if not games_by_slot:
                continue
            w = rules.matchup_winner(games_by_slot, matches_to_win)
            if w is None:
                continue
            gs = rules.resolve_groups(self.nodes[nid], winners, self.nodes)
            if gs is not None:
                winners[nid] = gs[0] if w == "a" else gs[1]
        return winners

    def node_finished(self, node_id: str) -> bool:
        """A matchup ends when all 5 matches are decided (all are played even
        after the matchup itself is mathematically decided)."""
        games_to_win = self.cfg["format"]["games_per_match"] // 2 + 1
        done = 0
        for slot in rules.MATCH_SLOTS:
            m = self.matches.get((node_id, slot))
            if m and m.winner(games_to_win) is not None:
                done += 1
        return done == len(rules.MATCH_SLOTS)

    def points_for(self, node_id: str) -> int:
        """Points per game in this matchup: short-handed team on either side
        switches the whole matchup to the short-handed scoring."""
        teams = self.node_teams(node_id)
        if teams and any(t in self.shorthanded for t in teams):
            return self.cfg["format"]["shorthanded_points"]
        return self.cfg["format"]["points_per_game"]

    def player_round_results(self, pid: int, round_no: int) -> List[dict]:
        """All decided matches of a player in a round:
        [{"won": bool, "margin": int, "num_games": int, "partners": [...]}]"""
        out = []
        for (nid, slot), m in self.matches.items():
            if self.nodes[nid].round != round_no or not m.games:
                continue
            side = "a" if pid in m.a else ("b" if pid in m.b else None)
            if side is None:
                continue
            w = m.winner()
            if w is None:
                continue
            sign = 1 if side == "a" else -1
            margin = sum(sign * (g[0] - g[1]) for g in m.games)
            out.append(
                {
                    "won": w == side,
                    "margin": margin,
                    "num_games": len(m.games),
                    "partners": list(m.a if side == "a" else m.b),
                }
            )
        return out

    def blind_prior_picks(self, tid: int, before_round: int) -> set:
        """Players of a team already fielded in blind matches of earlier rounds."""
        prior: set = set()
        for (nid, s), m in self.matches.items():
            if s != "BLIND" or self.nodes[nid].round >= before_round:
                continue
            teams = self.node_teams(nid)
            if teams is None:
                continue
            if teams[0] == tid:
                prior |= set(m.a)
            elif teams[1] == tid:
                prior |= set(m.b)
        return prior

    def blind_eligible(self, node_id: str, tid: int) -> Tuple[List[int], List[int]]:
        """(eligible females, eligible males) for a team's blind match:
        captains excluded, players drawn in earlier rounds' blinds excluded."""
        prior = self.blind_prior_picks(tid, self.nodes[node_id].round)
        members = self.base.teams[tid]
        females = sorted(
            p for p in members if self.base.players[p].gender == "F" and p not in prior
        )
        males = sorted(
            p for p in members
            if self.base.players[p].gender == "M"
            and not self.base.players[p].is_captain
            and p not in prior
        )
        return females, males

    def lineup_status(self, node_id: str, tid: int) -> dict:
        """Submission/compliance status of a team's lineup for a matchup.

        Returns {"submitted": bool, "complete": bool, "issues": [str],
                 "lineup": {slot: [pids]}}. Issues list what is missing or
        non-compliant (2 players per slot, correct genders, no man in two MDs).
        """
        side = self._side_of_team(node_id, tid)
        lineup: Dict[str, List[int]] = {}
        issues: List[str] = []
        for slot in ("WD", "MD1", "MD2", "MD3"):
            m = self.matches.get((node_id, slot))
            pids = list(getattr(m, side)) if m else []
            lineup[slot] = pids
            if len(pids) != 2:
                issues.append(f"{slot}: needs 2 players (has {len(pids)})")
        for pid in lineup["WD"]:
            if self.base.players[pid].gender != "F":
                issues.append(f"WD: #{pid} is not female")
        males = [p for s in ("MD1", "MD2", "MD3") for p in lineup[s]]
        for pid in males:
            if self.base.players[pid].gender != "M":
                issues.append(f"#{pid} in an MD slot is not male")
        if len(males) != len(set(males)):
            issues.append("a man is fielded in more than one MD")
        submitted = any(lineup.values())
        return {"submitted": submitted, "complete": submitted and not issues,
                "issues": issues, "lineup": lineup}

    def pair_strong(self, pids: List[int], round_no: int) -> bool:
        """Whether a pair counts as "strong" for the overtime bonus.
        Never strong in round 1.

        (a) both players won every match they played in the previous round; or
        (b) the two partnered in the previous round and won big:
            avg margin per game >= margin_per_game_threshold, or
            total match margin >= margin_per_match_threshold.
        """
        if round_no <= 1 or len(pids) < 2:
            return False
        prev = round_no - 1
        d = self.cfg["duration"]
        recs = {p: self.player_round_results(p, prev) for p in pids}
        # (a) clean sweep for both players
        if all(r and all(x["won"] for x in r) for r in recs.values()):
            return True
        # (b) partnered blowout win
        for r in recs[pids[0]]:
            if set(pids) <= set(r["partners"]) and r["won"]:
                if (
                    r["margin"] / max(r["num_games"], 1) >= d["margin_per_game_threshold"]
                    or r["margin"] >= d["margin_per_match_threshold"]
                ):
                    return True
        return False

    # ---------- event application ----------

    def apply(self, ev: Event) -> None:
        handler = {
            "group_draw": self._group_draw,
            "lineup_submit": self._lineup_submit,
            "blind_draw_result": self._blind_draw_result,
            "game_finished": self._game_finished,
            "game_corrected": self._game_corrected,
            "absence_registered": self._absence_registered,
            "substitute_assigned": self._substitute_assigned,
        }.get(ev.type)
        if handler is None:
            raise ValueError(f"unknown match-day event: {ev.type} (seq={ev.seq})")
        handler(ev)

    def _match(self, node_id: str, slot: str) -> Match:
        key = (node_id, slot)
        if key not in self.matches:
            if slot == "BLIND":
                round_no = self.nodes[node_id].round
                category = self.cfg["format"]["blind_match_types"][round_no - 1]
            else:
                category = "WD" if slot == "WD" else "MD"
            self.matches[key] = Match(slot=slot, category=category)
        return self.matches[key]

    def _side_of_team(self, node_id: str, tid: int) -> str:
        teams = self.node_teams(node_id)
        if teams is None:
            raise ValueError(f"{node_id} sides are not determined yet")
        if tid == teams[0]:
            return "a"
        if tid == teams[1]:
            return "b"
        raise ValueError(f"team {tid} is not part of {node_id}")

    def _group_draw(self, ev: Event) -> None:
        groups = {g: int(t) for g, t in ev.payload["groups"].items()}
        if sorted(groups) != [f"G{i}" for i in range(1, 9)] or sorted(
            groups.values()
        ) != sorted(self.base.teams):
            raise ValueError("group draw must cover G1-G8 and all 8 teams")
        self.group_of = groups
        self.changelog.append(f"[{ev.ts}] group draw: {groups}")

    def _lineup_submit(self, ev: Event) -> None:
        p = ev.payload
        node_id, tid = p["node"], int(p["team"])
        side = self._side_of_team(node_id, tid)
        for slot, pids in p["lineup"].items():
            if slot == "BLIND":
                raise ValueError("blind-match players must come from blind_draw_result events")
            m = self._match(node_id, slot)
            setattr(m, side, [int(x) for x in pids])
        self._validate_lineup(node_id, tid, side)
        self.changelog.append(f"[{ev.ts}] {node_id} team {tid} lineup submitted")

    def _validate_lineup(self, node_id: str, tid: int, side: str) -> None:
        """WD = 2 females; MD1-3 all male, each man plays at most one MD."""
        males_used: List[int] = []
        for slot in ("MD1", "MD2", "MD3"):
            m = self.matches.get((node_id, slot))
            pids = getattr(m, side) if m else []
            males_used += pids
            for pid in pids:
                if self.base.players[pid].gender != "M":
                    raise ValueError(f"{slot} contains non-male player #{pid}")
        if len(males_used) != len(set(males_used)):
            raise ValueError(f"{node_id} team {tid}: a man is fielded in more than one MD")
        wd = self.matches.get((node_id, "WD"))
        for pid in getattr(wd, side) if wd else []:
            if self.base.players[pid].gender != "F":
                raise ValueError(f"WD contains non-female player #{pid}")

    def _blind_draw_result(self, ev: Event) -> None:
        p = ev.payload
        node_id, tid = p["node"], int(p["team"])
        side = self._side_of_team(node_id, tid)
        m = self._match(node_id, "BLIND")
        pids = [int(x) for x in p["players"]]
        prior = self.blind_prior_picks(tid, self.nodes[node_id].round)
        for pid in pids:
            if self.base.players[pid].is_captain:
                raise ValueError(f"blind draw cannot select a captain (#{pid})")
            if pid in prior:
                raise ValueError(
                    f"#{pid} was already drawn in an earlier round's blind match"
                )
        if m.category == "XD":
            genders = sorted(self.base.players[x].gender for x in pids)
            if genders != ["F", "M"]:
                raise ValueError("XD blind draw must select exactly 1 male + 1 female")
        setattr(m, side, pids)
        self.changelog.append(f"[{ev.ts}] {node_id} team {tid} blind draw: {pids}")

    def _game_finished(self, ev: Event) -> None:
        p = ev.payload
        node_id, slot = p["node"], p["slot"]
        m = self._match(node_id, slot)
        game_no = int(p["game"])
        if game_no != len(m.games) + 1:
            raise ValueError(
                f"{node_id} {slot} expects game {len(m.games) + 1}, got game {game_no}"
            )
        if m.winner() is not None:
            raise ValueError(f"{node_id} {slot} is already decided")
        score = (int(p["score"][0]), int(p["score"][1]))
        cap = self.points_for(node_id)
        if max(score) < cap:
            raise ValueError(f"score {score} inconsistent with {cap}-point scoring")
        m.games.append(score)
        self.changelog.append(f"[{ev.ts}] {node_id} {slot} game {game_no} {score[0]}:{score[1]}")

    def _game_corrected(self, ev: Event) -> None:
        """Admin override for a mis-entered score: replaces one recorded game.

        History is never rewritten — this is a compensating event; replay
        applies the correction on top of the original entry. If the corrected
        score changes when the match became decided, games recorded past that
        point are dropped from the derived state.
        """
        p = ev.payload
        node_id, slot, game_no = p["node"], p["slot"], int(p["game"])
        key = (node_id, slot)
        m = self.matches.get(key)
        if m is None or game_no < 1 or game_no > len(m.games):
            raise ValueError(f"{node_id} {slot} has no recorded game {game_no} to correct")
        score = (int(p["score"][0]), int(p["score"][1]))
        rules.game_winner(score)  # rejects ties
        cap = self.points_for(node_id)
        if max(score) < cap:
            raise ValueError(f"score {score} inconsistent with {cap}-point scoring")
        old = m.games[game_no - 1]
        m.games[game_no - 1] = score
        # truncate anything recorded after the match is now decided
        games_to_win = self.cfg["format"]["games_per_match"] // 2 + 1
        wins = {"a": 0, "b": 0}
        keep = len(m.games)
        for i, g in enumerate(m.games):
            wins[rules.game_winner(g)] += 1
            if max(wins.values()) >= games_to_win:
                keep = i + 1
                break
        dropped = m.games[keep:]
        del m.games[keep:]
        note = f", dropped {len(dropped)} later game(s)" if dropped else ""
        self.changelog.append(
            f"[{ev.ts}] CORRECTION {node_id} {slot} game {game_no}: "
            f"{old[0]}:{old[1]} -> {score[0]}:{score[1]}{note}"
        )

    def _absence_registered(self, ev: Event) -> None:
        p = ev.payload
        tid = int(p["team"])
        self.shorthanded[tid] = {"absent_id": int(p["absent_id"]), "substitutes": {}}
        self.changelog.append(
            f"[{ev.ts}] team {tid} absence registered (#{p['absent_id']}), "
            f"team switches to {self.cfg['format']['shorthanded_points']}-point scoring"
        )

    def _substitute_assigned(self, ev: Event) -> None:
        p = ev.payload
        tid, round_no, pid = int(p["team"]), int(p["round"]), int(p["substitute_id"])
        if tid not in self.shorthanded:
            raise ValueError(f"team {tid} has no registered absence")
        used = self.shorthanded[tid]["substitutes"]
        for r, existing in used.items():
            if r != round_no and existing == pid:
                raise ValueError(
                    f"#{pid} already substituted in round {r}; "
                    "substitutes must differ across the three rounds"
                )
        used[round_no] = pid
        self.changelog.append(f"[{ev.ts}] team {tid} round {round_no} substitute: #{pid}")


def replay_matchday(
    base: TournamentState, events: List[Event], cfg: dict
) -> MatchDayState:
    """Replay match-day events on top of the pre-match state."""
    md = MatchDayState(base=base, cfg=cfg)
    for ev in events:
        if ev.type in PRE_MATCH_EVENTS:
            continue
        md.apply(ev)
    return md
