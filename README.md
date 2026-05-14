# Proto_JBRL Dungeon Step Viewer

Unity `Proto_JBRL` 프로젝트의 `DungeonGenerator` 절차적 생성 알고리즘을 **시드(Seed) 기반으로 결정론적으로 재현**하고, 한 단계씩 시각화해 보여 주는 Python/Tkinter 뷰어입니다.

Unity와 동일한 `Seed` / `Floor` / `DungeonSettings` 입력을 주면 던전 격자(grid)가 그대로 일치합니다. 이를 위해 C#의 `System.Random`(구버전 Mono 스타일) 동작과 `unchecked int` 연산을 파이썬에 1:1 포팅했습니다.

---

## 1. 주요 특징

- **결정론적 재현**: Unity의 시드 폴딩 규칙(`seed % int.MaxValue`)과 `System.Random` 알고리즘을 그대로 포팅 → 동일 입력 → 동일 맵.
- **스텝 단위 시각화**: BSP 분할 → 방 배치 → 방 채움 → MST 통로 → EXTRA 통로 → 계단 배치 전 과정을 스냅샷으로 저장하고, 화살표 / 슬라이더 / Play 버튼으로 스크럽 가능.
- **Unity 설정 일치**: `MapWidth`, `MapHeight`, `MinRoomSize`, `MaxRoomSize`, `BspDepth`, `Padding`, `ExtraConnProb`, `ExtraCandidateCount`, `MinStraight`, `MaxFloor` 모든 인스펙터 값을 UI에서 그대로 입력.
- **성능 최적화**: PIL `Image.resize(NEAREST)` 기반 렌더링 + 스텝별 `PhotoImage` 캐시 + 타임라인 디바운싱.

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
python DungeonGenrate_Simulator.py
```

### 조작 키

| 입력 | 동작 |
|---|---|
| `Seed` / `Floor` 입력 → `Generate` 또는 `Enter` | 던전 재생성 |
| `←` / `→` | 이전 / 다음 스텝 |
| `Home` / `End` | 첫 / 마지막 스텝 |
| `Space` | 자동 재생 / 일시정지 |
| Timeline 슬라이더 | 임의 스텝으로 스크럽 |
| `BSP` / `Rooms` / `Path` 토글 | 오버레이 표시 토글 |

---

## 3. 아키텍처 분석

### 3.1 전체 구조

```
┌───────────────────────────────────────────────────────────┐
│  DungeonViewer (Tkinter UI)                               │
│   ├─ 입력 패널 (Seed / Floor / DungeonSettings)           │
│   ├─ Canvas (PIL 렌더링 + 캐시)                            │
│   └─ Timeline / Play / Step 컨트롤                         │
└──────────────────────────┬────────────────────────────────┘
                           │ generate()
                           ▼
