# Proto_JBRL Dungeon Step Viewer

> Build: `Stub removal parity build 2026-06-10 r20`

Unity `Proto_JBRL` 프로젝트의 `DungeonGenerator` 절차적 생성 알고리즘을 **시드(Seed) 기반으로 결정론적으로 재현**하고, 한 단계씩 시각화해 보여 주는 Python/Tkinter 뷰어입니다.

Unity와 동일한 `Seed` / `Floor` / `DungeonSettings` 입력을 주면 던전 격자(grid)가 그대로 일치합니다. 이를 위해 C#의 `System.Random`(구버전 Mono 스타일) 동작과 `unchecked int` 연산을 파이썬에 1:1 포팅했습니다.

생성 순서:
**Seed+Floor → BSP 분할 → 방 배치 → 방 채움 → MST 통로 → (Elite 층이면) Elite Room 선택 → pair-score 기반 EXTRA 통로 → 계단 배치 → MonsterDen 지정.**

---

## 1. 주요 특징

- **결정론적 재현**: Unity의 시드 폴딩 규칙(`seed % int.MaxValue`)과 `System.Random` 알고리즘을 그대로 포팅 → 동일 입력 → 동일 맵.
- **스텝 단위 시각화**: BSP 분할 → 방 배치 → 방 채움 → MST 통로 → **Elite Room 선택** → **EXTRA 통로(attempt별 전체 pair sweep → pair-best 모음 → carve)** → 계단 배치 → **MonsterDen 지정**의 전 과정을 스냅샷으로 저장하고, 화살표 / 슬라이더 / Play 버튼으로 스크럽 가능.
- **Elite Room**: 층 번호 끝자리가 5인 플로어(5F, 15F, 25F, ...)에서 MST 완료 후 결정론적으로 리프 방을 하나 선택. 선택된 방은 빨간 테두리(`#ff6b6b`)와 `ELITE` 라벨로 강조되며, 해당 방을 포함하는 쌍은 EXTRA 후보에서 제외됨. RNG를 소비하지 않아 이후 EXTRA / 계단 / MonsterDen 흐름과 패리티가 유지됨.
- **MonsterDen 지정**: 계단 배치 후, Unity `DeterministicSeedUtility.CreateSeed` 로 만든 **별도 RNG**(`monster_den_select` 도메인)로 일반 방 중 일부를 MonsterDen 으로 지정. 스폰 방·계단 방·Elite 방은 제외. 분홍 테두리(`#ff4fd8`)와 `DEN` 라벨로 강조.
- **EXTRA 다중 후보 오버레이**: 한 스텝에서 여러 후보 경로를 **16색 팔레트로 동시 표시**하고, 그 중 선택된(best) 경로를 빨간 두꺼운 테두리로 강조.
- **점수 가중치 노출**: EXTRA 점수 계산식의 `overlap`, `path_len`, `center_distance` 가중치를 인스펙터에서 직접 입력해 튜닝.
- **Unity 설정 일치**: `MapWidth`, `MapHeight`, `MinRoomSize`, `MaxRoomSize`, `BspDepth`, `Padding`, `ExtraConnProb`, `ExtraCandidateCount`, `ExtraOverlapScoreWeight`, `ExtraPathLengthPenaltyWeight`, `ExtraCenterDistancePenaltyDivisor`, `MinStraight`, `MaxFloor`, `MonsterDenChance`, `MaxMonsterDenCount` 모든 인스펙터 값을 UI에서 그대로 입력.
- **줌 / 패닝**: 마우스 휠로 줌 인/아웃(0.5× ~ 10×, 커서 위치 기준), 좌클릭 드래그로 맵 이동. 캔버스 우상단에 현재 줌 배율 표시.
- **성능 최적화**: PIL `Image.resize(NEAREST)` 기반 렌더링 + 스텝별 `PhotoImage` 캐시(LRU 96개) + 타임라인 디바운싱(16ms).

---

## 2. 실행 방법

### 요구 사항

- Python 3.9+
- 패키지: `pillow` (PIL), `tkinter` (대부분의 표준 Python 배포판에 포함)

```bash
pip install pillow
```

### 실행

```bash
python DungeonGenerate_Simulator.py
```

### 조작 키

