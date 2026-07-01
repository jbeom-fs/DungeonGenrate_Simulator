# Proto_JBRL Dungeon Step Viewer

> Build: `Stair-last pipeline + Unity byte-exact seed parity build 2026-07-01 r25`

Unity `Proto_JBRL` 프로젝트의 `DungeonGenerator` 절차적 생성 알고리즘을 **시드(Seed) 기반으로 결정론적으로 재현**하고, 한 단계씩 시각화해 보여 주는 Python/Tkinter 뷰어입니다.

Unity와 동일한 `Seed` / `Floor` / `DungeonSettings` 입력을 주면 던전 격자(grid)가 그대로 일치합니다. 이를 위해 C#의 `System.Random`(구버전 Mono 스타일) 동작과 `unchecked int` 연산을 파이썬에 1:1 포팅했습니다.

생성 순서(2026-07-01 개편):
**Seed+Floor → BSP 분할 → 방 배치 → 방 채움 → MST 통로 → (Elite 층이면) Elite Room 선택 → nearest-neighbor edge 후보 생성 → edge shuffle → pair-score 기반 EXTRA 통로 → 스폰 위치 계산 → MonsterDen 지정 → 계단 배치(최후단).**

> **핵심 변경(2026-07-01)**: 계단 배치가 파이프라인 중간(EXTRA 직후)에서 **최후단(모든 방 라벨 확정 후)** 으로 이동했습니다. 이제 계단은 스폰 방·Elite 방·회피 타입을 알고 피할 수 있으며, 지형 RNG 와 분리된 **독립 계단 RNG(`stair_select` 도메인)** 로 배치됩니다. 덕분에 맵 레이아웃은 기존 시드와 불변인 채 계단 위치만 결정론적으로 재배치됩니다. 아울러 MonsterDen / 계단 시드 계산을 Unity `DeterministicSeedUtility.CreateSeed` 와 **바이트 단위로 일치**하게 재구현해, 던·계단 위치까지 Unity와 완전히 일치합니다.

---

## 1. 주요 특징

- **결정론적 재현**: Unity의 시드 폴딩 규칙(`seed % int.MaxValue`)과 `System.Random` 알고리즘을 그대로 포팅 → 동일 입력 → 동일 맵.
- **Unity 바이트 단위 시드 패리티**: MonsterDen / 계단 시드를 `DeterministicSeedUtility.CreateSeed(long globalSeed, int spawnRegion, int floor, int stableRoomKey, string domain)` 와 바이트 단위로 일치하게 재구현. 지형 시드는 폴딩된 int 를 쓰지만, 던·계단 도메인 시드는 **raw long 인스펙터 시드**와 `spawnRegion`(Dungeon=1)을 사용 → 던·계단 위치까지 Unity와 정확히 일치.
- **스텝 단위 시각화**: BSP 분할 → 방 배치 → 방 채움 → MST 통로 → **Elite Room 선택** → **EXTRA edge 후보 생성 → edge shuffle → edge별 roll/후보군 → carve** → **스폰 위치 계산 → MonsterDen 지정 → 계단 배치(최후단)** 의 전 과정을 스냅샷으로 저장하고, 화살표 / 슬라이더 / Play 버튼으로 스크럽 가능.
- **스폰 방 계산**: EXTRA 완료 후 `SpawnPositionService` 를 이식해 **맵 중앙에 맨해튼 거리로 가장 가까운 `ROOM` 타일**을 찾고, 그 타일을 포함하는 방을 스폰 방으로 확정. 스캔 순서(row-major, 엄격한 `<` 갱신)까지 Unity와 동일해 동타일로 수렴. 노란 테두리(`#ffd60a`)와 `SPAWN` 라벨로 강조.
- **계단 배치(최후단·독립 RNG·제외+폴백)**: 모든 방 라벨(스폰/Elite/Den)이 확정된 뒤 실행. 지형 RNG 가 아닌 **`stair_select` 도메인 시드로 만든 독립 RNG** 로 방을 셔플하고, 스폰·Elite·회피 타입 방을 제외해 첫 유효 방에 `STAIR_UP` 을 배치. 제외 후 후보가 없으면 **제외를 무시하고 재시도(소프트락 방지)**. 최고층(`floor >= MaxFloor`)은 계단 없음.
- **Elite Room**: 층 번호 끝자리가 5인 플로어(5F, 15F, 25F, ...)에서 MST 완료 후 결정론적으로 리프 방을 하나 선택. 선택된 방은 빨간 테두리(`#ff3b30`)와 `ELITE` 라벨로 강조되며, 해당 방을 포함하는 쌍은 EXTRA 후보에서 제외됨. RNG를 소비하지 않아 이후 EXTRA / 스폰 / MonsterDen / 계단 흐름과 패리티가 유지됨.
- **nearest-neighbor EXTRA**: MST 완료 후, 각 방의 **k개(`ExtraNeighborCount`) 최근접 이웃**으로 EXTRA edge 후보 목록을 만들고, Fisher-Yates 셔플 → edge마다 `ExtraConnProb` roll → 통과한 edge에서 pair-best 후보를 carve. 이미 연결됐거나 Elite Room을 포함하는 쌍은 후보에서 제외.
- **MonsterDen 지정**: 스폰 방 확정 후, `CreateSeed`(`monster_den_select` 도메인) 로 만든 **별도 RNG**로 일반 방 중 일부를 MonsterDen으로 지정. **스폰 방·Elite 방은 제외**(이 시점엔 계단이 아직 없어 계단 방 제외 없음). 보라 테두리(`#c77dff`)와 `DEN` 라벨로 강조.
- **EXTRA 다중 후보 오버레이**: 한 스텝에서 여러 후보 경로를 **16색 팔레트로 동시 표시**하고, 그 중 선택된(best) 경로를 빨간 두꺼운 테두리로 강조.
- **점수 가중치 노출**: EXTRA 점수 계산식의 `overlap`, `path_len`, `center_distance` 가중치를 인스펙터에서 직접 입력해 튜닝.
- **Unity 설정 일치**: `MapWidth`, `MapHeight`, `MinRoomSize`, `MaxRoomSize`, `BspDepth`, `Padding`, `ExtraConnProb`, `ExtraCandidateCount`, `ExtraNeighborCount`, `ExtraOverlapScoreWeight`, `ExtraPathLengthPenaltyWeight`, `ExtraCenterDistancePenaltyDivisor`, `MonsterDenChance`, `MaxMonsterDenCount`, `MinStraight`, `MaxFloor`, **`SpawnRegion`, `Stair avoids MonsterDen`** 모든 인스펙터 값을 UI에서 그대로 입력.
- **줌 / 패닝**: 마우스 휠로 줌 인/아웃(0.5× ~ 5×, 커서 위치 기준), 좌클릭 드래그로 맵 이동. 캔버스 우상단에 현재 줌 배율 표시.
- **재생 속도 조절**: Play 자동 재생의 속도를 0.2× ~ 5.0× 슬라이더로 조절.
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
| Speed 슬라이더 | 자동 재생 속도 조절 (0.2× ~ 5.0×) |
| 마우스 휠 | 캔버스 줌 인/아웃 (0.5× ~ 5×) |
| 좌클릭 드래그 | 캔버스 이동(패닝) |
| `BSP` / `Rooms` / `Path` 토글 | 오버레이 표시 토글 (`Path` 토글은 EXTRA 후보군 오버레이도 함께 제어) |