┌───────────────────────────────────────────────────────────┐
│  DungeonGenerator                                          │
│   ├─ DotNetRandom (C# System.Random 포팅)                  │
│   ├─ DungeonSettings.derive_seed() (Seed × Floor 폴딩)     │
│   ├─ BSP split → collect_rooms → fill_room                 │
│   ├─ connect_all (MST)                                     │
│   ├─ connect_extra_corridors (점수 기반 EXTRA)             │
│   └─ place_stairs                                          │
│   └─ snapshot(...) → Step 리스트                           │
└──────────────────────────┬────────────────────────────────┘
                           │ List[Step]
                           ▼
                      UI 가 step 단위로 렌더링
```

### 3.2 결정론을 만드는 두 축

#### (1) `DotNetRandom` — C# `System.Random` 포팅

[DungeonGenrate_Simulator.py:67](DungeonGenrate_Simulator.py#L67)

- 구버전 Mono / .NET Framework의 **subtractive generator**를 그대로 재현.
- `seed_array[56]`, `inext = 0`, `inextp = 21` 초기 상태, `MBIG = int.MaxValue`, `MSEED = 161803398` 등 동일.
- `Next()`, `Next(maxValue)`, `Next(minValue, maxValue)`, `NextDouble()` 시그니처 일치.
- 시드 음수/INT_MIN 처리, 큰 범위(`> int.MaxValue`)일 때의 `GetSampleForLargeRange()` 분기까지 포함.

부가적으로 C#의 `unchecked int` 산술을 위한 헬퍼:

- `int32(v)` — 32비트 wrap-around
- `csharp_remainder(a, b)` — 피제수의 부호를 따라가는 C# `%` 연산

#### (2) Seed × Floor 폴딩

[DungeonGenrate_Simulator.py:179](DungeonGenrate_Simulator.py#L179)

```python
def derive_seed(self) -> int:
    s = self.seed if self.seed is not None else 0
    a = int32(self.floor * int32(2654435761))
    b = int32(s ^ a)
    mixed = int32(b * int32(2246822519))
    return mixed & 0x7FFFFFFF
```

Unity의 `DungeonManager.BuildSettings()`가 12자리 long 시드를 `int`로 축약하는 규칙(`seed % int.MaxValue`)도 [`unity_build_settings_seed_from_text`](DungeonGenrate_Simulator.py#L59) 가 그대로 포팅. 두 단계(인스펙터 폴딩 + 플로어 mixing)를 거쳐야 같은 RNG 스트림이 나옵니다.

### 3.3 생성 파이프라인

[`DungeonGenerator.generate()`](DungeonGenrate_Simulator.py#L256) 이 단계마다 `snapshot()` 을 호출해 `Step` 리스트를 누적합니다.

| 단계 | 메서드 | 역할 |
|---|---|---|
| 00 | `BSPNode(root)` | 전체 맵을 하나의 노드로 초기화 |
| 01 | `bsp_split` | 가로/세로 기준 재귀 분할. `bsp_depth` 또는 최소 분할 크기에서 종료. 가로:세로 비율 1.25 임계로 축 선택 |
| 02 | `collect_rooms` | 리프 노드마다 `[min_room_size, max_room_size]` 범위로 방 크기/위치 결정 |
| 03 | `fill_room` | 방 셀을 `ROOM` 타일로 채움 |
| 04 | `connect_all` | **MST**: 연결된 방 집합에서 가장 가까운 미연결 방을 골라 L자 통로로 연결 |
| 05 | `connect_extra_corridors` | **EXTRA**: 점수가 높은 추가 통로를 선택적으로 더 깔아 단방향 트리 → 사이클 보강 |
| 06 | `place_stairs` | 셔플된 방 순서로 적합한 셀을 찾아 `STAIR_UP` 배치 (`floor < max_floor` 조건) |

#### MST 단계

[`connect_all`](DungeonGenrate_Simulator.py#L332)

- 방 0번을 시드로, 매 반복마다 `connected` 집합과 `remaining` 집합 사이 유클리드 거리 최소 쌍을 그리디 선택.
- L자 통로(`draw_l_corridor`)는 primary 방향 → 실패 시 alternate 방향 → 강제 fallback(mandatory).
- 결과로 `mst_pairs: Set[(i, j)]` 누적 → EXTRA 단계에서 중복 회피.

#### EXTRA 통로 단계

[`connect_extra_corridors`](DungeonGenrate_Simulator.py#L377)

Unity 최신 플로우와 정확히 동일한 순서로 구성되어 있어 가장 정교한 부분:

1. 최대 `rooms.Count - 1` 회 시도.
2. 각 시도마다 `ExtraConnProb` 롤. 실패 시 그 라운드 스킵(스냅샷에 roll 값 기록).
3. 성공 시 **현재까지 연결되지 않은 모든 방 쌍**을 안정적 `i < j` 순서로 검사.
4. 한 쌍당 `ExtraCandidateCount` 변형 경로 생성 (`emit_extra_path_candidate`, `pick_extra_axis`).
5. 각 후보는 **8개 거절 규칙**(`corridor_candidate_reject_reason`)으로 필터링:
   - `room-perimeter-corridor`, `corner-doorway`, `third-room`, `bad-door-run`, `orphan-door-stub`, `outward-room-stub`, `diagonal-room-stub`, `third-room-parallel`
6. 살아남은 후보는 [`score_extra_corridor_candidate`](DungeonGenrate_Simulator.py#L645) 로 점수화:
   ```
   score = overlap × 60  −  path_len × 8  −  center_dist_sq / 20  −  parallel_run × 25
   ```
7. 쌍별 최선 → 라운드 전체 최선 → carve. 동률 시 [`is_better_extra_corridor_candidate`](DungeonGenrate_Simulator.py#L542) 의 결정론적 tie-break(score / path_len / overlap / dist / src / dst / candidate / attempt / primary).

이 점수와 결정론적 tie-break이 일치해야 Unity와 같은 EXTRA 통로가 선택됩니다.

### 3.4 데이터 모델

[DungeonGenrate_Simulator.py:140-235](DungeonGenrate_Simulator.py#L140-L235)

| `@dataclass` | 의미 |
|---|---|
| `DungeonSettings` | 인스펙터 값 미러링 + `validate()` + `derive_seed()` |
| `Room` | `x, y, w, h, cx, cy` |
| `BSPNode` | 자식 노드 트리, `is_leaf` |
| `ExtraCorridorCandidate` | EXTRA 후보의 점수 / 경로 / tie-break 메타데이터 일체 |
| `Step` | `title`, 격자 복사본, 방/노드 스냅샷, 강조 경로, 디버그 노트 |

### 3.5 UI 계층

[`DungeonViewer`](DungeonGenrate_Simulator.py#L1168)

- **렌더링**: `Step.grid` → PIL `Image.putdata` (1픽셀 = 1셀) → `Image.resize(NEAREST)` 로 확대 → `ImageDraw` 로 BSP / Room / Path 오버레이 → `ImageTk.PhotoImage`.
- **캐시**: `(step_index, cell, show_bsp, show_rooms, show_path)` 키로 PhotoImage 캐시. LRU 96개 제한.
- **타임라인 디바운싱**: `ttk.Scale` 드래그 중 16ms 후 단일 draw로 합침([`on_scale`](DungeonGenrate_Simulator.py#L1298)) → 끌어도 부드러움.
- **자동 재생**: `after(180ms)` 기반 틱 루프(`_schedule_play_tick`).

### 3.6 격자 타일 코드

```
EMPTY        = 0   #111217  배경
ROOM         = 1   #6c8f4e  방 바닥
CORRIDOR     = 2   #b88a48  통로
STAIR_UP     = 3   #53d6ff  올라가는 계단
DOOR_CLOSED  = 5   #8c5b2f  (예약)
```

---

## 4. Unity와 정확히 일치시키려면

1. Unity 인스펙터의 `Seed` 값을 그대로 입력 (12자리 long도 OK — `unity_build_settings_seed_from_text` 가 폴딩).
2. 같은 `Floor` 입력.
3. `DungeonSettings` 의 모든 필드를 인스펙터와 동일하게 설정.
4. RNG 호출 순서가 보존되어야 하므로, Unity 측 `DungeonGenerator` 가 RNG를 추가로 소비하는 변경을 가하면 뷰어도 같은 위치에서 동일 호출을 추가해야 함.

---

## 5. 파일 구성

```
DungeonGenrate_Simulator/
├─ DungeonGenrate_Simulator.py    # 본체 (RNG + Generator + UI 모두 포함된 단일 파일)
└─ README.md                       # 본 문서
```

이 뷰어는 Unity 프로젝트 파일을 import 하지 않는 **독립 실행형 시각화 도구**입니다.
