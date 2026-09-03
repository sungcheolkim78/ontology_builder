# `backend/data` 레이아웃 개선 기획안

작성일: 2026-09-03. **2026-09-03에 구현 완료** — 코드와 실제
`backend/data`가 모두 아래 4번 "제안 구조"로 마이그레이션됨. 이 문서는
당시의 설계 검토 기록으로 남겨둔다.

## 1. 현재 구조

```text
backend/data/
├── {stem}_raw.md                  # 파싱된 문서 본문 (DATA_DIR 바로 아래, 평면 구조)
├── ...다른 문서들...
├── .gitkeep
├── chunks/
│   └── {stem}.json                # 조(條) 단위 청크 (오늘 추가됨)
├── graph/
│   ├── graph.ladybugdb(+ .wal 등) # 모든 문서가 공유하는 단일 그래프 DB
│   └── {stem}/
│       ├── manifest.json          # original_filename, converter
│       ├── summary.json           # 문서 요약 (오늘 추가됨)
│       ├── discovery.json
│       ├── versions.json
│       └── schema_v{N}.json
└── domain_schemas/
    └── {domain}/
        ├── schema.json
        ├── manifest.json
        └── pending_review.json
```

핵심 특징: 문서 하나의 자료가 **두 곳**에 나뉘어 있다 — 본문(`{stem}_raw.md`)은
`DATA_DIR` 바로 아래, 나머지 전부(`manifest.json`, `summary.json`,
`discovery.json`, 스키마 버전들)는 `graph/{stem}/` 아래. 오늘 추가한 청크는
세 번째 위치(`chunks/{stem}.json`)에 또 놓였다.