> 기본값: `Seed = 283321776792`, `Floor = 3`, `MapWidth = 120`, `MapHeight = 80`, `SpawnRegion = 1(Dungeon)`, `Stair avoids MonsterDen = off` 로 시작하며 실행 즉시 한 번 생성합니다.

---

## 3. 아키텍처 분석

### 3.1 전체 구조

```
┌───────────────────────────────────────────────────────────┐
│  DungeonViewer (Tkinter UI)                               │
│   ├─ 입력 패널 (Seed / Floor / DungeonSettings + 점수 가중치 │
│   │           + SpawnRegion + Stair avoids MonsterDen)     │
│   ├─ Canvas (PIL 렌더링 + 캐시 + 다색 EXTRA 오버레이)        │
│   └─ Timeline / Play / Speed / Step 컨트롤                 │
└──────────────────────────┬────────────────────────────────┘
                           │ generate()
                           ▼
┌───────────────────────────────────────────────────────────┐
│  DungeonGenerator                                          │
│   ├─ DotNetRandom (C# System.Random 포팅)                  │
│   ├─ create_seed (CreateSeed 바이트 단위 포팅 → Den/계단 시드)│
│   ├─ DungeonSettings.derive_seed() (Seed × Floor 폴딩)     │
│   ├─ bsp_split → collect_rooms → fill_room                 │
│   ├─ connect_all (MST + Elite + EXTRA)                     │
│   │    ├─ assign_elite_room (결정론적 Elite Room 선택)       │
│   │    └─ connect_extra_corridors                          │
│   │         ├─ build_extra_edge_candidates (k-NN edge)     │
│   │         ├─ Fisher-Yates shuffle                        │
│   │         └─ edge별 roll → pair-best carve               │
│   │              └─ build_extra_candidate_overlays         │
│   ├─ compute_spawn_pos (맵중앙 최근접 ROOM → 스폰 방)        │
│   ├─ assign_monster_dens (Den 도메인 별도 RNG)             │
│   ├─ place_stairs (계단 도메인 독립 RNG · 최후단 · 제외+폴백)│
│   └─ snapshot(...) → Step 리스트                           │
└──────────────────────────┬────────────────────────────────┘
                           │ List[Step]
                           ▼
                      UI 가 step 단위로 렌더링
```

