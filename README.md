# Proto_JBRL Dungeon Step Viewer

> Build: `Elite floors parity fix 2026-05-20 r17`

Unity `Proto_JBRL` 프로젝트의 `DungeonGenerator` 절차적 생성 알고리즘을 **시드(Seed) 기반으로 결정론적으로 재현**하고, 한 단계씩 시각화해 보여 주는 Python/Tkinter 뷰어입니다.

Unity와 동일한 `Seed` / `Floor` / `DungeonSettings` 입력을 주면 던전 격자(grid)가 그대로 일치합니다. 이를 위해 C#의 `System.Random`(구버전 Mono 스타일) 동작과 `unchecked int` 연산을 파이썬에 1:1 포팅했습니다.

---

## 1. 주요 특징

- **결정론적 재현**: Unity의 시드 폴딩 규칙(`seed % int.MaxValue`)과 `System.Random` 알고리즘을 그대로 포팅 → 동일 입력 → 동일 맵.
- **스텝 단위 시각화**: BSP 분할 → 방 배치 → 방 채움 → MST 통로 → **Elite Room 선택** → **EXTRA 통로(체크 → pair 스킵 → 후보군 → pair-best 모음 → carve)** → 계단 배치 전 과정을 스냅샷으로 저장하고, 화살표 / 슬라이더 / Play 버튼으로 스크럽 가능.
- **Elite Room**: 층 번호 끝자리가 5인 플로어(5F, 15F, 25F, ...)에서 MST 완료 후 결정론적으로 리프 방을 하나 선택. 선택된 방은 분홍 테두리(`#ff4fd8`)와 `ELITE` 라벨로 강조되며, 해당 방을 포함하는 쌍은 EXTRA 후보에서 제외됨. RNG를 소비하지 않아 이후 EXTRA / 계단 흐름과 패리티가 유지됨.
- **EXTRA 다중 후보 오버레이**: 한 스텝에서 여러 후보 경로를 **16색 팔레트로 동시 표시**하고, 그 중 선택된(best) 경로를 빨간 두꺼운 테두리로 강조.
- **점수 가중치 노출**: EXTRA 점수 계산식의 `overlap`, `path_len`, `center_distance` 가중치를 인스펙터에서 직접 입력해 튜닝.
- **Unity 설정 일치**: `MapWidth`, `MapHeight`, `MinRoomSize`, `MaxRoomSize`, `BspDepth`, `Padding`, `ExtraConnProb`, `ExtraCandidateCount`, `ExtraOverlapWeight`, `ExtraPathLenWeight`, `ExtraCenterDistanceDivisor`, `MinStraight`, `MaxFloor` 모든 인스펙터 값을 UI에서 그대로 입력.
- **줌 / 패닝**: 마우스 휠로 줌 인/아웃, 좌클릭 드래그로 맵 이동. 캔버스 우상단에 현재 줌 배율 표시.
- **성능 최적화**: PIL `Image.resize(NEAREST)` 기반 렌더링 + 스텝별 `PhotoImage` 캐시(LRU 96개) + 타임라인 디바운싱.

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
│   ├─ DungeonSettings.derive_seed() (Seed × Floor 폴딩)     │
│   ├─ BSP split → collect_rooms → fill_room                 │
│   ├─ connect_mst_all (MST)                                 │
│   ├─ assign_elite_room (결정론적 Elite Room 선택)            │
│   ├─ connect_extra_corridors (n-1회 시도, 점수 기반)         │
│   │    └─ build_extra_candidate_overlays (다색 오버레이)    │
│   └─ place_stairs                                          │
│   └─ snapshot(...) → Step 리스트                           │
└──────────────────────────┬────────────────────────────────┘
                           │ List[Step]
                           ▼
                      UI 가 step 단위로 렌더링