| 입력 | 동작 |
|---|---|
| `Seed` / `Floor` 입력 → `Generate` 또는 `Enter` | 던전 재생성 |
| `←` / `→` | 이전 / 다음 스텝 |
| `Home` / `End` | 첫 / 마지막 스텝 |
| `Space` | 자동 재생 / 일시정지 |
| Timeline 슬라이더 | 임의 스텝으로 스크럽 |
| 마우스 휠 | 캔버스 줌 인/아웃 (0.5× ~ 10×) |
| 좌클릭 드래그 | 캔버스 이동(패닝) |
| `BSP` / `Rooms` / `Path` 토글 | 오버레이 표시 토글 (`Path` 토글은 EXTRA 후보군 오버레이도 함께 제어) |

> 기본값: `Seed = 283321776792`, `Floor = 3`, `MapWidth = 120`, `MapHeight = 80` 로 시작하며 실행 즉시 한 번 생성합니다.

---

## 3. 아키텍처 분석

### 3.1 전체 구조

```
┌───────────────────────────────────────────────────────────┐
│  DungeonViewer (Tkinter UI)                               │
│   ├─ 입력 패널 (Seed / Floor / DungeonSettings + 점수 가중치)│
│   ├─ Canvas (PIL 렌더링 + 캐시 + 다색 EXTRA 오버레이)        │
│   └─ Timeline / Play / Step 컨트롤                         │
└──────────────────────────┬────────────────────────────────┘
                           │ generate()
                           ▼
┌───────────────────────────────────────────────────────────┐
│  DungeonGenerator                                          │
│   ├─ DotNetRandom (C# System.Random 포팅)                  │
│   ├─ DeterministicSeedUtility 포팅 (FNV → MonsterDen 시드)  │
│   ├─ DungeonSettings.derive_seed() (Seed × Floor 폴딩)     │
│   ├─ bsp_split → collect_rooms → fill_room                 │
│   ├─ connect_all (MST + Elite + EXTRA)                     │
│   │    ├─ assign_elite_room (결정론적 Elite Room 선택)       │
│   │    └─ connect_extra_corridors (n-1 attempt, 점수 기반)  │
│   │         └─ build_extra_candidate_overlays (다색 오버레이)│
│   ├─ place_stairs                                          │
│   ├─ assign_monster_dens (별도 결정론 RNG)                  │
│   └─ snapshot(...) → Step 리스트                           │
└──────────────────────────┬────────────────────────────────┘
                           │ List[Step]
                           ▼
                      UI 가 step 단위로 렌더링
```

### 3.2 결정론을 만드는 세 축

#### (1) `DotNetRandom` — C# `System.Random` 포팅

