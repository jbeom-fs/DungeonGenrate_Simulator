"""
Proto_JBRL Dungeon Step Viewer

Python/Tkinter visualizer that ports the current Proto_JBRL DungeonGenerator flow:
Seed+Floor -> BSP split -> room placement -> room fill -> MST corridors -> pair-scored EXTRA corridor -> stair placement.

Controls:
- Enter seed and floor, then click Generate / Enter
- Left / Right arrow: previous / next step
- Home / End: first / last step
- Timeline slider or ▶ Play button: scrub / auto-play generation steps

This is a standalone visualization tool. It does not import Unity project files.

Exact-match rule:
- Use the same Seed/Floor and the same serialized DungeonSettings values as Unity.
- The generator logic and old System.Random behavior are ported so the dungeon grid should match
  the Unity map when those inputs match.
"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk, messagebox
from dataclasses import dataclass
from typing import List, Optional, Tuple, Set, Dict
from PIL import Image, ImageTk, ImageDraw

EMPTY = 0
ROOM = 1
CORRIDOR = 2
STAIR_UP = 3
DOOR_CLOSED = 5


# -----------------------------
# C# unchecked int + System.Random compatible enough for Unity/old .NET Random
# -----------------------------
INT_MIN = -2147483648
INT_MAX = 2147483647
MBIG = INT_MAX
MSEED = 161803398


def int32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


def csharp_remainder(a: int, b: int) -> int:
    # C# % keeps the sign of the dividend. Python % is always non-negative for positive divisor.
    q = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        q = -q
    return a - q * b


def unity_build_settings_seed_from_text(text: str) -> int:
    # DungeonManager.BuildSettings() currently does:
    #     s.Seed = (int)(seed % int.MaxValue);
    # The Inspector seed is a long, while DungeonSettings.Seed is int?.
    # Matching this fold is required for 12-digit Unity seeds to generate the same map.
    return int32(csharp_remainder(int(text.strip()), INT_MAX))


class DotNetRandom:
    """Old System.Random subtractive generator, matching Unity/Mono style behavior."""

    def __init__(self, seed: int):
        seed = int32(seed)
        self.seed_array = [0] * 56
        subtraction = INT_MAX if seed == INT_MIN else abs(seed)
        mj = MSEED - subtraction
        mj %= MBIG
        self.seed_array[55] = mj
        mk = 1
        for i in range(1, 55):
            ii = (21 * i) % 55
            self.seed_array[ii] = mk
            mk = mj - mk
            if mk < 0:
                mk += MBIG
            mj = self.seed_array[ii]
        for _ in range(4):
            for i in range(1, 56):
                self.seed_array[i] -= self.seed_array[1 + (i + 30) % 55]
                if self.seed_array[i] < 0:
                    self.seed_array[i] += MBIG
        self.inext = 0
        self.inextp = 21

    def internal_sample(self) -> int:
        self.inext += 1
        if self.inext >= 56:
            self.inext = 1
        self.inextp += 1
        if self.inextp >= 56:
            self.inextp = 1
        ret = self.seed_array[self.inext] - self.seed_array[self.inextp]
        if ret == MBIG:
            ret -= 1
        if ret < 0:
            ret += MBIG
        self.seed_array[self.inext] = ret
        return ret

    def sample(self) -> float:
        return self.internal_sample() * (1.0 / MBIG)

    def next_double(self) -> float:
        return self.sample()

    def next(self, *args: int) -> int:
        if len(args) == 0:
            return self.internal_sample()
        if len(args) == 1:
            max_value = args[0]
            if max_value < 0:
                raise ValueError("maxValue must be non-negative")
            return int(self.sample() * max_value)
        if len(args) == 2:
            min_value, max_value = args
            if min_value > max_value:
                raise ValueError("minValue must be <= maxValue")
            rng = max_value - min_value
            if rng <= INT_MAX:
                return int(self.sample() * rng) + min_value
            return int(self.get_sample_for_large_range() * rng) + min_value
        raise TypeError("next accepts 0, 1, or 2 integer arguments")

    def get_sample_for_large_range(self) -> float:
        result = self.internal_sample()
        if self.internal_sample() % 2 == 0:
            result = -result
        d = result + (INT_MAX - 1)
        return d / (2 * INT_MAX - 1)


# -----------------------------
# Data
# -----------------------------
@dataclass
class DungeonSettings:
    map_width: int = 80
    map_height: int = 50
    min_room_size: int = 5
    max_room_size: int = 14
    bsp_depth: int = 4
    padding: int = 2
    extra_conn_prob: float = 0.5
    extra_candidate_count: int = 12
    seed: Optional[int] = None
    floor: int = 1
    max_floor: int = 100
    min_straight: int = 2

    def validate(self) -> None:
        if self.map_width < 10:
            raise ValueError("MapWidth must be >= 10")
        if self.map_height < 10:
            raise ValueError("MapHeight must be >= 10")
        if self.min_room_size < 3:
            raise ValueError("MinRoomSize must be >= 3")
        if self.max_room_size < self.min_room_size:
            raise ValueError("MaxRoomSize must be >= MinRoomSize")
        if self.bsp_depth < 1:
            raise ValueError("BspDepth must be >= 1")
        if self.padding < 1:
            raise ValueError("Padding must be >= 1")
        if self.min_straight < 1:
            raise ValueError("MinStraight must be >= 1")
        if self.max_floor < 1:
            self.max_floor = 100
        self.floor = max(1, min(self.max_floor, self.floor))
        self.extra_conn_prob = max(0.0, min(1.0, self.extra_conn_prob))
        self.extra_candidate_count = max(0, self.extra_candidate_count)

    def derive_seed(self) -> int:
        s = self.seed if self.seed is not None else 0
        a = int32(self.floor * int32(2654435761))
        b = int32(s ^ a)
        mixed = int32(b * int32(2246822519))
        return mixed & 0x7FFFFFFF


@dataclass
class Room:
    x: int
    y: int
    w: int
    h: int
    cx: int
    cy: int


@dataclass
class BSPNode:
    x: int
    y: int
    w: int
    h: int
    left: Optional['BSPNode'] = None
    right: Optional['BSPNode'] = None

    @property
    def is_leaf(self) -> bool:
        return self.left is None and self.right is None


@dataclass
class ExtraCorridorCandidate:
    src_idx: int
    dst_idx: int
    horiz_first: bool
    path: List[Tuple[int, int]]
    extra_attempt_index: int
    candidate_index: int
    primary_path: bool
    score: int
    overlap: int
    path_len: int
    center_dist_sq: int
    parallel_run: int
    axis_name: str


@dataclass
class Step:
    title: str
    grid: List[List[int]]
    rooms: List[Room]
    nodes: List[BSPNode]
    path: List[Tuple[int, int]]
    note: str = ""
    extra_paths: Optional[List[Tuple[List[Tuple[int, int]], str, str]]] = None


# -----------------------------
# Generator with step snapshots
# -----------------------------
class DungeonGenerator:
    def __init__(self, settings: DungeonSettings):
        self.s = settings
        self.s.validate()
        self.rng = DotNetRandom(self.s.derive_seed()) if self.s.seed is not None else DotNetRandom(0)
        self.grid = [[EMPTY for _ in range(self.s.map_width)] for _ in range(self.s.map_height)]
        self.corridor_tiles: Set[Tuple[int, int]] = set()
        self.rooms: List[Room] = []
        self.steps: List[Step] = []
        self.nodes: List[BSPNode] = []

    def snapshot(
        self,
        title: str,
        path: Optional[List[Tuple[int, int]]] = None,
        note: str = "",
        extra_paths: Optional[List[Tuple[List[Tuple[int, int]], str, str]]] = None,
    ) -> None:
        copied_grid = [row[:] for row in self.grid]
        copied_extra_paths = None
        if extra_paths:
            copied_extra_paths = [(list(candidate_path), color, label) for candidate_path, color, label in extra_paths]
        self.steps.append(Step(title, copied_grid, self.rooms[:], self.nodes[:], list(path or []), note, copied_extra_paths))

    @staticmethod
    def extra_candidate_color(index: int) -> str:
        # High-contrast palette for overlaying multiple EXTRA candidates.
        palette = [
            "#00e5ff", "#ffd60a", "#7cff6b", "#ff7ad9",
            "#b388ff", "#ff9f1c", "#2ec4b6", "#f72585",
            "#a7f432", "#4cc9f0", "#ff6b6b", "#caffbf",
            "#9bf6ff", "#fdffb6", "#ffc6ff", "#bdb2ff",
        ]
        return palette[index % len(palette)]

    @classmethod
    def build_extra_candidate_overlays(
        cls,
        candidates: List[ExtraCorridorCandidate],
        selected: Optional[ExtraCorridorCandidate] = None,
    ) -> List[Tuple[List[Tuple[int, int]], str, str]]:
        overlays: List[Tuple[List[Tuple[int, int]], str, str]] = []
        selected_key = None
        if selected is not None:
            selected_key = (selected.src_idx, selected.dst_idx, selected.candidate_index, selected.extra_attempt_index)

        color_index = 0
        for candidate in candidates:
            key = (candidate.src_idx, candidate.dst_idx, candidate.candidate_index, candidate.extra_attempt_index)
            if key == selected_key:
                continue
            label = f"C{color_index}: R{candidate.src_idx}->{candidate.dst_idx} score={candidate.score}"
            overlays.append((candidate.path[:], cls.extra_candidate_color(color_index), label))
            color_index += 1

        # Keep the selected candidate in the overlay list too, but reserve red for the existing current-path highlight.
        # This lets users compare it against all other pair-best candidates while preserving the final choice.
        if selected is not None:
            label = f"SELECTED R{selected.src_idx}->{selected.dst_idx} score={selected.score}"
            overlays.append((selected.path[:], "#ff3b30", label))
        return overlays

    def generate(self) -> List[Step]:
        root = BSPNode(1, 1, self.s.map_width - 2, self.s.map_height - 2)
        self.nodes = [root]
        self.snapshot("00. 초기 BSP 영역", note=f"derived seed = {self.s.derive_seed()}")
        self.bsp_split(root, 0)
        self.snapshot("01. BSP 분할 완료")
        self.collect_rooms(root)
        self.snapshot("02. 리프 노드에 방 위치/크기 결정")
        for i, room in enumerate(self.rooms):
            self.fill_room(room)
            self.snapshot(f"03-{i+1:02d}. 방 바닥 채우기 R{i}")
        self.connect_all()
        self.place_stairs()
        self.snapshot("99. 최종 던전")
        return self.steps

    def bsp_split(self, node: BSPNode, depth: int) -> None:
        if depth >= self.s.bsp_depth:
            return
        min_split = self.s.min_room_size * 2 + self.s.padding * 4
        can_h = node.h >= min_split
        can_v = node.w >= min_split
        if not can_h and not can_v:
            return
        if can_h and can_v:
            horiz = node.h > node.w * 1.25 or (node.w <= node.h * 1.25 and self.rng.next_double() < 0.5)
        else:
            horiz = can_h
        if horiz:
            lo = node.y + self.s.min_room_size + self.s.padding * 2
            hi = node.y + node.h - self.s.min_room_size - self.s.padding * 2
            if lo > hi:
                return
            sp = self.rng.next(lo, hi + 1)
            node.left = BSPNode(node.x, node.y, node.w, sp - node.y)
            node.right = BSPNode(node.x, sp, node.w, node.y + node.h - sp)
        else:
            lo = node.x + self.s.min_room_size + self.s.padding * 2
            hi = node.x + node.w - self.s.min_room_size - self.s.padding * 2
            if lo > hi:
                return
            sp = self.rng.next(lo, hi + 1)
            node.left = BSPNode(node.x, node.y, sp - node.x, node.h)
            node.right = BSPNode(sp, node.y, node.x + node.w - sp, node.h)
        self.nodes.extend([node.left, node.right])
        self.snapshot(f"01-{len(self.steps):02d}. BSP split depth {depth+1}")
        self.bsp_split(node.left, depth + 1)
        self.bsp_split(node.right, depth + 1)

    def collect_rooms(self, node: BSPNode) -> None:
        if node.is_leaf:
            p = self.s.padding
            max_w = min(node.w - p * 2, self.s.max_room_size)
            max_h = min(node.h - p * 2, self.s.max_room_size)
            if max_w < self.s.min_room_size or max_h < self.s.min_room_size:
                return
            rw = self.rng.next(self.s.min_room_size, max_w + 1)
            rh = self.rng.next(self.s.min_room_size, max_h + 1)
            rx_range = node.w - rw - p
            ry_range = node.h - rh - p
            rx = node.x + (self.rng.next(p, rx_range + 1) if rx_range > p else p)
            ry = node.y + (self.rng.next(p, ry_range + 1) if ry_range > p else p)
            room = Room(rx, ry, rw, rh, rx + rw // 2, ry + rh // 2)
            self.rooms.append(room)
            self.snapshot(f"02-{len(self.rooms):02d}. 방 생성 R{len(self.rooms)-1}")
        else:
            if node.left:
                self.collect_rooms(node.left)
            if node.right:
                self.collect_rooms(node.right)

    def fill_room(self, room: Room) -> None:
        for y in range(room.y, room.y + room.h):
            for x in range(room.x, room.x + room.w):
                self.grid[y][x] = ROOM

    def connect_all(self) -> None:
        n = len(self.rooms)
        if n < 2:
            return

        connected: Set[int] = {0}
        remaining: Set[int] = set(range(1, n))
        # C# HashSet iteration parity note: the project previously relied on
        # insertion-shaped traversal. This viewer keeps explicit deterministic
        # lists for stable tie behavior.
        connected_order: List[int] = [0]
        remaining_order: List[int] = list(range(1, n))
        mst_pairs: Set[Tuple[int, int]] = set()
        path_buf: List[Tuple[int, int]] = []
        edge_index = 0

        # Phase 1: MST only. EXTRA corridors no longer modify connected/remaining.
        while remaining:
            best_dist = float("inf")
            src_idx = -1
            dst_idx = -1
            for i in connected_order:
                for j in remaining_order:
                    if j not in remaining:
                        continue
                    d = self.euclidean_dist(self.rooms[i], self.rooms[j])
                    if d < best_dist:
                        best_dist = d
                        src_idx, dst_idx = i, j

            carved, path, note = self.draw_l_corridor(src_idx, dst_idx, True, path_buf)
            edge_index += 1
            self.snapshot(f"04-{edge_index:02d}. MST 통로 R{src_idx} -> R{dst_idx}", path, note)

            mst_pairs.add((min(src_idx, dst_idx), max(src_idx, dst_idx)))
            if dst_idx not in connected:
                connected_order.append(dst_idx)
            connected.add(dst_idx)
            remaining.remove(dst_idx)

        # Phase 2: Unity latest flow. After every MST edge has been carved,
        # run up to rooms.Count - 1 EXTRA attempts. EXTRA never mutates
        # connected/remaining; it only adds selected room pairs to connectedPairs.
        self.connect_extra_corridors(mst_pairs, path_buf)

    def connect_extra_corridors(self, connected_pairs: Set[Tuple[int, int]], path_buf: List[Tuple[int, int]]) -> None:
        """Port of current DungeonGenerator.ConnectExtraCorridors.

        Latest Unity flow:
        - MST is completed first.
        - Run max(0, rooms.Count - 1) EXTRA attempts.
        - Each attempt rolls ExtraConnProb. If the roll fails, no pair is reviewed.
        - For a successful attempt, every currently non-connected room pair is reviewed
          in stable i<j order.
        - For each room pair, ExtraCandidateCount path variants are emitted.
        - Dirty candidates are removed.
        - One best candidate is kept per room pair.
        - The best pair-best candidate for that attempt is carved.
        - The carved EXTRA pair is added to connectedPairs, so later attempts skip it.
        """
        n = len(self.rooms)
        if n < 2:
            return
        if self.s.extra_conn_prob <= 0.0:
            self.snapshot("05. EXTRA 통로 비활성", note="ExtraConnProb <= 0")
            return
        if self.s.extra_candidate_count <= 0:
            self.snapshot("05. EXTRA 후보 검토 생략", note="ExtraCandidateCount <= 0")
            return

        pair_best_candidates: List[ExtraCorridorCandidate] = []
        pair_candidates: List[ExtraCorridorCandidate] = []
        max_attempts = max(0, n - 1)
        carved_count = 0

        for attempt_index in range(max_attempts):
            roll = self.rng.next_double()
            if roll >= self.s.extra_conn_prob:
                self.snapshot(
                    f"05-{attempt_index + 1:02d}. EXTRA 시도 스킵",
                    note=f"attempt={attempt_index} roll={roll:.6f} >= ExtraConnProb={self.s.extra_conn_prob}"
                )
                continue

            pair_best_candidates.clear()
            rejected_notes: List[str] = []
            checked_pairs = 0
            skipped_connected_pairs = 0

            for i in range(n - 1):
                for j in range(i + 1, n):
                    pair_key = (i, j)
                    if pair_key in connected_pairs:
                        skipped_connected_pairs += 1
                        continue
                    checked_pairs += 1
                    pair_candidates.clear()
                    dist_sq = self.center_dist_sq(self.rooms[i], self.rooms[j])
                    self.build_extra_path_candidates_for_pair(
                        i, j, dist_sq, attempt_index, path_buf, pair_candidates, rejected_notes
                    )
                    if not pair_candidates:
                        continue
                    pair_best_candidates.append(self.select_best_extra_corridor_candidate(pair_candidates))

            if not pair_best_candidates:
                note = (
                    f"attempt={attempt_index} roll={roll:.6f} checked_pairs={checked_pairs} "
                    f"skipped_connected_pairs={skipped_connected_pairs} pair_best_candidates=0"
                )
                if rejected_notes:
                    note += "\n" + "\n".join(rejected_notes[:12])
                    if len(rejected_notes) > 12:
                        note += f"\n... rejected {len(rejected_notes) - 12} more"
                self.snapshot(f"05-{attempt_index + 1:02d}. EXTRA 선택 없음", note=note)
                continue

            selected = self.select_best_extra_corridor_candidate(pair_best_candidates)
            self.carve_path(selected.path)
            connected_pairs.add((selected.src_idx, selected.dst_idx))
            carved_count += 1
            note = (
                f"attempt={attempt_index} roll={roll:.6f} selected R{selected.src_idx} -> R{selected.dst_idx} "
                f"{selected.axis_name} candidate={selected.candidate_index} score={selected.score} "
                f"pairBestCount={len(pair_best_candidates)} checked_pairs={checked_pairs} "
                f"skipped_connected_pairs={skipped_connected_pairs} cells={selected.path_len} "
                f"overlap={selected.overlap} parallel={selected.parallel_run} "
                f"centerDistSq={selected.center_dist_sq} requestedPerPair={self.s.extra_candidate_count} "
                f"carvedExtrasSoFar={carved_count}"
            )
            overlays = self.build_extra_candidate_overlays(pair_best_candidates, selected)
            note += f"\nshown_candidates={max(0, len(overlays) - 1)} pair-best 후보 + selected"
            self.snapshot(
                f"05-{attempt_index + 1:02d}. EXTRA 선택 통로 R{selected.src_idx} -> R{selected.dst_idx}",
                selected.path, note, extra_paths=overlays
            )

        if carved_count == 0:
            self.snapshot("05. EXTRA 최종 결과", note=f"carved=0 attempts={max_attempts}")
    def build_extra_path_candidates_for_pair(
        self,
        src_idx: int,
        dst_idx: int,
        dist_sq: int,
        attempt_index: int,
        path_buf: List[Tuple[int, int]],
        candidates: List[ExtraCorridorCandidate],
        rejected_notes: List[str],
    ) -> None:
        src = self.rooms[src_idx]
        dst = self.rooms[dst_idx]
        primary_horiz_first = abs(dst.cx - src.cx) >= abs(dst.cy - src.cy)

        for candidate_index in range(self.s.extra_candidate_count):
            primary_path = (candidate_index % 2) == 0
            horiz_first = primary_horiz_first if primary_path else not primary_horiz_first
            self.emit_extra_path_candidate(src, dst, horiz_first, candidate_index, path_buf)
            self.try_add_extra_candidate(
                src_idx, dst_idx, dist_sq, primary_path, attempt_index, candidate_index,
                path_buf, candidates, rejected_notes
            )

    def try_add_extra_candidate(
        self,
        src_idx: int,
        dst_idx: int,
        dist_sq: int,
        primary_path: bool,
        attempt_index: int,
        candidate_index: int,
        path_buf: List[Tuple[int, int]],
        candidates: List[ExtraCorridorCandidate],
        rejected_notes: List[str],
    ) -> None:
        reject = self.corridor_candidate_reject_reason(path_buf, src_idx, dst_idx, False)
        if reject is not None:
            if len(rejected_notes) < 128:
                rejected_notes.append(f"R{src_idx}->R{dst_idx} candidate={candidate_index}: reject={reject}")
            return

        if self.extra_candidate_path_already_added(candidates, path_buf):
            return

        path = path_buf[:]
        path_len = len(path)
        overlap = self.count_corridor_overlap(path)
        parallel_run = self.longest_parallel_corridor_run(path)
        score = self.score_extra_corridor_candidate(path_len, dist_sq, overlap, parallel_run)
        candidates.append(ExtraCorridorCandidate(
            src_idx=src_idx,
            dst_idx=dst_idx,
            horiz_first=primary_path,
            path=path,
            extra_attempt_index=attempt_index,
            candidate_index=candidate_index,
            primary_path=primary_path,
            score=score,
            overlap=overlap,
            path_len=path_len,
            center_dist_sq=dist_sq,
            parallel_run=parallel_run,
            axis_name="primary" if primary_path else "alternate",
        ))

    @staticmethod
    def select_best_extra_corridor_candidate(candidates: List[ExtraCorridorCandidate]) -> ExtraCorridorCandidate:
        best = candidates[0]
        for candidate in candidates[1:]:
            if DungeonGenerator.is_better_extra_corridor_candidate(candidate, best):
                best = candidate
        return best

    @staticmethod
    def is_better_extra_corridor_candidate(a: ExtraCorridorCandidate, b: ExtraCorridorCandidate) -> bool:
        if a.score != b.score:
            return a.score > b.score
        if a.path_len != b.path_len:
            return a.path_len < b.path_len
        if a.overlap != b.overlap:
            return a.overlap > b.overlap
        if a.center_dist_sq != b.center_dist_sq:
            return a.center_dist_sq < b.center_dist_sq
        if a.src_idx != b.src_idx:
            return a.src_idx < b.src_idx
        if a.dst_idx != b.dst_idx:
            return a.dst_idx < b.dst_idx
        if a.candidate_index != b.candidate_index:
            return a.candidate_index < b.candidate_index
        if a.extra_attempt_index != b.extra_attempt_index:
            return a.extra_attempt_index < b.extra_attempt_index
        if a.primary_path != b.primary_path:
            return a.primary_path
        return False

    def emit_extra_path_candidate(
        self, src: Room, dst: Room, horiz_first: bool,
        candidate_index: int, path: List[Tuple[int, int]]
    ) -> None:
        variant = candidate_index // 2
        if variant == 0:
            self.emit_l_corridor_path(src, dst, horiz_first, path)
            return

        if horiz_first:
            sy = self.pick_extra_axis(src.y, src.y + src.h, src.cy, dst.cy, dst.y, dst.y + dst.h, variant)
            ey = self.pick_extra_axis(dst.y, dst.y + dst.h, dst.cy, src.cy, src.y, src.y + src.h, variant)
            self.emit_l_corridor_path_with_axes(src, dst, True, sy, ey, path)
        else:
            sx = self.pick_extra_axis(src.x, src.x + src.w, src.cx, dst.cx, dst.x, dst.x + dst.w, variant)
            ex = self.pick_extra_axis(dst.x, dst.x + dst.w, dst.cx, src.cx, src.x, src.x + src.w, variant)
            self.emit_l_corridor_path_with_axes(src, dst, False, sx, ex, path)

    @staticmethod
    def pick_extra_axis(start: int, end: int, center: int, target_center: int,
                        other_start: int, other_end: int, variant: int) -> int:
        overlap_start = max(start, other_start)
        overlap_end = min(end, other_end)
        if variant == 1 and overlap_start < overlap_end:
            return max(start, min(end - 1, (overlap_start + overlap_end - 1) // 2))

        mod = variant % 6
        if mod == 1:
            value = target_center
        elif mod == 2:
            value = center - max(1, (end - start) // 4)
        elif mod == 3:
            value = center + max(1, (end - start) // 4)
        elif mod == 4:
            value = start
        elif mod == 5:
            value = end - 1
        else:
            value = center
        return max(start, min(end - 1, value))

    def emit_l_corridor_path_with_axes(
        self, src: Room, dst: Room, horiz_first: bool,
        src_axis: int, dst_axis: int, path: List[Tuple[int, int]]
    ) -> None:
        path.clear()
        MIN = self.s.min_straight
        dx = dst.cx - src.cx
        dy = dst.cy - src.cy
        if horiz_first:
            if dx >= 0:
                door_sx, far_sx, door_ex, far_ex, step_s, step_e = src.x + src.w - 1, src.x + src.w + MIN - 1, dst.x, dst.x - MIN, 1, -1
            else:
                door_sx, far_sx, door_ex, far_ex, step_s, step_e = src.x, src.x - MIN, dst.x + dst.w - 1, dst.x + dst.w + MIN - 1, -1, 1
            sy = self.clamp_door_axis(src_axis, src.y, src.y + src.h)
            ey = self.clamp_door_axis(dst_axis, dst.y, dst.y + dst.h)
            self.emit_h(door_sx + step_s, far_sx, sy, path)
            self.emit_h(door_ex + step_e, far_ex, ey, path)
            self.emit_h(far_sx, far_ex, sy, path)
            self.emit_v(sy, ey, far_ex, path)
        else:
            if dy >= 0:
                door_sy, far_sy, door_ey, far_ey, step_s, step_e = src.y + src.h - 1, src.y + src.h + MIN - 1, dst.y, dst.y - MIN, 1, -1
            else:
                door_sy, far_sy, door_ey, far_ey, step_s, step_e = src.y, src.y - MIN, dst.y + dst.h - 1, dst.y + dst.h + MIN - 1, -1, 1
            sx = self.clamp_door_axis(src_axis, src.x, src.x + src.w)
            ex = self.clamp_door_axis(dst_axis, dst.x, dst.x + dst.w)
            self.emit_v(door_sy + step_s, far_sy, sx, path)
            self.emit_v(door_ey + step_e, far_ey, ex, path)
            self.emit_v(far_sy, far_ey, sx, path)
            self.emit_h(sx, ex, far_ey, path)

    @staticmethod
    def extra_candidate_path_already_added(candidates: List[ExtraCorridorCandidate], path: List[Tuple[int, int]]) -> bool:
        for existing in candidates:
            if len(existing.path) != len(path):
                continue
            if all(existing.path[i] == path[i] for i in range(len(path))):
                return True
        return False

    @staticmethod
    def score_extra_corridor_candidate(path_len: int, center_dist_sq: int, overlap: int, parallel_run: int) -> int:
        return overlap * 60 - path_len * 8 - center_dist_sq // 20 - parallel_run * 25

    def longest_parallel_corridor_run(self, path: List[Tuple[int, int]]) -> int:
        longest = 0
        current = 0
        for x, y in path:
            parallel = self.in_bounds(x, y) and self.grid[y][x] != CORRIDOR and self.has_adjacent_corridor(x, y)
            if parallel:
                current += 1
                longest = max(longest, current)
            else:
                current = 0
        return longest

    def has_adjacent_corridor(self, x: int, y: int) -> bool:
        return (
            (self.in_bounds(x + 1, y) and self.grid[y][x + 1] == CORRIDOR) or
            (self.in_bounds(x - 1, y) and self.grid[y][x - 1] == CORRIDOR) or
            (self.in_bounds(x, y + 1) and self.grid[y + 1][x] == CORRIDOR) or
            (self.in_bounds(x, y - 1) and self.grid[y - 1][x] == CORRIDOR)
        )

    @staticmethod
    def center_dist_sq(a: Room, b: Room) -> int:
        dx = b.cx - a.cx
        dy = b.cy - a.cy
        return dx * dx + dy * dy

    def count_corridor_overlap(self, path: List[Tuple[int, int]]) -> int:
        count = 0
        for x, y in path:
            if self.in_bounds(x, y) and self.grid[y][x] == CORRIDOR:
                count += 1
        return count

    def measure_adjacent_parallel_run(self, path: List[Tuple[int, int]], src_idx: int, dst_idx: int) -> int:
        max_distance_from_perimeter = 2
        side_padding = max(1, self.s.min_straight)
        path_set = set(path)
        best = 0
        for i, r in enumerate(self.rooms):
            if i == src_idx or i == dst_idx:
                continue
            x_start = r.x - side_padding
            x_end = r.x + r.w + side_padding
            y_start = r.y - side_padding
            y_end = r.y + r.h + side_padding
            for distance in range(1, max_distance_from_perimeter + 1):
                best = max(best, self.horizontal_run_length(path_set, r.y - 1 - distance, x_start, x_end))
                best = max(best, self.horizontal_run_length(path_set, r.y + r.h + distance, x_start, x_end))
                best = max(best, self.vertical_run_length(path_set, r.x - 1 - distance, y_start, y_end))
                best = max(best, self.vertical_run_length(path_set, r.x + r.w + distance, y_start, y_end))
        return best

    @staticmethod
    def horizontal_run_length(path_set: Set[Tuple[int, int]], y: int, x_start: int, x_end: int) -> int:
        best = 0
        run = 0
        for x in range(x_start, x_end):
            if (x, y) in path_set:
                run += 1
                best = max(best, run)
            else:
                run = 0
        return best

    @staticmethod
    def vertical_run_length(path_set: Set[Tuple[int, int]], x: int, y_start: int, y_end: int) -> int:
        best = 0
        run = 0
        for y in range(y_start, y_end):
            if (x, y) in path_set:
                run += 1
                best = max(best, run)
            else:
                run = 0
        return best

    @staticmethod
    def euclidean_dist(a: Room, b: Room) -> float:
        dx = b.cx - a.cx
        dy = b.cy - a.cy
        return math.sqrt(dx * dx + dy * dy)

    def draw_l_corridor(self, src_idx: int, dst_idx: int, is_mandatory: bool, path_buf: List[Tuple[int, int]]):
        src = self.rooms[src_idx]
        dst = self.rooms[dst_idx]
        dx = dst.cx - src.cx
        dy = dst.cy - src.cy
        primary_horiz_first = abs(dx) >= abs(dy)

        self.emit_l_corridor_path(src, dst, primary_horiz_first, path_buf)
        primary_path = path_buf[:]
        primary_reject = self.corridor_candidate_reject_reason(primary_path, src_idx, dst_idx, is_mandatory)
        if primary_reject is None:
            self.carve_path(primary_path)
            return True, primary_path, "carve=primary(clean)"

        self.emit_l_corridor_path(src, dst, not primary_horiz_first, path_buf)
        alternate_path = path_buf[:]
        alternate_reject = self.corridor_candidate_reject_reason(alternate_path, src_idx, dst_idx, is_mandatory)
        if alternate_reject is None:
            self.carve_path(alternate_path)
            return True, alternate_path, f"carve=alternate(clean, primary-reject={primary_reject})"

        if is_mandatory:
            self.carve_path(primary_path)
            return True, primary_path, f"carve=primary FALLBACK(primary={primary_reject}, alternate={alternate_reject}, mandatory)"
        return False, alternate_path, f"carve=SKIP(primary={primary_reject}, alternate={alternate_reject}, extra)"

    def emit_l_corridor_path(self, src: Room, dst: Room, horiz_first: bool, path: List[Tuple[int, int]]) -> None:
        path.clear()
        MIN = self.s.min_straight
        dx = dst.cx - src.cx
        dy = dst.cy - src.cy
        if horiz_first:
            if dx >= 0:
                door_sx, far_sx, door_ex, far_ex, step_s, step_e = src.x + src.w - 1, src.x + src.w + MIN - 1, dst.x, dst.x - MIN, 1, -1
            else:
                door_sx, far_sx, door_ex, far_ex, step_s, step_e = src.x, src.x - MIN, dst.x + dst.w - 1, dst.x + dst.w + MIN - 1, -1, 1
            sy, ey = src.cy, dst.cy
            if abs(sy - ey) < MIN:
                used, sy2, ey2 = self.try_use_shared_door_axis(src.y, src.y + src.h, dst.y, dst.y + dst.h, sy)
                if used:
                    sy, ey = sy2, ey2
                else:
                    ey_dir = 1 if ey >= sy else -1
                    ey = max(dst.y, min(dst.y + dst.h - 1, sy + ey_dir * MIN))
                    if abs(sy - ey) < MIN:
                        sy = max(src.y, min(src.y + src.h - 1, ey - ey_dir * MIN))
            sy = self.clamp_door_axis(sy, src.y, src.y + src.h)
            ey = self.clamp_door_axis(ey, dst.y, dst.y + dst.h)
            self.emit_h(door_sx + step_s, far_sx, sy, path)
            self.emit_h(door_ex + step_e, far_ex, ey, path)
            self.emit_h(far_sx, far_ex, sy, path)
            self.emit_v(sy, ey, far_ex, path)
        else:
            if dy >= 0:
                door_sy, far_sy, door_ey, far_ey, step_s, step_e = src.y + src.h - 1, src.y + src.h + MIN - 1, dst.y, dst.y - MIN, 1, -1
            else:
                door_sy, far_sy, door_ey, far_ey, step_s, step_e = src.y, src.y - MIN, dst.y + dst.h - 1, dst.y + dst.h + MIN - 1, -1, 1
            sx, ex = src.cx, dst.cx
            if abs(sx - ex) < MIN:
                used, sx2, ex2 = self.try_use_shared_door_axis(src.x, src.x + src.w, dst.x, dst.x + dst.w, sx)
                if used:
                    sx, ex = sx2, ex2
                else:
                    ex_dir = 1 if ex >= sx else -1
                    ex = max(dst.x, min(dst.x + dst.w - 1, sx + ex_dir * MIN))
                    if abs(sx - ex) < MIN:
                        sx = max(src.x, min(src.x + src.w - 1, ex - ex_dir * MIN))
            sx = self.clamp_door_axis(sx, src.x, src.x + src.w)
            ex = self.clamp_door_axis(ex, dst.x, dst.x + dst.w)
            self.emit_v(door_sy + step_s, far_sy, sx, path)
            self.emit_v(door_ey + step_e, far_ey, ex, path)
            self.emit_v(far_sy, far_ey, sx, path)
            self.emit_h(sx, ex, far_ey, path)

    @staticmethod
    def emit_h(x0: int, x1: int, y: int, path: List[Tuple[int, int]]) -> None:
        step = 1 if x1 >= x0 else -1
        for x in range(x0, x1 + step, step):
            path.append((x, y))

    @staticmethod
    def emit_v(y0: int, y1: int, x: int, path: List[Tuple[int, int]]) -> None:
        step = 1 if y1 >= y0 else -1
        for y in range(y0, y1 + step, step):
            path.append((x, y))

    @staticmethod
    def clamp_int(value: int, min_value: int, max_value: int) -> int:
        return max(min_value, min(max_value, value))

    @staticmethod
    def clamp_door_axis(value: int, start: int, end: int) -> int:
        # Mirrors DungeonGenerator.ClampDoorAxis. Right/Bottom are exclusive;
        # avoid corner doorway anchors when the side has interior cells.
        if end - start <= 2:
            return DungeonGenerator.clamp_int(value, start, end - 1)
        return DungeonGenerator.clamp_int(value, start + 1, end - 2)

    @staticmethod
    def try_use_shared_door_axis(src_start: int, src_end: int, dst_start: int, dst_end: int, preferred: int):
        overlap_start = max(src_start, dst_start)
        overlap_end = min(src_end, dst_end)
        if overlap_start >= overlap_end:
            return False, preferred, preferred
        shared = max(overlap_start, min(overlap_end - 1, preferred))
        return True, shared, shared

    def carve_path(self, path: List[Tuple[int, int]]) -> None:
        for x, y in path:
            self.set_tile(x, y)

    def set_tile(self, x: int, y: int) -> None:
        if not self.in_bounds(x, y):
            return
        if self.grid[y][x] != ROOM:
            self.grid[y][x] = CORRIDOR
        self.corridor_tiles.add((x, y))

    def is_corridor_candidate_clean(self, path: List[Tuple[int, int]], src_idx: int, dst_idx: int, is_mandatory: bool) -> bool:
        return self.corridor_candidate_reject_reason(path, src_idx, dst_idx, is_mandatory) is None

    def corridor_candidate_reject_reason(self, path: List[Tuple[int, int]], src_idx: int, dst_idx: int, is_mandatory: bool) -> Optional[str]:
        # Mirrors current DungeonGenerator.IsCorridorCandidateClean ordering.
        # Room-perimeter/corner-doorway checks are EXTRA-only for connectivity safety.
        if not is_mandatory and self.path_carves_room_perimeter(path, src_idx, dst_idx):
            return "room-perimeter-corridor"
        if not is_mandatory and self.path_uses_room_corner_doorway(path, src_idx, dst_idx):
            return "corner-doorway"
        if self.path_hits_third_room(path, src_idx, dst_idx):
            return "third-room"
        if self.path_creates_bad_door_run(path):
            return "bad-door-run"
        if self.path_creates_orphan_door_stub(path):
            return "orphan-door-stub"
        if not is_mandatory and self.path_creates_outward_room_stub(path, src_idx, dst_idx):
            return "outward-room-stub"
        if not is_mandatory and self.path_creates_diagonal_room_stub(path, src_idx, dst_idx):
            return "diagonal-room-stub"
        if not is_mandatory and self.path_creates_third_room_parallel_run(path, src_idx, dst_idx):
            return "third-room-parallel"
        return None

    def path_carves_room_perimeter(self, path: List[Tuple[int, int]], src_idx: int, dst_idx: int) -> bool:
        for x, y in path:
            for r in self.rooms:
                if self.is_room_perimeter_cell(x, y, r):
                    return True
        return False

    @staticmethod
    def is_room_perimeter_cell(x: int, y: int, room: Room) -> bool:
        if x < room.x or x >= room.x + room.w or y < room.y or y >= room.y + room.h:
            return False
        return x == room.x or x == room.x + room.w - 1 or y == room.y or y == room.y + room.h - 1

    def path_uses_room_corner_doorway(self, path: List[Tuple[int, int]], src_idx: int, dst_idx: int) -> bool:
        return (
            self.path_uses_room_corner_doorway_for_room(path, self.rooms[src_idx])
            or self.path_uses_room_corner_doorway_for_room(path, self.rooms[dst_idx])
        )

    def path_uses_room_corner_doorway_for_room(self, path: List[Tuple[int, int]], room: Room) -> bool:
        for x, y in path:
            result = self.try_get_door_anchor_from_adjacent_cell(x, y, room)
            if result is None:
                continue
            side, anchor_x, anchor_y = result
            if self.is_corner_door_anchor(anchor_x, anchor_y, room, side):
                return True
        return False

    @staticmethod
    def try_get_door_anchor_from_adjacent_cell(x: int, y: int, room: Room):
        if x == room.x - 1 and room.y <= y < room.y + room.h:
            return "left", room.x, y
        if x == room.x + room.w and room.y <= y < room.y + room.h:
            return "right", room.x + room.w - 1, y
        if y == room.y - 1 and room.x <= x < room.x + room.w:
            return "top", x, room.y
        if y == room.y + room.h and room.x <= x < room.x + room.w:
            return "bottom", x, room.y + room.h - 1
        return None

    @staticmethod
    def is_corner_door_anchor(anchor_x: int, anchor_y: int, room: Room, side: str) -> bool:
        if side in ("left", "right"):
            return anchor_y == room.y or anchor_y == room.y + room.h - 1
        return anchor_x == room.x or anchor_x == room.x + room.w - 1

    def path_hits_third_room(self, path: List[Tuple[int, int]], src_idx: int, dst_idx: int) -> bool:
        for x, y in path:
            for k, r in enumerate(self.rooms):
                if k == src_idx or k == dst_idx:
                    continue
                xL, xR, yT, yB = r.x, r.x + r.w, r.y, r.y + r.h
                xL2, xR2, yT2, yB2 = r.x - 2, r.x + r.w + 1, r.y - 2, r.y + r.h + 1
                if xL <= x < xR and yT <= y < yB:
                    return True
                if (y == yT - 1 or y == yB) and xL <= x < xR:
                    return True
                if (x == xL - 1 or x == xR) and yT <= y < yB:
                    return True
                if (y == yT2 or y == yB2) and xL <= x < xR:
                    return True
                if (x == xL2 or x == xR2) and yT <= y < yB:
                    return True
        return False

    def path_creates_bad_door_run(self, path: List[Tuple[int, int]]) -> bool:
        for r in self.rooms:
            if self.side_has_bad_door_run(path, r.y - 1, True, r.x, r.x + r.w):
                return True
            if self.side_has_bad_door_run(path, r.y + r.h, True, r.x, r.x + r.w):
                return True
            if self.side_has_bad_door_run(path, r.x - 1, False, r.y, r.y + r.h):
                return True
            if self.side_has_bad_door_run(path, r.x + r.w, False, r.y, r.y + r.h):
                return True
        return False

    def side_has_bad_door_run(self, path: List[Tuple[int, int]], fixed_coord: int, horizontal: bool, range_start: int, range_end: int) -> bool:
        run_len = 0
        for k in range(range_start, range_end):
            x = k if horizontal else fixed_coord
            y = fixed_coord if horizontal else k
            is_door = self.in_bounds(x, y) and (self.grid[y][x] == CORRIDOR or self.path_would_carve_corridor(path, x, y))
            if is_door:
                run_len += 1
                if run_len > 1:
                    return True
            else:
                run_len = 0
        return False

    def path_creates_orphan_door_stub(self, path: List[Tuple[int, int]]) -> bool:
        for x, y in path:
            if not self.in_bounds(x, y) or self.grid[y][x] == ROOM:
                continue
            for r in self.rooms:
                if self.is_orphan_door_on_side(path, x, y, r.y - 1, True, r.x, r.x + r.w, 0, -1):
                    return True
                if self.is_orphan_door_on_side(path, x, y, r.y + r.h, True, r.x, r.x + r.w, 0, 1):
                    return True
                if self.is_orphan_door_on_side(path, x, y, r.x - 1, False, r.y, r.y + r.h, -1, 0):
                    return True
                if self.is_orphan_door_on_side(path, x, y, r.x + r.w, False, r.y, r.y + r.h, 1, 0):
                    return True
        return False

    def is_orphan_door_on_side(self, path: List[Tuple[int, int]], x: int, y: int, fixed_coord: int, horizontal: bool,
                               range_start: int, range_end: int, outward_dx: int, outward_dy: int) -> bool:
        side_k = x if horizontal else y
        side_fixed = y if horizontal else x
        if side_fixed != fixed_coord or side_k < range_start or side_k >= range_end:
            return False
        ox = x + outward_dx
        oy = y + outward_dy
        if not self.in_bounds(ox, oy):
            return True
        if self.grid[oy][ox] == CORRIDOR:
            return False
        return not self.path_would_carve_corridor(path, ox, oy)

    def path_creates_diagonal_room_stub(self, path: List[Tuple[int, int]], src_idx: int, dst_idx: int) -> bool:
        """Port of DungeonGenerator.PathCreatesDiagonalRoomStub.

        This guard is used only for EXTRA corridors.
        When two rooms are separated on both X and Y, an L path that touches
        both a top/bottom side and a left/right side of either endpoint room
        tends to create a visually awkward diagonal corner stub, so Unity now
        rejects that optional candidate.
        """
        src = self.rooms[src_idx]
        dst = self.rooms[dst_idx]

        separated_x = src.x + src.w <= dst.x or dst.x + dst.w <= src.x
        separated_y = src.y + src.h <= dst.y or dst.y + dst.h <= src.y
        if not separated_x or not separated_y:
            return False

        if self.path_touches_top_or_bottom_side(path, src) and self.path_touches_left_or_right_side(path, src):
            return True
        if self.path_touches_top_or_bottom_side(path, dst) and self.path_touches_left_or_right_side(path, dst):
            return True

        return False

    def path_creates_outward_room_stub(self, path: List[Tuple[int, int]], src_idx: int, dst_idx: int) -> bool:
        """Port of DungeonGenerator.PathCreatesOutwardRoomStub.

        This guard is used only for EXTRA corridors.
        - X-separated rooms with useful Y-overlap should connect through left/right sides.
          If the candidate touches top/bottom perimeter sides on both rooms, reject it.
        - Y-separated rooms with useful X-overlap should connect through top/bottom sides.
          If the candidate touches left/right perimeter sides on both rooms, reject it.
        """
        src = self.rooms[src_idx]
        dst = self.rooms[dst_idx]

        overlap_x = self.overlap_length(src.x, src.x + src.w, dst.x, dst.x + dst.w)
        overlap_y = self.overlap_length(src.y, src.y + src.h, dst.y, dst.y + dst.h)

        separated_x = src.x + src.w <= dst.x or dst.x + dst.w <= src.x
        separated_y = src.y + src.h <= dst.y or dst.y + dst.h <= src.y
        min_useful_overlap = max(1, self.s.min_straight)

        if separated_x and overlap_y >= min_useful_overlap:
            src_uses_vertical_detour_side = self.path_touches_top_or_bottom_side(path, src)
            dst_uses_vertical_detour_side = self.path_touches_top_or_bottom_side(path, dst)
            if src_uses_vertical_detour_side and dst_uses_vertical_detour_side:
                return True

        if separated_y and overlap_x >= min_useful_overlap:
            src_uses_horizontal_detour_side = self.path_touches_left_or_right_side(path, src)
            dst_uses_horizontal_detour_side = self.path_touches_left_or_right_side(path, dst)
            if src_uses_horizontal_detour_side and dst_uses_horizontal_detour_side:
                return True

        return False

    @staticmethod
    def overlap_length(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
        return max(0, min(a_end, b_end) - max(a_start, b_start))

    @staticmethod
    def path_touches_top_or_bottom_side(path: List[Tuple[int, int]], room: Room) -> bool:
        for x, y in path:
            on_top_or_bottom = y == room.y - 1 or y == room.y + room.h
            if on_top_or_bottom and room.x <= x < room.x + room.w:
                return True
        return False

    @staticmethod
    def path_touches_left_or_right_side(path: List[Tuple[int, int]], room: Room) -> bool:
        for x, y in path:
            on_left_or_right = x == room.x - 1 or x == room.x + room.w
            if on_left_or_right and room.y <= y < room.y + room.h:
                return True
        return False

    def path_creates_third_room_parallel_run(self, path: List[Tuple[int, int]], src_idx: int, dst_idx: int) -> bool:
        max_distance_from_perimeter = 2
        min_parallel_run_length = 3
        side_padding = max(1, self.s.min_straight)
        path_set = set(path)
        for i, r in enumerate(self.rooms):
            if i == src_idx or i == dst_idx:
                continue
            x_start = r.x - side_padding
            x_end = r.x + r.w + side_padding
            y_start = r.y - side_padding
            y_end = r.y + r.h + side_padding
            for distance in range(1, max_distance_from_perimeter + 1):
                if self.path_has_horizontal_run(path_set, r.y - 1 - distance, x_start, x_end, min_parallel_run_length):
                    return True
                if self.path_has_horizontal_run(path_set, r.y + r.h + distance, x_start, x_end, min_parallel_run_length):
                    return True
                if self.path_has_vertical_run(path_set, r.x - 1 - distance, y_start, y_end, min_parallel_run_length):
                    return True
                if self.path_has_vertical_run(path_set, r.x + r.w + distance, y_start, y_end, min_parallel_run_length):
                    return True
        return False

    @staticmethod
    def path_has_horizontal_run(path_set: Set[Tuple[int, int]], y: int, x_start: int, x_end: int, min_run_length: int) -> bool:
        run_len = 0
        for x in range(x_start, x_end):
            if (x, y) in path_set:
                run_len += 1
                if run_len >= min_run_length:
                    return True
            else:
                run_len = 0
        return False

    @staticmethod
    def path_has_vertical_run(path_set: Set[Tuple[int, int]], x: int, y_start: int, y_end: int, min_run_length: int) -> bool:
        run_len = 0
        for y in range(y_start, y_end):
            if (x, y) in path_set:
                run_len += 1
                if run_len >= min_run_length:
                    return True
            else:
                run_len = 0
        return False

    def path_would_carve_corridor(self, path: List[Tuple[int, int]], x: int, y: int) -> bool:
        if not self.in_bounds(x, y):
            return False
        if self.grid[y][x] == ROOM:
            return False
        return (x, y) in path

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.s.map_width and 0 <= y < self.s.map_height

    def place_stairs(self) -> None:
        if not self.rooms or self.s.floor >= self.s.max_floor:
            return
        indices = list(range(len(self.rooms)))
        for i in range(len(indices) - 1, 0, -1):
            j = self.rng.next(i + 1)
            indices[i], indices[j] = indices[j], indices[i]
        for idx in indices:
            ok, sx, sy = self.try_find_stair_pos(self.rooms[idx])
            if ok:
                self.grid[sy][sx] = STAIR_UP
                self.snapshot(f"06. 계단 배치 R{idx}", note=f"STAIR_UP=({sx}, {sy})")
                break

    def try_find_stair_pos(self, room: Room):
        candidates: List[Tuple[int, int]] = []
        for row in range(room.y + 1, room.y + room.h - 1):
            for col in range(room.x + 1, room.x + room.w - 1):
                if self.is_valid_stair_pos(col, row):
                    candidates.append((col, row))
        if not candidates:
            return False, -1, -1
        sx, sy = candidates[self.rng.next(len(candidates))]
        return True, sx, sy

    def is_valid_stair_pos(self, x: int, y: int) -> bool:
        if self.grid[y][x] != ROOM:
            return False
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            nx, ny = x + dx, y + dy
            if not self.in_bounds(nx, ny):
                continue
            if self.grid[ny][nx] == CORRIDOR:
                return False
        return True


# -----------------------------
# Tkinter UI
# -----------------------------
class DungeonViewer(tk.Tk):
    COLORS: Dict[int, str] = {
        EMPTY: "#111217",
        ROOM: "#6c8f4e",
        CORRIDOR: "#b88a48",
        STAIR_UP: "#53d6ff",
        DOOR_CLOSED: "#8c5b2f",
    }

    def __init__(self):
        super().__init__()
        self.title("Proto_JBRL Dungeon Step Viewer - latest DungeonGenerator parity build + scored EXTRA corridor")
        self.geometry("1180x760")
        self.minsize(980, 660)
        self.steps: List[Step] = []
        self.index = 0
        self.cell = 10
        self.show_bsp = tk.BooleanVar(value=True)
        self.show_rooms = tk.BooleanVar(value=True)
        self.show_path = tk.BooleanVar(value=True)
        self.seed_var = tk.StringVar(value="283321776792")
        self.floor_var = tk.StringVar(value="3")
        self.map_w_var = tk.StringVar(value="80")
        self.map_h_var = tk.StringVar(value="50")
        self.min_room_var = tk.StringVar(value="5")
        self.max_room_var = tk.StringVar(value="14")
        self.bsp_depth_var = tk.StringVar(value="4")
        self.padding_var = tk.StringVar(value="2")
        self.extra_conn_var = tk.StringVar(value="0.5")
        self.extra_candidate_count_var = tk.StringVar(value="12")
        self.min_straight_var = tk.StringVar(value="2")
        self.max_floor_var = tk.StringVar(value="100")
        self.status_var = tk.StringVar(value="")
        self.is_playing = False
        self.play_delay_ms = 180
        self.play_button: Optional[ttk.Button] = None
        self._play_after_id: Optional[str] = None
        self._scale_after_id: Optional[str] = None
        self._scale_target_index: Optional[int] = None
        self._setting_scale = False
        self._draw_cache: Dict[Tuple[int, int, bool, bool, bool], ImageTk.PhotoImage] = {}
        self._draw_cache_order: List[Tuple[int, int, bool, bool, bool]] = []
        self._photo_ref: Optional[ImageTk.PhotoImage] = None
        self._build_ui()
        self.bind("<Left>", lambda e: self.prev_step())
        self.bind("<Right>", lambda e: self.next_step())
        self.bind("<Home>", lambda e: self.goto_step(0))
        self.bind("<End>", lambda e: self.goto_step(len(self.steps) - 1))
        self.bind("<Return>", lambda e: self.generate())
        self.bind("<space>", lambda e: self.toggle_play())
        self.generate()

    def _add_labeled_entry(self, parent: ttk.Frame, label: str, var: tk.StringVar, width: int) -> None:
        ttk.Label(parent, text=label).pack(side=tk.LEFT)
        ttk.Entry(parent, width=width, textvariable=var).pack(side=tk.LEFT, padx=(4, 10))

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=(8, 8, 8, 2))
        top.pack(fill=tk.X)
        self._add_labeled_entry(top, "Seed", self.seed_var, 18)
        self._add_labeled_entry(top, "Floor", self.floor_var, 6)
        ttk.Button(top, text="Generate", command=self.generate).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Button(top, text="◀ Prev", command=self.prev_step).pack(side=tk.LEFT)
        ttk.Button(top, text="Next ▶", command=self.next_step).pack(side=tk.LEFT, padx=(4, 12))
        ttk.Checkbutton(top, text="BSP", variable=self.show_bsp, command=self.draw).pack(side=tk.LEFT)
        ttk.Checkbutton(top, text="Rooms", variable=self.show_rooms, command=self.draw).pack(side=tk.LEFT)
        ttk.Checkbutton(top, text="Path", variable=self.show_path, command=self.draw).pack(side=tk.LEFT)
        ttk.Label(top, text="  ← / →: step, Space: play/pause, Enter: regenerate").pack(side=tk.LEFT, padx=(12, 0))

        settings_box = ttk.LabelFrame(self, text="Unity DungeonSettings", padding=(8, 4, 8, 6))
        settings_box.pack(fill=tk.X, padx=8, pady=(2, 8))
        settings_row1 = ttk.Frame(settings_box)
        settings_row1.pack(fill=tk.X, pady=(0, 4))
        settings_row2 = ttk.Frame(settings_box)
        settings_row2.pack(fill=tk.X)
        self._add_labeled_entry(settings_row1, "Map Width", self.map_w_var, 5)
        self._add_labeled_entry(settings_row1, "Map Height", self.map_h_var, 5)
        self._add_labeled_entry(settings_row1, "Min Room Size", self.min_room_var, 4)
        self._add_labeled_entry(settings_row1, "Max Room Size", self.max_room_var, 4)
        self._add_labeled_entry(settings_row1, "Bsp Depth", self.bsp_depth_var, 4)
        self._add_labeled_entry(settings_row2, "Padding", self.padding_var, 4)
        self._add_labeled_entry(settings_row2, "Extra Conn Prob", self.extra_conn_var, 5)
        self._add_labeled_entry(settings_row2, "Extra Candidate Count", self.extra_candidate_count_var, 4)
        self._add_labeled_entry(settings_row2, "MinStraight", self.min_straight_var, 4)
        self._add_labeled_entry(settings_row2, "MaxFloor", self.max_floor_var, 5)

        self.canvas = tk.Canvas(self, bg="#1b1d22", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        bottom = ttk.Frame(self, padding=(8, 0, 8, 8))
        bottom.pack(fill=tk.X)
        ttk.Button(bottom, text="◀", width=3, command=self.prev_step).pack(side=tk.LEFT, padx=(0, 4))
        self.play_button = ttk.Button(bottom, text="▶ Play", width=9, command=self.toggle_play)
        self.play_button.pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(bottom, text="▶", width=3, command=self.next_step).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(bottom, text="Timeline").pack(side=tk.LEFT, padx=(0, 6))
        self.scale = ttk.Scale(bottom, from_=0, to=0, orient=tk.HORIZONTAL, command=self.on_scale)
        self.scale.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(bottom, textvariable=self.status_var, width=62).pack(side=tk.RIGHT, padx=(8, 0))

    def generate(self) -> None:
        self.stop_playback()
        try:
            seed_text = self.seed_var.get().strip()
            seed = unity_build_settings_seed_from_text(seed_text) if seed_text else None
            floor = int(self.floor_var.get().strip())
            settings = DungeonSettings(
                map_width=int(self.map_w_var.get().strip()),
                map_height=int(self.map_h_var.get().strip()),
                min_room_size=int(self.min_room_var.get().strip()),
                max_room_size=int(self.max_room_var.get().strip()),
                bsp_depth=int(self.bsp_depth_var.get().strip()),
                padding=int(self.padding_var.get().strip()),
                extra_conn_prob=float(self.extra_conn_var.get().strip()),
                extra_candidate_count=int(self.extra_candidate_count_var.get().strip()),
                min_straight=int(self.min_straight_var.get().strip()),
                max_floor=int(self.max_floor_var.get().strip()),
                seed=seed,
                floor=floor,
            )
            gen = DungeonGenerator(settings)
            self.steps = gen.generate()
            self._clear_draw_cache()
            self.index = 0
            self.scale.configure(to=max(0, len(self.steps) - 1))
            self.scale.set(0)
            self.draw()
        except Exception as ex:
            messagebox.showerror("Generate failed", str(ex))

    def on_scale(self, value: str) -> None:
        if not self.steps or self._setting_scale:
            return
        # ttk.Scale fires many callbacks while dragging. Rendering each callback
        # makes the UI stutter, so coalesce them into one draw per UI frame.
        self._scale_target_index = max(0, min(len(self.steps) - 1, int(float(value))))
        if self._scale_after_id is not None:
            try:
                self.after_cancel(self._scale_after_id)
            except Exception:
                pass
        self._scale_after_id = self.after(16, self._apply_scale_target)

    def _apply_scale_target(self) -> None:
        self._scale_after_id = None
        if self._scale_target_index is None or not self.steps:
            return
        self.index = self._scale_target_index
        self._scale_target_index = None
        self.draw()

    def toggle_play(self) -> None:
        if self.is_playing:
            self.stop_playback()
        else:
            self.start_playback()

    def start_playback(self) -> None:
        if not self.steps:
            return
        self.is_playing = True
        if self.play_button is not None:
            self.play_button.configure(text="⏸ Pause")
        self._schedule_play_tick()

    def stop_playback(self) -> None:
        self.is_playing = False
        if self.play_button is not None:
            self.play_button.configure(text="▶ Play")
        if self._play_after_id is not None:
            try:
                self.after_cancel(self._play_after_id)
            except Exception:
                pass
            self._play_after_id = None

    def _schedule_play_tick(self) -> None:
        if not self.is_playing:
            return
        self._play_after_id = self.after(self.play_delay_ms, self._play_tick)

    def _play_tick(self) -> None:
        self._play_after_id = None
        if not self.is_playing or not self.steps:
            return
        if self.index >= len(self.steps) - 1:
            self.stop_playback()
            return
        self.goto_step(self.index + 1)
        self._schedule_play_tick()

    def goto_step(self, idx: int) -> None:
        if not self.steps:
            return
        self.index = max(0, min(len(self.steps) - 1, idx))
        self._setting_scale = True
        self.scale.set(self.index)
        self._setting_scale = False
        self.draw()

    def prev_step(self) -> None:
        self.goto_step(self.index - 1)

    def next_step(self) -> None:
        self.goto_step(self.index + 1)

    def _clear_draw_cache(self) -> None:
        self._draw_cache.clear()
        self._draw_cache_order.clear()
        self._photo_ref = None

    @staticmethod
    def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
        hex_color = hex_color.lstrip("#")
        return int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)

    def _remember_cached_image(self, key: Tuple[int, int, bool, bool, bool], photo: ImageTk.PhotoImage) -> None:
        self._draw_cache[key] = photo
        self._draw_cache_order.append(key)
        # Keep cache bounded. The viewer can generate many steps, and PhotoImage
        # objects hold Tk-side memory until released.
        while len(self._draw_cache_order) > 96:
            old = self._draw_cache_order.pop(0)
            self._draw_cache.pop(old, None)

    def _get_rendered_map_image(self, step: Step, step_index: int, cell: int) -> ImageTk.PhotoImage:
        show_bsp = bool(self.show_bsp.get())
        show_rooms = bool(self.show_rooms.get())
        show_path = bool(self.show_path.get())
        key = (step_index, cell, show_bsp, show_rooms, show_path)
        cached = self._draw_cache.get(key)
        if cached is not None:
            return cached

        h = len(step.grid)
        w = len(step.grid[0]) if h else 0
        rgb_by_tile = {tile: self._hex_to_rgb(color) for tile, color in self.COLORS.items()}
        magenta = self._hex_to_rgb("#ff00ff")
        pixels = []
        for row in step.grid:
            for tile in row:
                pixels.append(rgb_by_tile.get(tile, magenta))

        tiny = Image.new("RGB", (w, h))
        tiny.putdata(pixels)
        resample = Image.Resampling.NEAREST if hasattr(Image, "Resampling") else Image.NEAREST
        image = tiny.resize((w * cell, h * cell), resample)
        draw = ImageDraw.Draw(image)

        def rect_grid(x: int, y: int, rw: int, rh: int, color: str, width: int = 1) -> None:
            x0, y0 = x * cell, y * cell
            x1, y1 = (x + rw) * cell, (y + rh) * cell
            draw.rectangle([x0, y0, x1, y1], outline=color, width=width)

        if show_bsp:
            for node in step.nodes:
                rect_grid(node.x, node.y, node.w, node.h, "#3b4a66", 1)

        if show_rooms:
            for r in step.rooms:
                rect_grid(r.x, r.y, r.w, r.h, "#e8f1a1", 1)

        if show_path and step.extra_paths:
            for candidate_path, color, label in step.extra_paths:
                for x, y in set(candidate_path):
                    if 0 <= x < w and 0 <= y < h:
                        x0, y0 = x * cell, y * cell
                        draw.rectangle([x0 + 1, y0 + 1, x0 + cell - 1, y0 + cell - 1], outline=color, width=max(1, min(2, cell // 3)))
                if candidate_path and cell >= 6:
                    lx, ly = candidate_path[0]
                    if 0 <= lx < w and 0 <= ly < h:
                        short_label = label.split(":", 1)[0].replace("SELECTED", "S")
                        draw.text((lx * cell + 1, ly * cell + 1), short_label, fill=color)

        if show_path and step.path:
            for x, y in set(step.path):
                if 0 <= x < w and 0 <= y < h:
                    x0, y0 = x * cell, y * cell
                    draw.rectangle([x0, y0, x0 + cell, y0 + cell], outline="#ff3b30", width=max(2, min(4, cell // 2)))

        photo = ImageTk.PhotoImage(image)
        self._remember_cached_image(key, photo)
        return photo

    def draw(self) -> None:
        self.canvas.delete("all")
        if not self.steps:
            return
        step = self.steps[self.index]
        h = len(step.grid)
        w = len(step.grid[0]) if h else 0
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        margin = 18
        title_h = 48
        self.cell = max(3, int(min((cw - margin * 2) / w, (ch - title_h - margin) / h)))
        ox = (cw - w * self.cell) // 2
        oy = title_h

        self.canvas.create_text(16, 12, anchor="nw", fill="#f2f2f2", font=("Arial", 13, "bold"), text=step.title)
        if step.note:
            self.canvas.create_text(16, 34, anchor="nw", fill="#c8c8c8", font=("Arial", 10), text=step.note)
        self.canvas.create_text(cw - 16, 12, anchor="ne", fill="#9aa4b2", font=("Arial", 9),
                                text="Optimized: cached image rendering + debounced timeline")

        self._photo_ref = self._get_rendered_map_image(step, self.index, self.cell)
        self.canvas.create_image(ox, oy, anchor="nw", image=self._photo_ref)

        if self.show_rooms.get():
            for i, r in enumerate(step.rooms):
                self.canvas.create_text(ox + r.cx * self.cell + self.cell // 2,
                                        oy + r.cy * self.cell + self.cell // 2,
                                        text=f"R{i}", fill="#ffffff",
                                        font=("Arial", max(7, self.cell), "bold"))

        legend_x = ox
        legend_y = oy + h * self.cell + 8
        legend = [("EMPTY", EMPTY), ("ROOM", ROOM), ("CORRIDOR", CORRIDOR), ("STAIR_UP", STAIR_UP), ("selected path", -1), ("EXTRA candidates", -2)]
        lx = legend_x
        for name, tile in legend:
            if tile == -1:
                color = "#ff3b30"
            elif tile == -2:
                color = "#00e5ff"
            else:
                color = self.COLORS[tile]
            self.canvas.create_rectangle(lx, legend_y, lx + 12, legend_y + 12, outline="", fill=color)
            self.canvas.create_text(lx + 16, legend_y + 6, anchor="w", fill="#d8d8d8", font=("Arial", 9), text=name)
            lx += 116 if tile < 0 else 92

        folded_seed = unity_build_settings_seed_from_text(self.seed_var.get()) if self.seed_var.get().strip() else "random"
        self.status_var.set(f"Step {self.index + 1}/{len(self.steps)}   Unity Seed={folded_seed}   cached draw")

    def draw_rect(self, ox: int, oy: int, x: int, y: int, w: int, h: int, color: str, width: int = 1) -> None:
        self.canvas.create_rectangle(
            ox + x * self.cell,
            oy + y * self.cell,
            ox + (x + w) * self.cell,
            oy + (y + h) * self.cell,
            outline=color,
            width=width,
        )


if __name__ == "__main__":
    DungeonViewer().mainloop()