```

### 3.2 결정론을 만드는 두 축

#### (1) `DotNetRandom` — C# `System.Random` 포팅

[DungeonGenerate_Simulator.py:73](DungeonGenerate_Simulator.py#L73)

- 구버전 Mono / .NET Framework의 **subtractive generator**를 그대로 재현.
- `seed_array[56]`, `inext = 0`, `inextp = 21` 초기 상태, `MBIG = int.MaxValue`, `MSEED = 161803398` 등 동일.
- `Next()`, `Next(maxValue)`, `Next(minValue, maxValue)`, `NextDouble()` 시그니처 일치.
- 시드 음수/INT_MIN 처리, 큰 범위(`> int.MaxValue`)일 때의 `GetSampleForLargeRange()` 분기까지 포함.

부가적으로 C#의 `unchecked int` 산술을 위한 헬퍼:

- `uint32(v)` — 32비트 부호 없는 wrap-around
- `int32(v)` — 32비트 부호 있는 wrap-around
- `csharp_remainder(a, b)` — 피제수의 부호를 따라가는 C# `%` 연산

#### (2) Seed × Floor 폴딩

[DungeonGenerate_Simulator.py:191](DungeonGenerate_Simulator.py#L191)

```python
def derive_seed(self) -> int:
    seed_value = self.seed if self.seed is not None else 0
    s = uint32(seed_value)
    floor_mix = uint32(self.floor * 2654435761)
    mixed = uint32((s ^ floor_mix) * 2246822519)
    return mixed & INT_MAX