[DungeonGenerate_Simulator.py:123](DungeonGenerate_Simulator.py#L123)

- 구버전 Mono / .NET Framework의 **subtractive generator**를 그대로 재현.
- `seed_array[56]`, `inext = 0`, `inextp = 21` 초기 상태, `MBIG = int.MaxValue`, `MSEED = 161803398` 등 동일.
- `next()`, `next(maxValue)`, `next(minValue, maxValue)`, `next_double()` 시그니처 일치.
- 시드 음수/INT_MIN 처리, 큰 범위(`> int.MaxValue`)일 때의 `get_sample_for_large_range()` 분기까지 포함.

부가적으로 C#의 `unchecked int` 산술을 위한 헬퍼:

- `uint32(v)` — 32비트 부호 없는 wrap-around
- `int32(v)` — 32비트 부호 있는 wrap-around
- `csharp_remainder(a, b)` — 피제수의 부호를 따라가는 C# `%` 연산

#### (2) Seed × Floor 폴딩 — 지형 RNG

[DungeonGenerate_Simulator.py:248](DungeonGenerate_Simulator.py#L248)

```python
def derive_seed(self) -> int:
    s = self.seed if self.seed is not None else 0
    a = int32(self.floor * int32(2654435761))
    b = int32(s ^ a)
    mixed = int32(b * int32(2246822519))
    return mixed & 0x7FFFFFFF
```

Unity의 `DungeonManager.BuildSettings()`가 12자리 long 시드를 `int`로 축약하는 규칙(`seed % int.MaxValue`)도 [`unity_build_settings_seed_from_text`](DungeonGenerate_Simulator.py#L66) 가 그대로 포팅. 두 단계(인스펙터 폴딩 + 플로어 mixing)를 거쳐야 같은 RNG 스트림이 나옵니다. 이 시드로 만든 `DotNetRandom` 이 BSP / 방 / 통로 / 계단 배치를 모두 구동합니다.

> **중요**: `SpawnRegion`은 지형(BSP/방/통로/계단) 생성에 사용되지 않습니다. 지형 시드는 `Seed`와 `Floor` 값만으로 결정됩니다. (`SpawnRegion`은 아래 MonsterDen 시드 계산에만 쓰입니다.)

#### (3) `DeterministicSeedUtility` 포팅 — MonsterDen 전용 RNG

[`deterministic_create_seed`](DungeonGenerate_Simulator.py#L112)

- Unity `DeterministicSeedUtility.CreateSeed` 의 FNV-1a 해시 포팅 (`add_fnv_byte` / `add_fnv_int` / `add_fnv_long` / `add_fnv_string` / `to_positive_seed`).
- 입력: `globalSeed(source_seed_long)`, `spawnRegion`, `floor`, `stableRoomKey(=0)`, `domain("monster_den_select")`.
- 지형 RNG 와 **완전히 분리된** 별도 `DotNetRandom` 인스턴스를 만들어 MonsterDen 지정에만 사용 → 지형 생성 RNG 스트림에 영향 없음.

### 3.3 생성 파이프라인

[`DungeonGenerator.generate()`](DungeonGenerate_Simulator.py#L376) 이 단계마다 `snapshot()` 을 호출해 `Step` 리스트를 누적합니다.

| 단계 | 메서드 | 역할 |
|---|---|---|
| 00 | `BSPNode(root)` | 전체 맵을 하나의 노드로 초기화 (`derived seed` 노트) |
| 01 | `bsp_split` | 가로/세로 기준 재귀 분할. `bsp_depth` 또는 최소 분할 크기에서 종료. 가로:세로 비율 1.25 임계로 축 선택 |
| 02 | `collect_rooms` | 리프 노드마다 `[min_room_size, max_room_size]` 범위로 방 크기/위치 결정 |
| 03 | `fill_room` | 방 셀을 `ROOM` 타일로 채움 (방마다 1스텝) |
| 04 | `connect_all` (MST) | **MST**: 연결된 방 집합에서 가장 가까운 미연결 방을 골라 L자 통로로 연결 |
| 04-E | `assign_elite_room` | **Elite Room**: 층 끝자리가 5인 플로어에서만 MST 리프 방 중 하나를 결정론적으로 선택. RNG 비소비 |
| 05 | `connect_extra_corridors` | **EXTRA**: n-1회 attempt 를 돌며 전체 room-pair sweep → 후보군 → pair-best 모음 → carve |
| 06 | `place_stairs` | 셔플된 방 순서로 적합한 셀을 찾아 `STAIR_UP` 배치 (`floor < max_floor` 조건) |
| 07 | `assign_monster_dens` | **MonsterDen**: 별도 결정론 RNG 로 일반 방 일부를 MonsterDen 으로 지정 |
| 99 | 최종 | MST + EXTRA + 계단 + MonsterDen 이 모두 적용된 최종 던전 스냅샷 |

#### MST 단계

[`connect_all`](DungeonGenerate_Simulator.py#L453)

- 방 0번을 시드로, 매 반복마다 `connected` 집합과 `remaining` 집합 사이 유클리드 거리 최소 쌍을 그리디 선택.
- L자 통로([`draw_l_corridor`](DungeonGenerate_Simulator.py#L908))는 primary 방향 → 실패 시 alternate 방향 → 강제 fallback(mandatory).
- 결과로 `mst_pairs: Set[(i, j)]` 누적 → EXTRA 단계에서 중복 회피.
- `mst_edges` 목록도 만들어 `assign_elite_room` 에 전달.
- MST 가 모두 끝난 뒤 같은 메서드 안에서 `assign_elite_room` → `connect_extra_corridors` 순으로 이어짐.

#### Elite Room 선택

[`assign_elite_room`](DungeonGenerate_Simulator.py#L1316)

- **적용 조건**: [`is_elite_floor`](DungeonGenerate_Simulator.py#L1313) → `floor > 0 and floor % 10 == 5` (5F, 15F, 25F, …). 비해당 층은 스냅샷 없이 `-1` 반환.
- MST의 degree == 1인 리프 방 중에서 R0을 제외하고, 깊이(내림차순) → 시작 방으로부터 거리(내림차순) → 인덱스(오름차순) 순으로 정렬해 첫 번째를 선택([`is_better_elite_room_candidate`](DungeonGenerate_Simulator.py#L1370)). 리프가 없으면 가장 먼 비-시작 방으로 fallback.
- 선택된 방은 `room.is_elite = True` 로만 표시(별도 타일 코드 없음).
- **RNG를 소비하지 않는다**. 이 덕분에 Elite/비Elite 층 모두 EXTRA / 계단 / MonsterDen RNG 흐름이 동일하게 유지됨.
- 선택된 방을 포함하는 모든 pair 는 EXTRA 후보 생성 단계에서 스킵됨.

#### EXTRA 통로 단계

[`connect_extra_corridors`](DungeonGenerate_Simulator.py#L502)

MST 완료 후 최대 `n-1`회 attempt 를 순서대로 수행합니다. EXTRA 는 `connected`/`remaining` 을 더 이상 변경하지 않고, 선택된 쌍만 `connected_pairs` 에 추가합니다.

각 attempt 의 스텝 구성 (타이틀은 `05-A{attempt:02d}` 접두):

| 스텝 타이틀 | 내용 |
|---|---|
| `05-A{N}. EXTRA attempt 스킵` | `ExtraConnProb` 롤 실패 — 이번 attempt 는 EXTRA 를 만들지 않음 |
| `05-A{N}. EXTRA attempt 시작 - 전체 room pair sweep` | 롤 통과 — 이번 attempt 가 모든 미연결 쌍을 검사 |
| `05-A{N}. pair R{src} -> R{dst} 스킵` | 이미 연결됐거나 Elite Room 을 포함하는 pair 는 스킵 |
| `05-A{N}. R{src} -> R{dst} 후보 없음` | 거절 규칙을 통과한 후보가 없을 때, 거절 사유 목록과 함께 표시 |
| `05-A{N}. R{src} -> R{dst} 후보군 + pair best` | **그 쌍의 모든 클린 후보를 16색 팔레트로 동시 표시**, pair-best 는 빨간 두꺼운 테두리 |
| `05-A{N}. EXTRA 선택 없음` | 모든 쌍에서 pair-best 가 비었을 때 |
| `05-A{N}. 전체 pair-best 후보군 + attempt 최종 best` | **그 attempt 의 모든 pair-best 를 한 화면에 모아 표시**, 그중 최종 best 를 빨간 강조 |
| `05-A{N}. EXTRA 통로 생성 R{src} -> R{dst}` | 최종 best 를 carve. 격자에 `CORRIDOR` 가 실제로 새겨지는 순간 |

알고리즘 본체:

1. 각 attempt 마다 RNG `next_double()` 한 번으로 `roll`. `roll >= ExtraConnProb` 면 스킵.
2. 통과하면 모든 미연결 room pair `(src < dst)` 를 검사 (이미 연결됐거나 Elite Room 을 포함하면 건너뜀).
3. 한 쌍당 `ExtraCandidateCount` 변형 경로 생성([`build_extra_path_candidates_for_pair`](DungeonGenerate_Simulator.py#L639) → [`emit_extra_path_candidate`](DungeonGenerate_Simulator.py#L734), [`pick_extra_axis`](DungeonGenerate_Simulator.py#L752)).
4. 각 후보는 **8개 거절 규칙**([`corridor_candidate_reject_reason`](DungeonGenerate_Simulator.py#L1037))으로 필터링 (순서대로):
   - `room-perimeter-corridor`, `corner-doorway`, `third-room`, `bad-door-run`, `orphan-door-stub`, `outward-room-stub`, `diagonal-room-stub`, `third-room-parallel`
   - (`room-perimeter`, `corner-doorway`, `outward-room-stub`, `diagonal-room-stub`, `third-room-parallel` 는 EXTRA 전용; MST mandatory 통로에는 적용되지 않음)
5. 살아남은 후보는 [`score_extra_corridor_candidate`](DungeonGenerate_Simulator.py#L815) 로 점수화 — **가중치가 UI에서 노출**됨:
   ```
   score = overlap × ExtraOverlapScoreWeight (default 20)
         − path_len × ExtraPathLengthPenaltyWeight (default 8)
         − center_dist_sq ÷ ExtraCenterDistancePenaltyDivisor (default 20)
   ```
   > `parallel_run` 은 디버그 노트/dirty 체크용으로 측정되지만 **점수에서 차감되지 않습니다.**
6. 쌍별 최선([`select_best_extra_corridor_candidate`](DungeonGenerate_Simulator.py#L704)) → attempt 전체에서 다시 최선 → carve. 동률 시 [`is_better_extra_corridor_candidate`](DungeonGenerate_Simulator.py#L712) 의 결정론적 tie-break(score / path_len / overlap / center_dist / src / dst / candidate / attempt / primary) 적용.
7. carve 한 쌍은 `connected_pairs` 에 추가되어 이후 attempt 에서 중복 선택되지 않음.

#### EXTRA 후보 오버레이

[`build_extra_candidate_overlays`](DungeonGenerate_Simulator.py#L349)

- 한 스텝에 여러 후보 경로를 함께 표시하기 위한 `(path, color, label)` 리스트를 만듦.
- 16색 고대비 팔레트 ([`extra_candidate_color`](DungeonGenerate_Simulator.py#L338)) 가 후보별로 순차 배정 — `#00e5ff`, `#ffd60a`, `#7cff6b`, `#ff7ad9`, ...
- 선택된 best 후보는 팔레트가 아닌 빨간색 (`#ff3b30`) 으로 별도 표시.
- 각 후보 경로의 첫 번째 셀에 `C{n}: R{src}->{dst} score={score}`, 선택됨은 `SELECTED R{src}->{dst} score={score}` 라벨이 작은 텍스트로 그려짐.

#### MonsterDen 지정

[`assign_monster_dens`](DungeonGenerate_Simulator.py#L1381)

- 계단 배치 후 수행. Unity 파이프라인(Registry 가 Stair 를 먼저 표시 → 스폰 방 탐색 → MonsterDen 지정)을 그대로 따름.
- 먼저 모든 방을 `Normal` 로 초기화하고, `STAIR_UP` 타일을 포함한 방을 `Stair` 로 표시.
- [`compute_spawn_pos`](DungeonGenerate_Simulator.py#L1440) 로 맵 중앙에 가장 가까운 `ROOM` 셀을 스폰 위치로 잡고, 그 방을 후보에서 제외(`exclude_spawn_key`).
- MonsterDen 후보 = `Normal` 이며 Elite 가 아니고 스폰 방이 아닌 방.
- `deterministic_create_seed(source_seed_long, spawn_region, floor, 0, "monster_den_select")` 로 만든 **별도 RNG** 로 `MaxMonsterDenCount` 회 시도: `next_double() < MonsterDenChance` 면 후보 중 하나를 무작위로 골라 `MonsterDen` 으로 지정.
- `MaxMonsterDenCount <= 0` 이면 `"07. MonsterDen 지정 생략"` 스냅샷만 남기고 종료.

### 3.4 데이터 모델

[DungeonGenerate_Simulator.py:199-307](DungeonGenerate_Simulator.py#L199-L307)

| `@dataclass` | 의미 |
|---|---|
| `DungeonSettings` | 인스펙터 값 미러링 + 점수 가중치 3종 + MonsterDen 설정(`monster_den_chance`, `max_monster_den_count`, `source_seed_long`, `spawn_region`) + `validate()` + `derive_seed()` |
| `Room` | `x, y, w, h, cx, cy` + `is_elite` + `room_type`(`Normal` / `Stair` / `MonsterDen`) |
| `BSPNode` | 자식 노드 트리, `is_leaf` |
| `ExtraCorridorCandidate` | EXTRA 후보의 점수 / 경로 / tie-break 메타데이터 일체 |
| `Step` | `title`, 격자 복사본, 방/노드 스냅샷, 강조 경로, 디버그 노트, `extra_paths`(다색 후보 오버레이 리스트) |

### 3.5 UI 계층

[`DungeonViewer`](DungeonGenerate_Simulator.py#L1514)

- **렌더링**: `Step.grid` → PIL `Image.putdata` (1픽셀 = 1셀) → `Image.resize(NEAREST)` 로 확대 → `ImageDraw` 로 BSP / Room / **EXTRA 다색 후보** / 선택 경로 오버레이 → `ImageTk.PhotoImage`.
- **방 강조**: `room_type == MonsterDen` 이면 분홍 테두리(`#ff4fd8`, 굵기 2) + `DEN` 라벨, `is_elite` 면 빨간 테두리(`#ff6b6b`, 굵기 2) + `ELITE` 라벨, 그 외 일반 방은 연두 테두리(`#e8f1a1`).
- **EXTRA 후보 그리기**: 클린 후보 경로는 셀 내부 1px 안쪽 테두리(`cell//3` 두께), 선택 경로는 셀 외곽 두꺼운 테두리(`cell//2` 두께)로 그려 시각적 우선순위 부여.
- **캐시**: `(step_index, cell, show_bsp, show_rooms, show_path)` 키로 PhotoImage 캐시. LRU 96개 제한.
- **타임라인 디바운싱**: `ttk.Scale` 드래그 중 16ms 후 단일 draw로 합침 → 끌어도 부드러움.
- **자동 재생**: `after(180ms)` 기반 틱 루프.
- **줌/패닝**: 마우스 휠로 0.5×~10× 줌(커서 위치 기준), 좌클릭 드래그로 맵 이동. 캔버스 우상단에 `Zoom {배율}x | wheel: zoom, drag: pan` 표시.
- **범례**: `EMPTY` / `ROOM` / `CORRIDOR` / `STAIR_UP` / `MonsterDen` / `Elite` / `selected path` / `EXTRA candidates` 8종 표시.
- **상태 표시줄**: `Step {i}/{n}   Unity Seed={folded}   cached draw`.

### 3.6 격자 타일 코드

```
EMPTY        = 0   #111217  배경
ROOM         = 1   #6c8f4e  방 바닥
CORRIDOR     = 2   #b88a48  통로
STAIR_UP     = 3   #53d6ff  올라가는 계단
DOOR_CLOSED  = 5   #8c5b2f  (예약, 미사용)
```

> Elite Room / MonsterDen 은 **별도 타일 코드가 아니라** `Room.is_elite` / `Room.room_type` 플래그로 표현되며, 방 바닥 타일은 그대로 `ROOM` 입니다. 강조는 방 테두리 색/라벨로만 이루어집니다.

방 타입 / 오버레이 색:

```
Room outline (Normal)   #e8f1a1
Elite Room outline      #ff6b6b  (굵기 2, ELITE 라벨)
MonsterDen outline      #ff4fd8  (굵기 2, DEN 라벨)
selected path           #ff3b30  (현재 강조 경로 / EXTRA final best)
EXTRA candidates        팔레트 16색 (#00e5ff, #ffd60a, #7cff6b, #ff7ad9, ...)
BSP outline             #3b4a66
```

---

## 4. Unity와 정확히 일치시키려면

1. Unity 인스펙터의 `Seed` 값을 그대로 입력 (12자리 long도 OK — `unity_build_settings_seed_from_text` 가 폴딩).
2. 같은 `Floor` 입력.
3. `DungeonSettings` 의 모든 필드(점수 가중치 3종 + MonsterDen 설정 포함)를 인스펙터와 동일하게 설정.
4. 지형(BSP/방/통로/계단)은 `Seed`/`Floor` 만으로 결정되므로 `SpawnRegion` 은 지형에 영향을 주지 않음. 단, **MonsterDen 시드**는 `source_seed_long`(원본 long Seed) + `SpawnRegion` + `Floor` + 도메인으로 계산되므로, MonsterDen 결과까지 맞추려면 이 값들도 동일해야 함.
5. RNG 호출 순서가 보존되어야 하므로, Unity 측 `DungeonGenerator` 가 RNG를 추가로 소비하는 변경을 가하면 뷰어도 같은 위치에서 동일 호출을 추가해야 함.
   - Elite Room 선택은 RNG를 소비하지 않으므로 엘리트 유무에 관계없이 EXTRA/계단/MonsterDen RNG 흐름이 동일.
   - MonsterDen 은 지형 RNG 와 분리된 별도 결정론 RNG 를 쓰므로 지형 스트림에 영향을 주지 않음.

---

## 5. 파일 구성

```
DungeonGenrate_Simulator/
├─ DungeonGenerate_Simulator.py   # 본체 (RNG + Generator + UI 모두 포함된 단일 파일)
└─ readme.md                       # 본 문서
```

이 뷰어는 Unity 프로젝트 파일을 import 하지 않는 **독립 실행형 시각화 도구**입니다.