### 3.2 결정론을 만드는 축

#### (1) `DotNetRandom` — C# `System.Random` 포팅

[DungeonGenerate_Simulator.py:125](DungeonGenerate_Simulator.py#L125)

- 구버전 Mono / .NET Framework의 **subtractive generator**를 그대로 재현.
- `seed_array[56]`, `inext = 0`, `inextp = 21` 초기 상태, `MBIG = int.MaxValue`, `MSEED = 161803398` 등 동일.
- `next()`, `next(maxValue)`, `next(minValue, maxValue)`, `next_double()` 시그니처 일치.
- 시드 음수/INT_MIN 처리, 큰 범위(`> int.MaxValue`)일 때의 `get_sample_for_large_range()` 분기까지 포함.

부가적으로 C#의 `unchecked int` 산술을 위한 헬퍼:

- `int32(v)` — 32비트 부호 있는 wrap-around
- `csharp_remainder(a, b)` — 피제수의 부호를 따라가는 C# `%` 연산

#### (2) Seed × Floor 폴딩 — 지형 RNG

[DungeonGenerate_Simulator.py:255](DungeonGenerate_Simulator.py#L255)

```python
def derive_seed(self) -> int:
    s = self.seed if self.seed is not None else 0
    a = int32(self.floor * int32(2654435761))
    b = int32(s ^ a)
    mixed = int32(b * int32(2246822519))
    return mixed & 0x7FFFFFFF
```

Unity의 `DungeonManager.BuildSettings()`가 12자리 long 시드를 `int`로 축약하는 규칙(`seed % int.MaxValue`)도 [`unity_build_settings_seed_from_text`](DungeonGenerate_Simulator.py#L117) 가 그대로 포팅. 두 단계(인스펙터 폴딩 + 플로어 mixing)를 거쳐야 같은 RNG 스트림이 나옵니다. 이 **폴딩된 int 시드**로 만든 `DotNetRandom` 이 BSP / 방 / 통로를 모두 구동합니다.

#### (3) `create_seed` — `DeterministicSeedUtility.CreateSeed` 바이트 단위 포팅

[DungeonGenerate_Simulator.py:78](DungeonGenerate_Simulator.py#L78)

MonsterDen / 계단 전용 시드입니다. 지형 RNG 와는 **완전히 분리된** 별도 `DotNetRandom` 을 만듭니다. Unity `CreateSeed(long globalSeed, int spawnRegion, int floor, int stableRoomKey, string spawnDomain)` 와 **바이트 단위로 일치**하도록 재구현했습니다. (2026-07-01 개편에서 기존 근사 해시를 교체)

- **`globalSeed` 는 C# `long` 으로 해시(`AddLong`)**: low int → high int 순으로 각각 4바이트. 그리고 이때 쓰는 값은 지형용 폴딩 int 가 아니라 **raw long 인스펙터 시드(`DungeonManager.seed`)** 입니다 → 뷰어의 `DungeonSettings.raw_seed`.
- **`spawnRegion` 은 실제 값(Dungeon = 1)** — 기존에 0 으로 고정했던 것을 UI 노출값으로 교체.
- **문자열 도메인(`AddString`)**: 길이를 int 로 먼저 넣고, 각 문자를 UTF-16 int(4바이트)로 해시.
- **`ToPositiveSeed`**: `hash & 0x7FFFFFFF`, 0 이면 1 로 보정.

이 시드 계산을 도메인별로 감싼 것이:

- [`create_monster_den_seed`](DungeonGenerate_Simulator.py#L1509) — 도메인 `monster_den_select`
- [`create_stair_seed`](DungeonGenerate_Simulator.py#L1611) — 도메인 `stair_select`

두 시드는 각각 독립된 `DotNetRandom` 인스턴스를 만들어 MonsterDen 지정 / 계단 배치에만 사용하므로, BSP·방·통로를 구동하는 지형 RNG 스트림에는 영향이 없습니다. 즉 계단 배치 로직이 바뀌어도 **맵 레이아웃은 기존 시드와 불변**입니다.

### 3.3 생성 파이프라인

[`DungeonGenerator.generate()`](DungeonGenerate_Simulator.py#L399) 이 단계마다 `snapshot()` 을 호출해 `Step` 리스트를 누적합니다. (`snapshot()` 은 매 스텝마다 방 리스트를 깊은 복사해 그 시점의 `is_elite` / `is_monster_den` / `is_spawn` 플래그를 보존합니다.)

Unity `DungeonManager.RunGenerationPipeline` 순서와 동일하게, **모든 방 라벨(스폰/Den/Elite)이 확정된 뒤 계단을 최후단에서 배치**합니다.

| 단계 | 메서드 | 역할 |
|---|---|---|
| 00 | `BSPNode(root)` | 전체 맵을 하나의 노드로 초기화 (`derived seed` 노트) |
| 01 | `bsp_split` | 가로/세로 기준 재귀 분할. `bsp_depth` 또는 최소 분할 크기에서 종료. 가로:세로 비율 1.25 임계로 축 선택 |
| 02 | `collect_rooms` | 리프 노드마다 `[min_room_size, max_room_size]` 범위로 방 크기/위치 결정 |
| 03 | `fill_room` | 방 셀을 `ROOM` 타일로 채움 (방마다 1스텝) |
| 04 | `connect_all` (MST) | **MST**: 연결된 방 집합에서 가장 가까운 미연결 방을 골라 L자 통로로 연결 |
| 04E | `assign_elite_room` | **Elite Room**: 층 끝자리가 5인 플로어에서만 MST 리프 방 중 하나를 결정론적으로 선택. RNG 비소비 |
| 05 | `connect_extra_corridors` | **EXTRA**: k-NN edge 후보 생성 → shuffle → edge별 roll → pair-best carve |
| 06 | `compute_spawn_pos` | **스폰 방**: 맵 중앙 최근접 `ROOM` 타일을 찾아 스폰 방 확정 (`spawn_room_index`) |
| 07 | `assign_monster_dens` | **MonsterDen**: Den 도메인 별도 RNG 로 일반 방 일부를 지정 (스폰·Elite 제외) |
| 08 | `place_stairs` | **계단**: 계단 도메인 독립 RNG 로 방 셔플 → 스폰/Elite/회피 제외 후 `STAIR_UP` 배치 (제외 후보 없으면 제외 무시 재시도, 최고층 무배치) |
| 99 | 최종 | MST + Elite + EXTRA + 스폰 + MonsterDen + 계단 이 모두 적용된 최종 던전 스냅샷 |

#### MST 단계

[`connect_all`](DungeonGenerate_Simulator.py#L479)

- 방 0번을 시드로, 매 반복마다 `connected` 집합과 `remaining` 집합 사이 유클리드 거리 최소 쌍을 그리디 선택.
- L자 통로([`draw_l_corridor`](DungeonGenerate_Simulator.py#L1058))는 primary 방향 → 실패 시 alternate 방향 → 강제 fallback(mandatory).
- 결과로 `mst_pairs: Set[(i, j)]` 누적 → EXTRA 단계에서 중복 회피. `mst_edges` 목록도 만들어 `assign_elite_room` 에 전달.
- MST 가 모두 끝난 뒤 같은 메서드 안에서 `assign_elite_room` → `connect_extra_corridors` 순으로 이어짐.

#### Elite Room 선택

[`assign_elite_room`](DungeonGenerate_Simulator.py#L527)

- **적용 조건**: [`is_elite_floor`](DungeonGenerate_Simulator.py#L576) → `floor > 0 and floor % 10 == 5` (5F, 15F, 25F, …). 비해당 층은 스냅샷 없이 종료.
- MST 인접 리스트로 BFS 깊이([`build_mst_depths`](DungeonGenerate_Simulator.py#L579))를 구한 뒤, degree == 1 인 리프 방 중에서 R0을 제외하고 깊이(내림차순) → 시작 방으로부터 거리(내림차순) → 인덱스(오름차순) 순으로 정렬해 첫 번째를 선택([`is_better_elite_room_candidate`](DungeonGenerate_Simulator.py#L596)). 리프가 없으면 가장 먼 비-시작 방으로 fallback.
- 선택된 방은 `room.is_elite = True` 로만 표시(별도 타일 코드 없음). `elite_room_index` 에도 기록.
- **RNG를 소비하지 않는다**. 이 덕분에 Elite/비Elite 층 모두 EXTRA / 스폰 / MonsterDen / 계단 RNG 흐름이 동일하게 유지됨.
- 선택된 방을 포함하는 모든 pair 는 EXTRA edge 후보 생성 단계에서 스킵됨([`is_extra_room_excluded`](DungeonGenerate_Simulator.py#L771)).
- 스냅샷: `04E. Elite Room 선택 R{best}` (또는 방이 2개 미만일 때 `04E. Elite Room 선택 실패`).

#### EXTRA 통로 단계

[`connect_extra_corridors`](DungeonGenerate_Simulator.py#L605)

MST(+Elite) 완료 후 **nearest-neighbor edge 기반**으로 EXTRA 통로를 추가합니다. 흐름은 ① edge 후보 목록 생성 → ② Fisher-Yates 셔플 → ③ edge별 `ExtraConnProb` roll → ④ 통과한 edge에서 pair-best 후보 carve 순서입니다.

가드(조기 종료):

- `ExtraConnProb <= 0` → `05. EXTRA 통로 비활성`
- `ExtraCandidateCount <= 0` → `05. EXTRA 후보 검토 생략`
- `ExtraNeighborCount <= 0` → `05. EXTRA edge 후보 검토 생략`
- edge 후보가 0개 → `05. EXTRA edge 후보 없음`

스텝 구성:

| 스텝 타이틀 | 내용 |
|---|---|
| `05-00. EXTRA edge 후보 생성` | k-NN edge 후보 목록 + 파라미터 노트(distSq 포함) 표시 |
| `05-01. EXTRA edge 후보 shuffle 완료` | Fisher-Yates 셔플 후의 edge 순서 표시 |
| `05-E{NN}. EXTRA edge R{a} -> R{b} 스킵` | `roll >= ExtraConnProb` — 이 edge 는 EXTRA 를 만들지 않음 |
| `05-E{NN}. EXTRA edge R{a} -> R{b} 진행` | `roll < ExtraConnProb` — 이 edge 의 후보를 검토 |
| `05-E{NN}. R{a} -> R{b} 후보 없음` | 거절 규칙을 통과한 후보가 없을 때, 거절 사유 목록과 함께 표시 |
| `05-E{NN}. R{a} -> R{b} 후보군 + pair best` | **그 edge 의 모든 클린 후보를 16색 팔레트로 동시 표시**, pair-best 는 빨간 두꺼운 테두리 |
| `05-E{NN}. EXTRA 통로 생성 R{src} -> R{dst}` | pair-best 를 carve. 격자에 `CORRIDOR` 가 실제로 새겨지는 순간 |
| `05. EXTRA 최종 결과` | 모든 edge 에서 carve 가 0개였을 때만 |

알고리즘 본체:

1. **edge 후보 생성**([`build_extra_edge_candidates`](DungeonGenerate_Simulator.py#L725)): 각 방마다 `center_dist_sq` 기준 최근접 이웃 `ExtraNeighborCount` 개를 부분 selection-sort 로 골라 `(min, max)` 쌍을 만듦. 이미 연결된 쌍(`connected_pairs`)·중복 edge·Elite Room 포함 쌍은 제외. tie-break 는 거리 → 인덱스([`is_better_extra_neighbor`](DungeonGenerate_Simulator.py#L775)).
2. **shuffle**: edge 목록을 Fisher-Yates 로 in-place 셔플 (`rng.next(i + 1)` 사용).
3. **edge별 roll**: 각 edge 마다 `next_double()` 한 번으로 `roll`. `roll >= ExtraConnProb` 면 스킵.
4. 통과하면 그 쌍에 대해 `ExtraCandidateCount` 변형 경로 생성([`build_extra_path_candidates_for_pair`](DungeonGenerate_Simulator.py#L789) → [`emit_extra_path_candidate`](DungeonGenerate_Simulator.py#L884), [`pick_extra_axis`](DungeonGenerate_Simulator.py#L903)).
5. 각 후보는 **8개 거절 규칙**([`corridor_candidate_reject_reason`](DungeonGenerate_Simulator.py#L1183))으로 필터링 (순서대로):
   - `room-perimeter-corridor`, `corner-doorway`, `third-room`, `bad-door-run`, `orphan-door-stub`, `outward-room-stub`, `diagonal-room-stub`, `third-room-parallel`
   - (`room-perimeter`, `corner-doorway`, `outward-room-stub`, `diagonal-room-stub`, `third-room-parallel` 는 EXTRA 전용; MST mandatory 통로에는 적용되지 않음)
6. 살아남은 후보는 [`score_extra_corridor_candidate`](DungeonGenerate_Simulator.py#L965) 로 점수화 — **가중치가 UI에서 노출**됨:
   ```
   score = overlap × ExtraOverlapScoreWeight (default 10)
         − path_len × ExtraPathLengthPenaltyWeight (default 8)
         − center_dist_sq ÷ ExtraCenterDistancePenaltyDivisor (default 10)
   ```
   > `parallel_run` 은 디버그 노트/dirty 체크용으로 측정되지만 **점수에서 차감되지 않습니다.**
7. edge별 최선([`select_best_extra_corridor_candidate`](DungeonGenerate_Simulator.py#L855)) 을 골라 carve. 동률 시 [`is_better_extra_corridor_candidate`](DungeonGenerate_Simulator.py#L863) 의 결정론적 tie-break(score / path_len / overlap / center_dist / src / dst / candidate / attempt / primary) 적용.
8. carve 한 쌍은 `connected_pairs` 에 추가되어 이후 edge 후보 생성/검토에서 중복되지 않음.

#### EXTRA 후보 오버레이

[`build_extra_candidate_overlays`](DungeonGenerate_Simulator.py#L373)

- 한 스텝에 여러 후보 경로를 함께 표시하기 위한 `(path, color, label)` 리스트를 만듦.
- 16색 고대비 팔레트 ([`extra_candidate_color`](DungeonGenerate_Simulator.py#L362)) 가 후보별로 순차 배정 — `#00e5ff`, `#ffd60a`, `#7cff6b`, `#ff7ad9`, ...
- 선택된 best 후보는 팔레트가 아닌 빨간색 (`#ff3b30`) 으로 별도 표시.
- 각 후보 경로의 첫 번째 셀에 `C{n}: R{src}->{dst} score={score}`, 선택됨은 `SELECTED R{src}->{dst} score={score}` 라벨이 작은 텍스트로 그려짐.

#### 스폰 위치 계산

[`compute_spawn_pos`](DungeonGenerate_Simulator.py#L1513)

- Unity `SpawnPositionService.ComputeSpawnPos` 이식. EXTRA 통로까지 완성된 격자에서 **맵 중앙 `(map_width // 2, map_height // 2)` 에 맨해튼 거리가 가장 작은 `ROOM` 타일**을 찾음.
- 스캔은 row-major(행 우선), 갱신은 엄격한 `dist < best`(동률이면 먼저 스캔된 타일 유지) — Unity와 동일 순서라 항상 같은 타일로 수렴.
- 그 타일을 포함하는 방을 [`room_index_at`](DungeonGenerate_Simulator.py#L1551) 으로 찾아 `spawn_room_index` 로 확정하고 해당 방의 `is_spawn = True`.
- 스냅샷: `06. 스폰 위치 계산 R{spawn}` (중앙 좌표·스폰 타일·맨해튼 거리 노트).
- 이 스폰 방은 이후 MonsterDen / 계단 후보에서 제외 기준으로 쓰임.

#### MonsterDen 지정

[`assign_monster_dens`](DungeonGenerate_Simulator.py#L1459)

- 스폰 위치 확정 후 수행. 먼저 모든 방의 `is_monster_den` 을 초기화.
- `MaxMonsterDenCount <= 0` 또는 `MonsterDenChance <= 0.0` 이면 `"07. MonsterDen 지정 생략"` 스냅샷만 남기고 종료.
- MonsterDen 후보 = **스폰 방(`spawn_room_index`)·Elite 방을 제외**한 일반 방. (`RoomRegistry.AssignMonsterDens` 와 동일: `Normal && !elite && !spawn`. 계단은 아직 배치 전이라 제외 대상이 아님.) 후보가 없으면 `"07. MonsterDen 후보 없음"`.
- [`create_monster_den_seed`](DungeonGenerate_Simulator.py#L1509) 로 만든 **별도 RNG** 로 `MaxMonsterDenCount` 회 시도: `next_double() < MonsterDenChance` 면 남은 후보 중 하나를 무작위로 골라 `is_monster_den = True` 로 지정(중복 방지를 위해 후보에서 pop).
- 결과에 따라 `"07. MonsterDen 지정 R..."` 또는 `"07. MonsterDen 지정 없음"` 스냅샷을 남기며, 노트에 시도별 roll 로그를 기록.

#### 계단 배치 (최후단 · 독립 RNG · 제외+폴백)

[`place_stairs`](DungeonGenerate_Simulator.py#L1557)

Unity `DungeonManager.PlaceStairForFloor` 이식. 모든 방 라벨이 확정된 **최후단**에서 실행됩니다.

- **가드**: 방이 없거나 `floor >= max_floor`(최고층)면 `"08. 계단 배치 생략"` 후 종료 → 최고층은 계단 없음.
- **독립 RNG**: [`create_stair_seed`](DungeonGenerate_Simulator.py#L1611)(`stair_select` 도메인)로 만든 `DotNetRandom` 으로 방 인덱스를 **Fisher-Yates 셔플**. 지형 RNG 를 소비하지 않아 레이아웃 불변.
- **2단 폴백** ([`try_select_and_carve_stair`](DungeonGenerate_Simulator.py#L1580)):
  1. **제외 적용**: 셔플 순서로 순회하며 [`is_stair_excluded`](DungeonGenerate_Simulator.py#L1600)(스폰 방 · Elite 방 · (옵션)MonsterDen)를 스킵하고, [`try_find_stair_pos`](DungeonGenerate_Simulator.py#L1615) 로 유효 셀을 찾으면 `STAIR_UP` 을 새기고 `stair_room_index` 기록.
  2. **제외 무시 재시도**: 1단계에서 아무 방도 못 찾으면 제외를 무시하고 다시 순회 → 소프트락 방지(계단 무배치 방지).
- **유효 셀**: 방 테두리를 제외한 내부에서, 상하좌우에 `CORRIDOR` 가 인접하지 않은 `ROOM` 셀. 후보 수집 순서(row → col)와 `rng.next(len)` 선택까지 Unity `TryFindStairPosition` 과 동일.
- **회피 타입(`stair_avoid_monster_den`)**: Unity `DungeonManager.stairAvoidTypes` 대응. 스폰·Elite 는 **항상** 제외되며, 이 토글은 추가로 MonsterDen 방을 제외할지 결정. 현재 Unity 씬은 `stairAvoidTypes` 를 빈 배열로 직렬화하므로 뷰어 기본값도 off.
- 스냅샷: `08. 계단 배치 R{idx}`(좌표·`exclusionsApplied`·`stairSeed` 노트) 또는 `08. 계단 배치 생략` / `08. 계단 배치 실패`.

### 3.4 데이터 모델

[DungeonGenerate_Simulator.py:201-320](DungeonGenerate_Simulator.py#L201-L320)

| `@dataclass` | 의미 |
|---|---|
| `DungeonSettings` | 인스펙터 값 미러링 + EXTRA 파라미터(`extra_conn_prob`, `extra_candidate_count`, `extra_neighbor_count`) + 점수 가중치 3종 + MonsterDen 설정(`monster_den_chance`, `max_monster_den_count`) + **시드 2종(`seed`=폴딩 int / `raw_seed`=raw long)** + **`spawn_region`(Dungeon=1)** + **`stair_avoid_monster_den`** + `validate()` + `derive_seed()` |
| `Room` | `x, y, w, h, cx, cy` + `is_elite` + `is_monster_den` + **`is_spawn`** |
| `BSPNode` | 자식 노드 트리, `is_leaf` |
| `ExtraRoomPair` | EXTRA edge 후보 한 쌍(`a`, `b`, `center_dist_sq`) |
| `ExtraCorridorCandidate` | EXTRA 후보의 점수 / 경로 / tie-break 메타데이터 일체 |
| `Step` | `title`, 격자 복사본, 방/노드 스냅샷, 강조 경로, 디버그 노트, `extra_paths`(다색 후보 오버레이 리스트) |

> `seed` 는 지형(BSP/방/통로)용 폴딩 int, `raw_seed` 는 MonsterDen/계단 `create_seed` 에 넣는 raw long 인스펙터 시드입니다. 시드 텍스트가 비어 있으면(랜덤 시드 모드) `raw_seed` 는 0 으로 대체됩니다.

### 3.5 UI 계층

[`DungeonViewer`](DungeonGenerate_Simulator.py#L1641)

- **렌더링**: `Step.grid` → PIL `Image.putdata` (1픽셀 = 1셀) → `Image.resize(NEAREST)` 로 확대 → `ImageDraw` 로 BSP / Room / **EXTRA 다색 후보** / 선택 경로 오버레이 → `ImageTk.PhotoImage`.
- **방 강조**: `is_elite` 면 빨간 테두리(`#ff3b30`) + `R{i}\nELITE` 라벨(연빨강 `#ffb3ad`), `is_monster_den` 이면 보라 테두리(`#c77dff`) + `R{i}\nDEN` 라벨(연보라 `#e0b3ff`), **`is_spawn` 이면 노란 테두리(`#ffd60a`) + `R{i}\nSPAWN` 라벨(연노랑 `#ffe680`)**, 그 외 일반 방은 연두 테두리(`#e8f1a1`) + `R{i}` 라벨. (우선순위: Elite > Den > Spawn)
- **EXTRA 후보 그리기**: 클린 후보 경로는 셀 내부 1px 안쪽 테두리(`cell//3` 두께), 선택 경로는 셀 외곽 두꺼운 테두리(`cell//2` 두께)로 그려 시각적 우선순위 부여.
- **입력 패널**: 기존 `DungeonSettings` 필드 + **`SpawnRegion (Dungeon=1)` 입력란** + **`Stair avoids MonsterDen` 체크박스**.
- **캐시**: `(step_index, cell, show_bsp, show_rooms, show_path)` 키로 PhotoImage 캐시. LRU 96개 제한.
- **타임라인 디바운싱**: `ttk.Scale` 드래그 중 16ms 후 단일 draw로 합침 → 끌어도 부드러움.
- **자동 재생**: `after()` 기반 틱 루프. 기본 180ms 를 Speed 슬라이더(0.2×~5.0×)로 나눠 간격 조절.
- **줌/패닝**: 마우스 휠로 0.5×~5× 줌(커서 위치 기준), 좌클릭 드래그로 맵 이동. 캔버스 우상단에 `Zoom {배율}x | wheel: zoom, drag: pan` 표시.
- **범례**: `EMPTY` / `ROOM` / `CORRIDOR` / `STAIR_UP` / **`SPAWN`** / `ELITE` / `DEN` / `selected path` / `EXTRA candidates` 9종 표시.
- **상태 표시줄**: `Step {i}/{n}   Unity Seed={folded}   cached draw`.

### 3.6 격자 타일 코드

```
EMPTY        = 0   #111217  배경
ROOM         = 1   #6c8f4e  방 바닥
CORRIDOR     = 2   #b88a48  통로
STAIR_UP     = 3   #53d6ff  올라가는 계단
DOOR_CLOSED  = 5   #8c5b2f  (예약, 미사용)
```

> Spawn / Elite / MonsterDen 은 **별도 타일 코드가 아니라** `Room.is_spawn` / `Room.is_elite` / `Room.is_monster_den` 플래그로 표현되며, 방 바닥 타일은 그대로 `ROOM` 입니다. 강조는 방 테두리 색/라벨로만 이루어집니다. (계단만 실제 `STAIR_UP` 타일로 격자에 기입됨)

방 타입 / 오버레이 색:

```
Room outline (Normal)   #e8f1a1
Spawn Room outline      #ffd60a  (SPAWN 라벨, 라벨색 #ffe680)
Elite Room outline      #ff3b30  (ELITE 라벨, 라벨색 #ffb3ad)
MonsterDen outline      #c77dff  (DEN 라벨, 라벨색 #e0b3ff)
selected path           #ff3b30  (현재 강조 경로 / EXTRA pair best)
EXTRA candidates        팔레트 16색 (#00e5ff, #ffd60a, #7cff6b, #ff7ad9, ...)
BSP outline             #3b4a66
```

---

## 4. Unity와 정확히 일치시키려면

1. Unity 인스펙터의 `Seed` 값을 그대로 입력 (12자리 long도 OK). 뷰어는 이 값을 **두 가지로 사용**합니다: 지형용 폴딩 int(`unity_build_settings_seed_from_text`)와 Den/계단용 **raw long**(`create_seed` 에 그대로 투입).
2. 같은 `Floor` 입력.
3. `DungeonSettings` 의 모든 필드(EXTRA 파라미터 + 점수 가중치 3종 + MonsterDen 설정 포함)를 인스펙터와 동일하게 설정. 특히 `ExtraNeighborCount` 가 edge 후보 개수를, `ExtraConnProb` 가 edge별 roll 통과율을 좌우합니다.
4. **`SpawnRegion`** 을 Unity `DungeonManager.currentStageRegion` 과 동일하게(기본 Dungeon=1) 설정. 이 값은 `create_seed` 의 `spawnRegion` 바이트에 직접 반영되어 Den/계단 위치를 바꿉니다.
5. **`Stair avoids MonsterDen`** 토글을 Unity `DungeonManager.stairAvoidTypes` 구성과 맞추세요. 스폰·Elite 는 항상 제외되며, `stairAvoidTypes` 에 `MonsterDen` 을 넣어 두었다면 이 토글을 켜야 계단 위치가 일치합니다. (현재 씬은 빈 배열 → 기본 off)
6. 지형(BSP/방/통로)은 `Seed`/`Floor` 만으로 결정됩니다. **스폰 방은 EXTRA 완성 격자에서 맵중앙 최근접 ROOM 타일**로 결정론적으로 계산됩니다. **MonsterDen·계단 시드**는 raw long `Seed` + `SpawnRegion` + `Floor` + 도메인(`monster_den_select` / `stair_select`)으로 계산되며, 지형 RNG 와 분리돼 있습니다.
7. RNG 호출 순서가 보존되어야 하므로, Unity 측이 지형 RNG 를 추가로 소비하는 변경을 가하면 뷰어도 같은 위치에서 동일 호출을 추가해야 함.
   - Elite Room 선택은 RNG를 소비하지 않으므로 엘리트 유무에 관계없이 EXTRA/스폰/MonsterDen/계단 RNG 흐름이 동일.
   - EXTRA 단계의 지형 RNG 소비 순서: edge shuffle(Fisher-Yates) → edge별 `next_double()` roll → (carve 자체는 RNG 비소비).
   - MonsterDen / 계단은 각자 별도 결정론 RNG(도메인 시드)를 쓰므로 지형 스트림에 영향을 주지 않음. 계단이 최후단으로 이동해도 **맵 레이아웃은 기존 시드와 불변**.

---

## 5. 파일 구성

```
DungeonGenrate_Simulator/
├─ DungeonGenerate_Simulator.py   # 본체 (RNG + Generator + UI 모두 포함된 단일 파일)
└─ readme.md                       # 본 문서
```

이 뷰어는 Unity 프로젝트 파일을 import 하지 않는 **독립 실행형 시각화 도구**입니다.