```

Unity의 `DungeonManager.BuildSettings()`가 12자리 long 시드를 `int`로 축약하는 규칙(`seed % int.MaxValue`)도 [`unity_build_settings_seed_from_text`](DungeonGenerate_Simulator.py#L65) 가 그대로 포팅. 두 단계(인스펙터 폴딩 + 플로어 mixing)를 거쳐야 같은 RNG 스트림이 나옵니다.

> **중요**: `SpawnRegion`은 지형 생성에 사용되지 않습니다. 시드는 `Seed`와 `Floor` 값만으로 결정됩니다.

### 3.3 생성 파이프라인

[`DungeonGenerator.generate()`](DungeonGenerate_Simulator.py#L322) 이 단계마다 `snapshot()` 을 호출해 `Step` 리스트를 누적합니다.

| 단계 | 메서드 | 역할 |
|---|---|---|
| 00 | `BSPNode(root)` | 전체 맵을 하나의 노드로 초기화 |
| 01 | `bsp_split` | 가로/세로 기준 재귀 분할. `bsp_depth` 또는 최소 분할 크기에서 종료. 가로:세로 비율 1.25 임계로 축 선택 |
| 02 | `collect_rooms` | 리프 노드마다 `[min_room_size, max_room_size]` 범위로 방 크기/위치 결정 |
| 03 | `fill_room` | 방 셀을 `ROOM` 타일로 채움 |
| 04 | `connect_mst_all` | **MST**: 연결된 방 집합에서 가장 가까운 미연결 방을 골라 L자 통로로 연결 |
| 04E | `assign_elite_room` | **Elite Room**: 층 끝자리가 5인 플로어에서만 MST 리프 방 중 하나를 결정론적으로 선택. RNG 비소비. |
| 05 | `connect_extra_corridors` | **EXTRA**: n-1회 시도를 돌며 체크 → pair 스킵 → 후보군 → pair-best 모음 → carve 의 스텝을 생성 |
| 06 | `place_stairs` | 셔플된 방 순서로 적합한 셀을 찾아 `STAIR_UP` 배치 (`floor < max_floor` 조건) |
| 99 | 최종 | MST + EXTRA + 계단이 모두 적용된 최종 던전 스냅샷 |

#### MST 단계

[`connect_mst_all`](DungeonGenerate_Simulator.py#L401)

- 방 0번을 시드로, 매 반복마다 `connected` 집합과 `remaining` 집합 사이 유클리드 거리 최소 쌍을 그리디 선택.
- L자 통로(`draw_l_corridor`)는 primary 방향 → 실패 시 alternate 방향 → 강제 fallback(mandatory).
- 결과로 `connected_pairs: Set[(i, j)]` 누적 → EXTRA 단계에서 중복 회피.
- `mst_edges` 목록도 반환 → `assign_elite_room`에 전달.

#### Elite Room 선택

[`assign_elite_room`](DungeonGenerate_Simulator.py#L434)

- **적용 조건**: `floor % 10 == 5` (5F, 15F, 25F, …). 비해당 층은 `"04E. Elite Room 없음"` 스냅샷만 남기고 종료.
- MST의 degree == 1인 리프 방 중에서 R0을 제외하고, 깊이(내림차순) → 거리(내림차순) → 인덱스(오름차순) 순으로 정렬해 첫 번째를 선택.
- **RNG를 소비하지 않는다**. 이 덕분에 Elite/비Elite 층 모두 EXTRA 통로와 계단 배치의 RNG 흐름이 동일하게 유지됨.
- 선택된 방을 포함하는 모든 pair는 EXTRA 후보 생성 단계에서 스킵됨.

#### EXTRA 통로 단계

[`connect_extra_corridors`](DungeonGenerate_Simulator.py#L508)

MST 완료 후 n-1회 시도를 순서대로 수행합니다.

각 시도의 스텝 구성:

| 스텝 타이틀 | 내용 |
|---|---|
| `05-{N:02d}. EXTRA 생성 체크: 진행` | `ExtraConnProb` 롤 통과 — 이번 시도는 EXTRA를 시도함 |
| `05-{N:02d}. EXTRA 생성 체크: 스킵` | 롤 실패 — 이번 시도는 EXTRA를 만들지 않음 |
| `05-{N:02d}. pair R{src} -> R{dst} 스킵` | Elite Room을 포함하거나 이미 연결된 pair는 스킵 |
| `05-{N:02d}. R{src} -> R{dst} 후보 없음` | 거절 규칙을 통과한 후보가 없을 때, 거절 사유 목록과 함께 표시 |
| `05-{N:02d}. R{src} -> R{dst} 후보군 + pair best` | **그 쌍의 모든 클린 후보를 16색 팔레트로 동시 표시**, pair-best는 빨간 두꺼운 테두리 |
| `05-{N:02d}. EXTRA pair-best 후보군 + 최종 best` | **그 시도의 모든 pair-best를 한 화면에 모아 표시**, 그중 최종 best를 빨간 강조 |
| `05-{N:02d}. EXTRA 통로 생성 R{src} -> R{dst}` | 최종 best를 carve. 격자에 `CORRIDOR` 가 실제로 새겨지는 순간 |
| `05-{N:02d}. EXTRA 선택 없음` | 모든 쌍에서 후보가 비었을 때 |

알고리즘 본체:

1. 각 시도마다 RNG `next_double()` 한 번으로 `roll`. `roll >= ExtraConnProb` 면 스킵.
2. 통과하면 모든 미연결 room pair를 검사 (단, Elite Room을 포함하는 pair는 건너뜀).
3. 한 쌍당 `ExtraCandidateCount` 변형 경로 생성([`emit_extra_path_candidate`](DungeonGenerate_Simulator.py#L741), [`pick_extra_axis`](DungeonGenerate_Simulator.py#L759)).
4. 각 후보는 **8개 거절 규칙**([`corridor_candidate_reject_reason`](DungeonGenerate_Simulator.py#L1019))으로 필터링:
   - `third-room`, `bad-door-run`, `orphan-door-stub`, `room-perimeter-corridor`, `corner-doorway`, `diagonal-room-stub`, `outward-room-stub`, `third-room-parallel`
5. 살아남은 후보는 [`score_extra_corridor_candidate`](DungeonGenerate_Simulator.py#L822) 로 점수화 — **가중치가 UI에서 노출**됨:
   ```
   score = overlap × ExtraOverlapWeight (default 20)
         − path_len × ExtraPathLenWeight (default 8)
         − center_dist_sq ÷ ExtraCenterDistanceDivisor (default 20)
   ```
   > `parallel_run` 은 디버그 노트용으로 측정되지만 **점수에서 차감되지 않습니다.**
6. 쌍별 최선([`select_best_extra_corridor_candidate`](DungeonGenerate_Simulator.py#L711)) → 시도 전체에서 다시 최선 → carve. 동률 시 [`is_better_extra_corridor_candidate`](DungeonGenerate_Simulator.py#L719) 의 결정론적 tie-break(score / path_len / overlap / dist / src / dst / candidate / attempt / primary) 적용.
7. carve 한 쌍은 `connected_pairs` 에 추가되어 이후 시도에서 중복 선택되지 않음.

#### EXTRA 후보 오버레이

[`build_extra_candidate_overlays`](DungeonGenerate_Simulator.py#L295)

- 한 스텝에 여러 후보 경로를 함께 표시하기 위한 `(path, color, label)` 리스트를 만듦.
- 16색 고대비 팔레트 ([`extra_candidate_color`](DungeonGenerate_Simulator.py#L285)) 가 후보별로 순차 배정 — `#00e5ff`, `#ffd60a`, `#7cff6b`, `#ff7ad9`, ...
- 선택된 best 후보는 팔레트가 아닌 빨간색 (`#ff3b30`) 으로 별도 표시.
- 각 후보 경로의 첫 번째 셀에 `C{n}: R{src}->{dst} score={score}`, 선택됨은 `SELECTED R{src}->{dst} score={score}` 라벨이 작은 텍스트로 그려짐.

### 3.4 데이터 모델