`GET /api/files`와 `GET /api/documents`(`backend/app/main.py`)는 둘 다
`DATA_DIR.iterdir()`로 "점(`.`)으로 시작하지 않는 파일"을 그대로 문서 목록으로
취급한다 (`docs/SPEC.md`에도 이 전제가 명시돼 있음: "GET /api/files lists
everything directly in backend/data/ as a user document"). 즉 지금 구조가
평면인 이유는 이 두 엔드포인트가 "DATA_DIR 바로 아래 = 문서"라는 가장 단순한
규칙에 의존하고 있기 때문이다. `graph/`, `chunks/`, `domain_schemas/`가
문서로 오인되지 않는 것은 이들이 디렉터리이기 때문(`p.is_file()` 체크)이며,
파일 이름 규칙(`_raw.md` 접미사)과 디렉터리 이름 규칙이 우연히 충돌하지 않고
있을 뿐 구조적으로 보장되지는 않는다. 실제로 `backend/data`에는 이미
`_raw` 접미사가 없는 예전 방식 `.md` 파일들이 섞여 있어(수동 복사로 추정),
이 규칙이 한 번 흐트러진 전례가 있다.

## 2. 문제점

1. **문서 자료가 세 군데로 흩어짐.** 문서 하나(`report_raw`)를 완전히
   이해/백업/삭제하려면 `DATA_DIR/report_raw.md`, `graph/report_raw/*`,
   `chunks/report_raw.json` 세 경로를 모두 찾아야 한다. 삭제 시 하나라도
   빠뜨리면 고아 파일이 남는다.
2. **새 문서-단위 산출물을 추가할 때마다 새 최상위 디렉터리가 필요.**
   오늘 청크를 추가하면서 `chunks/`라는 최상위 디렉터리가 하나 더 생겼다.
   다음에 임베딩 캐시, OCR 로그, 추출 이미지 등을 추가하면 또 새
   최상위 디렉터리 + 그에 대응하는 `..._path_for(stem)` 헬퍼 +
   목록/삭제/백업 로직에 대한 인지가 각각 필요하다 — 문서 단위로 묶는
   경계가 없어 산출물 종류가 늘어날수록 흩어지는 위치도 선형으로 늘어난다.
3. **평면 루트가 파일명 규칙에 암묵적으로 의존.** `DATA_DIR` 바로 아래에
   문서 파일과 비-문서 디렉터리가 같이 있고, `/api/files`·`/api/documents`는
   "파일이면 문서"로 가정한다. 앞으로 문서가 아닌 파일(예: 내보내기 결과,
   임시 파일)이 실수로 `DATA_DIR` 루트에 놓이면 그대로 문서 목록에 노출된다.
4. **백업/정리/테스트 스크립트가 위치를 개별적으로 알아야 함.**
   `scripts/backup_data.sh`, 테스트의 `clean_dirs`류 픽스처
   (`tests/test_ontology.py`의 `DATA_DIR, GRAPH_DIR, DOMAIN_SCHEMA_DIR`
   나열 등)가 매번 "지금 몇 개의 최상위 디렉터리가 있는지"를 알고 있어야
   한다. 새 산출물 종류를 추가할 때마다 이 목록들도 같이 고쳐야 하며,
   빠뜨려도 에러 없이 조용히 오래된 데이터가 남는다.
5. **문서-단위 데이터와 전역(도메인/공유) 데이터의 경계가 불명확.**
   `graph.ladybugdb`(모든 문서가 공유하는 그래프 DB)와
   `domain_schemas/{domain}`(여러 문서에 걸친 도메인 스키마)은 성격상
   "전역" 데이터인데, `graph/{stem}/`(문서 전용 데이터)와 같은 깊이에
   나란히 있어 파일 트리만 봐서는 이 구분이 드러나지 않는다.

## 3. 설계 목표

- **문서 하나 = 폴더 하나.** 한 문서에 속한 모든 산출물(본문, manifest,
  요약, discovery, 청크, 스키마 버전들)이 같은 폴더 아래에 있어야 한다.
  삭제/이동/백업이 그 폴더 하나를 대상으로 끝나야 한다.
- **새 산출물 종류 추가 시 새 최상위 디렉터리가 필요 없어야 한다.**
  "문서별 폴더 안에 파일 하나 더 쓰기"로 끝나야 하며, 그 파일 경로를
  계산하는 헬퍼 함수 하나만 새로 추가하면 충분해야 한다.
- **문서-단위 데이터 vs 전역 데이터의 경계를 디렉터리 구조로 드러낸다.**
  `documents/`(문서별) vs 그 바깥(전역: 그래프 DB, 도메인 스키마)으로
  한눈에 구분되게 한다.
- **목록 조회가 여전히 단순해야 한다.** `/api/files`, `/api/documents`가
  하나의 글롭(glob) 패턴으로 문서 목록을 구할 수 있어야 한다.
- **기존 백업/복원 스크립트, LadybugDB 파일 경로, 도메인 스키마 저장
  방식은 그대로 둔다.** 이 기획안은 문서-단위 저장 구조만 정리 대상으로
  하며, 그래프 DB나 도메인 스키마 계층은 건드리지 않는다(그래프 DB가
  여러 문서를 공유하는 것은 `CLAUDE.md`에 명시된 의도된 설계).

## 4. 제안 구조

```text
backend/data/
├── documents/
│   └── {stem}/
│       ├── raw.md              # 기존 {stem}_raw.md
│       ├── manifest.json       # original_filename, converter
│       ├── summary.json        # 문서 요약
│       ├── discovery.json      # 온톨로지 발견 리포트
│       ├── chunks.json         # 조(條) 단위 청크
│       ├── versions.json       # 스키마 버전 메타
│       └── schema_v{N}.json    # 버전별 스키마
├── graph/
│   └── graph.ladybugdb(+ .wal 등)   # 전역 공유 그래프 DB (변경 없음)
└── domain_schemas/
    └── {domain}/                    # 전역 도메인 스키마 (변경 없음)
        ├── schema.json
        ├── manifest.json
        └── pending_review.json
```

`documents/{stem}/`는 오늘 `graph/{stem}/`가 하던 역할을 그대로 흡수하고,
거기에 본문(`raw.md`)과 청크(`chunks.json`)를 추가로 옮겨온 것뿐이다 —
새로운 개념을 도입하는 게 아니라 이미 있던 "문서별 폴더" 패턴
(`graph_dir_for(stem)`)을 전체 문서 산출물로 확장하는 것.

앞으로 임베딩 캐시나 OCR 로그 같은 걸 추가하고 싶으면
`documents/{stem}/embeddings_cache.json`처럼 파일 하나 추가 + 경로 헬퍼
하나 추가로 끝난다.

## 5. 필요한 코드 변경 (참고용 체크리스트 — 실행 전 상세 설계 필요)

- `app/paths.py`: `data_dir()`는 그대로 두고, `documents_dir()` 같은
  헬퍼를 추가하거나 `app/ontology.py`의 `graph_dir_for(stem)`을
  `documents_dir() / stem`을 가리키도록 바꾼다.
- `app/parser.py`, `app/chunking.py`: `{stem}_raw.md`를
  `documents/{stem}/raw.md`에 쓰도록 변경. 파일명이 더 이상 stem을
  포함하지 않으므로(`raw.md`로 고정) `_document_path()`/`_stem()`
  (`app/main.py`)도 "파일명 → stem" 대신 "stem → 폴더 경로"로 뒤집어야
  한다.
- `app/chunking.py`: `CHUNK_DIR`를 없애고 `chunks.json`을
  `documents/{stem}/` 안에 쓰도록 변경.
- `app/ontology.py`: `graph_dir_for`, `versions_path`,
  `discovery_path_for`, `summary_path_for`가 전부 이미 "stem →
  하위 경로" 패턴이므로 base 디렉터리만 `GRAPH_DIR`에서
  `documents_dir()`로 바꾸면 대부분 그대로 동작한다.
- `app/main.py`: `GET /api/files`, `GET /api/documents`를
  `DATA_DIR.iterdir()` 대신 `documents_dir().iterdir()` (하위 디렉터리
  나열) 방식으로 변경 — 문서 판별이 "파일이냐"에서 "폴더 안에 raw.md가
  있느냐"로 바뀐다. `GET /api/files/{filename}`처럼 파일명을 그대로
  받는 API들의 시그니처(쿼리 파라미터 등)는 유지하되 내부에서 stem 폴더로
  변환하도록 조정.
- `app/graphdb.py`: 변경 없음 (`DB_PATH`는 `graph/graph.ladybugdb`
  그대로 — 전역 데이터).
- `scripts/backup_data.sh` / `scripts/restore_data.sh`: 최상위 디렉터리
  목록이 `documents/`, `graph/`, `domain_schemas/` 세 개로 단순해짐 —
  스크립트 자체는 대부분 `tar` 통짜 백업이라 영향 적음, 확인만 필요.
- `backend/tests/conftest.py`, 각 테스트의 `clean_dirs` 류 픽스처:
  `DATA_DIR, GRAPH_DIR, DOMAIN_SCHEMA_DIR` 나열이
  `DATA_DIR, DOMAIN_SCHEMA_DIR`(그래프 DB 디렉터리 포함) 정도로 줄어듦.
- 모든 관련 테스트(`test_parse.py`, `test_files.py`, `test_ontology.py`,
  `test_chunking.py`)의 경로 가정을 새 구조로 갱신.

## 6. 마이그레이션 및 롤아웃

이 변경은 되돌리기 어렵고(파일 이동은 실수 시 실제 추출 데이터 유실
위험), `backend/data`는 git으로 백업되지 않는 host 전용 데이터이므로
아래 순서를 반드시 지킨다.

1. `./scripts/backup_data.sh`로 스냅샷을 먼저 뜬다.
2. 코드 변경(4번 항목)을 별도 브랜치에서 진행하고, 테스트는 항상
   `ONTOLOGY_DATA_DIR`(임시 디렉터리)로 격리된 상태에서 통과시킨다.
3. 실제 `backend/data`에 대해 1회성 마이그레이션 스크립트를 작성한다
   (예: `scripts/migrate_data_layout.py`) — 각 `{stem}_raw.md`를
   `documents/{stem}/raw.md`로, `graph/{stem}/*`를
   `documents/{stem}/*`로, `chunks/{stem}.json`을
   `documents/{stem}/chunks.json`으로 이동. `graph/graph.ladybugdb`와
   `domain_schemas/`는 그대로 둔다.
4. 마이그레이션은 `--dry-run`으로 먼저 이동 계획만 출력해 검토한 뒤 실행.
5. 마이그레이션 후 `podman-compose down && podman-compose up --build -d`로
   재기동하고, 문서 목록/스키마/그래프/청크가 모두 이전과 동일하게
   보이는지 확인한다. Ladybug Explorer는 `graph.ladybugdb` 경로가
   바뀌지 않으므로 별도 조치가 필요 없다.
6. 문제가 없는 것을 확인한 뒤에만 예전 백업을 정리한다(즉시 삭제하지
   않는다).

## 7. 지금 당장 하지 않는 이유

파일 배치를 바꾸는 것은 실행 중인 로컬 환경의 실제 추출 데이터를
직접 건드리는 작업이라 실수 시 되돌리기 어렵다. 이 문서는 설계
검토용이며, 실행은 사용자가 마이그레이션 시점을 명시적으로 정한 뒤
별도로 진행한다.