[DungeonGenerate_Simulator.py:149-252](DungeonGenerate_Simulator.py#L149-L252)

| `@dataclass` | 의미 |
|---|---|
| `DungeonSettings` | 인스펙터 값 미러링 + 점수 가중치 3종 + `validate()` + `derive_seed()` |
| `Room` | `x, y, w, h, cx, cy` |
| `BSPNode` | 자식 노드 트리, `is_leaf` |
| `ExtraCorridorCandidate` | EXTRA 후보의 점수 / 경로 / tie-break 메타데이터 일체 |
| `Step` | `title`, 격자 복사본, 방/노드 스냅샷, 강조 경로, 디버그 노트, `extra_paths`(다색 후보 오버레이 리스트), **`elite_room_index`** |

### 3.5 UI 계층

[`DungeonViewer`](DungeonGenerate_Simulator.py#L1333)

- **렌더링**: `Step.grid` → PIL `Image.putdata` (1픽셀 = 1셀) → `Image.resize(NEAREST)` 로 확대 → `ImageDraw` 로 BSP / Room / **EXTRA 다색 후보** / 선택 경로 오버레이 → `ImageTk.PhotoImage`.
- **Elite Room 표시**: `step.elite_room_index`가 방 인덱스와 일치하면 분홍 테두리(`#ff4fd8`, 굵기 3) + `ELITE` 라벨로 강조.
- **EXTRA 후보 그리기**: 클린 후보 경로는 셀 내부 1px 안쪽 테두리(`cell//3` 두께), 선택 경로는 셀 외곽 두꺼운 테두리(`cell//2` 두께)로 그려 시각적 우선순위 부여.
- **캐시**: `(step_index, cell, show_bsp, show_rooms, show_path)` 키로 PhotoImage 캐시. LRU 96개 제한.
- **타임라인 디바운싱**: `ttk.Scale` 드래그 중 16ms 후 단일 draw로 합침 → 끌어도 부드러움.
- **자동 재생**: `after(180ms)` 기반 틱 루프.
- **줌/패닝**: 마우스 휠로 0.5×~10× 줌(커서 위치 기준), 좌클릭 드래그로 맵 이동. 캔버스 우상단에 `Zoom {배율}x | wheel: zoom, drag: pan` 표시.
- **범례**: `EMPTY` / `ROOM` / `ELITE` / `CORRIDOR` / `STAIR_UP` / `selected path` / `EXTRA candidates` 7종 표시.

### 3.6 격자 타일 코드

```
EMPTY             = 0   #111217  배경
ROOM              = 1   #6c8f4e  방 바닥
CORRIDOR          = 2   #b88a48  통로
STAIR_UP          = 3   #53d6ff  올라가는 계단
DOOR_CLOSED       = 5   #8c5b2f  (예약)
ELITE_ROOM_MARKER = 98  #ff4fd8  Elite Room 바닥
```

오버레이 색:

```
selected path        #ff3b30  (현재 강조 경로 / EXTRA final best)
EXTRA candidates     팔레트 16색 (#00e5ff, #ffd60a, #7cff6b, #ff7ad9, ...)
Elite Room outline   #ff4fd8  (방 테두리 굵기 3, ELITE 라벨)
Room outline         #e8f1a1
BSP outline          #3b4a66
```

---

## 4. Unity와 정확히 일치시키려면

1. Unity 인스펙터의 `Seed` 값을 그대로 입력 (12자리 long도 OK — `unity_build_settings_seed_from_text` 가 폴딩).
2. 같은 `Floor` 입력.
3. `DungeonSettings` 의 모든 필드(점수 가중치 3종 포함)를 인스펙터와 동일하게 설정.
4. `SpawnRegion` 값은 지형 생성에 영향을 주지 않으므로 입력 불필요.
5. RNG 호출 순서가 보존되어야 하므로, Unity 측 `DungeonGenerator` 가 RNG를 추가로 소비하는 변경을 가하면 뷰어도 같은 위치에서 동일 호출을 추가해야 함.
   - Elite Room 선택은 RNG를 소비하지 않으므로 엘리트 유무에 관계없이 EXTRA/계단 RNG 흐름이 동일.

---

## 5. 파일 구성

```
DungeonGenrate_Simulator/
├─ DungeonGenerate_Simulator.py   # 본체 (RNG + Generator + UI 모두 포함된 단일 파일)
└─ README.md                       # 본 문서
```

이 뷰어는 Unity 프로젝트 파일을 import 하지 않는 **독립 실행형 시각화 도구**입니다.
