# Ontology Builder Architecture

> 현재 코드(`backend/app`, `frontend/src`), 테스트, 배포 설정을 기준으로 한
> 서비스·데이터·API 계약 문서다. 제품 기능의 우선순위와 범위는
> [`PRD.md`](PRD.md)를 따른다.

## 1. 시스템 컨텍스트

```text
Browser
  │ Vue 3 / Vite
  ├── local dev: /api, /health → Vite proxy → :8000
  └── Render: VITE_API_BASE_URL → backend public URL
                         │
                    FastAPI :8000
       ┌─────────────┼──────────────┬──────────────┐
       ▼             ▼              ▼              ▼
   anydoc       OpenRouter       LadybugDB       OpenTelemetry
 document       chat/embed      graph file       → Jaeger
 conversion
```

로컬 Compose는 `frontend`, `backend`, `jaeger`, 선택적인 read-only Ladybug
Explorer를 실행한다. 운영 Render는 정적 프론트엔드와 Docker 백엔드를 분리하고,
백엔드 `/app/data`에 1GB persistent disk를 마운트한다.

## 2. 컴포넌트와 책임

### Backend

- `main.py`: FastAPI 앱, CORS/인증 middleware, 요청 검증, 서비스 조합, 응답 shape
- `parser.py`: 업로드 데이터를 Markdown으로 변환하고 `data/{stem}_raw.md` 저장
- `ontology.py`: discovery, 스키마 생성/수렴, 그래프 추출, 검증/진화,
  스키마 버전·manifest·domain 파일 저장, 임베딩 orchestration
- `graphdb.py`: 단일 LadybugDB 연결, DDL, 트랜잭션, 그래프 조회·검색·홉 확장
- `graphrag.py`: 질문 분석, 타입별 키워드/임베딩 폴백, 컨텍스트 조립
- `chat.py`: OpenRouter ChatOpenAI 생성, 모델 catalog/config, LangChain 메시지 변환
- `embeddings.py`: OpenRouter 임베딩 생성과 노드 임베딩 텍스트 규칙
- `telemetry.py`: 채팅·임베딩 호출의 span, 입력/출력 metadata, 연결 오류 재시도
- `auth.py`: 선택적인 단일 공유 비밀번호 토큰 검증
- `paths.py`: `ONTOLOGY_DATA_DIR` override와 기본 `backend/data` 결정

### Frontend

`App.vue`가 선택 문서, 타입/엣지 필터, schema refresh signal, 홉 수, Markdown
표시 설정, 하이라이트 노드를 소유한다.

- `SettingsPanel.vue`: 문서 목록/업로드, 모델 표시, 스키마 library, 필터, 설정
- `ChatPanel.vue`: 브라우저 내 대화 이력, GraphRAG chips, ESC abort
- `OntologyGraph.vue`: 스키마 preview 또는 실제 graph의 v-network-graph 렌더링,
  d3-force layout과 하이라이트 focus
- `DocumentPreview.vue`: Markdown 원문을 marked로 HTML 렌더링
- `SchemaGraphPreview.vue`: 스키마/노드/엣지 JSON raw viewer
- `utils/api.js`: API base URL, 선택적 Bearer token의 저장·첨부

## 3. 주요 데이터 흐름

### 문서에서 그래프까지

```text
upload → parse to Markdown → document manifest
       → discovery (optional)
       → generate/copy domain schema → schema version + active pointer
       → extract graph → LadybugDB rows
       → embed (separate) → node FLOAT[1536] vectors
```

스키마 생성과 추출은 각각 LLM JSON 응답을 받아 markdown code fence를 제거한
뒤 JSON으로 파싱한다. `detail`은 label/type만으로 잃는 조건·예외·수치 등의
문서별 맥락을 보존한다.

### 질문에서 답변까지

1. 활성 스키마와 그래프가 있으면 `graphrag.search_graph`를 호출한다.
2. 질문 분석 LLM이 스키마에 존재하는 관련 타입과 타입별 구체 키워드를 반환한다.
3. 타입별 label substring → embedding top 5 → 해당 타입 전체 순으로 노드를 찾는다.
4. 연결 엣지를 찾고 최대 5홉까지 undirected Cypher traversal로 확장한다.
5. 실제 노드·엣지를 `Entities`/`Relations` 텍스트로 만들고 answer LLM의 system
   message에 삽입한다.
6. 응답은 답변 텍스트와 함께 타입/관련 노드/관련 엣지를 반환한다.

분석 결과가 비어 있거나 graph context가 없으면 GraphRAG 답변을 만들지 않고
미찾음 메시지를 반환한다. 검색 내부의 JSON 파싱 등 기술 오류는 plain chat으로
fallback한다.

## 4. 데이터 모델과 저장소

### 파일 레이아웃

```text
backend/data/
  {stem}_raw.md
  graph/
    graph.ladybugdb
    {stem}/
      manifest.json
      discovery.json
      versions.json
      schema_v1.json
      schema_v2.json
  domain_schemas/
    {domain}/
      schema.json
      manifest.json             # calibration_stems, history
      pending_review.json
```

`versions.json`은 `{active_version, versions[]}`이며 각 version item은
`version`, `document_type`, `created_at`을 가진다. discovery는 schema version과
독립적으로 문서당 하나를 덮어쓴다. `manifest.json`은 `original_filename`만
보존한다.

### 논리 모델

```text
Document(stem, original_filename, markdown)
  └─ SchemaVersion(version, document_type, created_at, active)
       └─ Node(id, label, type, detail?, embedding[1536]?)
       └─ Edge(source, target, type, detail?)
```

LadybugDB는 모든 문서/버전이 공유하는 하나의 DB 파일이다. 노드 타입마다
`NODE TABLE`을, 엣지 타입·source type·target type 조합마다 `REL TABLE`을 만든다.
행에는 `source_document`와 `source_version` 의미를 포함한 내부 식별자를 사용하며,
내부 노드 id는 `{stem}::{id}`로 전역 유일화한다. API 경계에서는 원래 id로 되돌린다.
`_ExtractedDocument` 내부 테이블은 `(stem, version)`별 그래프 존재 marker다.

노드 테이블은 `embedding FLOAT[1536]`을 가지며 오래된 노드나 임베딩 전 단계의
노드는 NULL일 수 있다. 임베딩 차원을 바꾸려면 전체 그래프를 재추출해야 한다.
그래프 DB public 함수는 하나의 cached connection을 공유하고 RLock으로 직렬화한다.
쓰기 후 connection을 닫아 WAL checkpoint를 유도한다.

## 5. API surface

모든 `/api` 엔드포인트는 인증이 켜진 경우 Bearer 토큰이 필요하다. `OPTIONS`는
항상 middleware를 통과한다. 파일명은 basename으로 정규화한다.

### 공통/인증/채팅

| Method | Path | 계약 |
|---|---|---|
| GET | `/health` | `{"status":"ok"}`; liveness, 항상 공개 |
| GET | `/api/hello` | 개발용 `{"message":"Hello from FastAPI"}` |
| GET | `/api/config` | `model`, `models[]`, `max_tokens`, `auth_required`; 공개 |
| POST | `/api/config/model` | body `{"model": id}`; catalog 밖이면 400 |
| POST | `/api/login` | body `{"password": string}` → `{"token": string}`; 실패 401 |
| POST | `/api/chat` | body `messages[]`, optional `filename`, `hops`; assistant 응답 |

`ChatMessage`는 `role`, `content`다. GraphRAG 응답에는 `node_types`, `edge_types`,
`related_nodes`, `related_edges`가 추가되고, plain chat에는 `role`, `content`만
있다. `hops`는 서버에서 1~5로 clamp한다.

### 문서

| Method | Path | 계약 |
|---|---|---|
| POST | `/api/parse` | multipart field `file`; `{filename, path}` |
| GET | `/api/files` | `{files:[{filename}]}`; 최신순 |
| GET | `/api/files/{filename}` | 저장 Markdown plain text; 없으면 404 |
| GET | `/api/documents` | 원본명, schema/graph 여부, `graphdb_name` 포함 |

### 문서별 ontology

| Method | Path | 계약 |
|---|---|---|
| GET | `/api/ontology/schemas` | 저장된 schema 문서 stem 목록 |
| POST | `/api/ontology/reset-database` | 전체 graph DB 삭제, schema/원문 보존 |
| POST/GET | `/api/ontology/{filename}/discover` | discovery 생성/조회; 미존재 조회 404 |
| POST | `/api/ontology/{filename}/schema` | optional `document_type`, `max_chars`, `use_discovery`; 새 version 반환 |
| POST | `/api/ontology/{filename}/schema/use` | body `source_stem`; source 활성 schema 복사 |
| GET | `/api/ontology/{filename}/schema` | 활성 schema; 없으면 404 |
| POST | `/api/ontology/{filename}/extract` | 활성/default schema로 graph 추출 |
| POST | `/api/ontology/{filename}/embed` | graph node embeddings 계산; count 반환 |
| POST | `/api/ontology/{filename}/validate` | optional `max_chars`; validation report |
| POST | `/api/ontology/{filename}/evolve` | `validation_report`, optional `max_chars`; proposal |
| POST | `/api/ontology/{filename}/evolve/apply` | `changes[]`; schema 변경 적용 |
| POST | `/api/ontology/{filename}/schema/stability` | `document_type`, `runs`, `max_chars`; stability report |
| GET | `/api/ontology/{filename}` | 활성 version graph `{nodes,edges}` |
| GET | `/api/ontology/{filename}/schema/versions` | version metadata, active/has_graph 포함 |
| POST | `/api/ontology/{filename}/schema/versions/{version}/activate` | 활성 version 변경 |
| DELETE | `/api/ontology/{filename}/schema/versions/{version}` | schema와 해당 graph data 삭제 |

### 도메인 스키마

| Method | Path | 계약 |
|---|---|---|
| POST | `/api/ontology/domain-schema/converge` | `filenames[]`, optional seed/schema 설정; 비영속 수렴 결과 |
| POST | `/api/ontology/domain-schema/redundant-types` | node/edge types와 threshold로 중복쌍 조회 |
| GET | `/api/ontology/domain-schemas` | 저장된 domain 목록 |
| GET | `/api/ontology/domain-schema/{domain}` | schema, calibration stems, history, pending review |
| POST | `/api/ontology/domain-schema/{domain}/converge` | 저장 schema를 seed로 수렴 실행/저장 |
| POST | `/api/ontology/domain-schema/{domain}/pending-review/apply` | pending `changes[]` 적용 |
| POST | `/api/ontology/{filename}/schema/use-domain` | body `domain`, `document_type`; domain schema를 새 version으로 복사 |

요청 모델의 빈 `filenames`는 400, 대상 문서/domain/schema/version 부재는 404,
LLM이 유효 JSON을 반환하지 못하거나 식별자/그래프 shape가 잘못되면 400이다.

## 6. 보안 모델

### 인증

`APP_PASSWORD`가 비어 있으면 인증 middleware는 비활성이다. 값이 설정되면
`/health`, `/api/login`, `/api/config`를 제외한 요청은
`Authorization: Bearer <sha256(APP_PASSWORD)>`를 요구한다. 로그인은
constant-time password 비교를 사용한다. 토큰은 stateless·고정·무만료이며
비밀번호 변경 시 기존 토큰이 즉시 무효화된다.

이는 사용자별 인증/권한 시스템이 아니라 OpenRouter 비용과 공개 접근을 막는
공유 비밀번호 방어선이다. 프론트엔드는 토큰을 localStorage에 보관하므로
XSS 방어가 별도 필요하며, 현재 CSP·rate limit·CSRF token·logout은 제공하지 않는다.

### 네트워크와 파일 경계

- CORS origin은 `CORS_ALLOWED_ORIGINS`의 comma-separated 목록이며 기본값은
  `http://localhost:5173`이다.
- 인증 middleware는 브라우저 CORS preflight인 `OPTIONS`를 통과시킨다.
- 파일 접근은 `os.path.basename`으로 data 디렉터리 밖 경로를 차단한다.
- LLM 프롬프트와 응답은 로컬 Jaeger 추적 span 속성에 기록될 수 있다. 외부
  collector를 운영할 때 문서 민감정보가 유출되지 않도록 exporter 정책을 검토해야 한다.
- `reset-database`는 모든 문서 그래프를 파괴하는 운영 기능이므로 공개 환경에서
  공유 토큰만으로 노출하는 것은 위험하다.

## 7. 외부 서비스 및 운영 사양

- **OpenRouter:** `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`; LangChain OpenAI
  호환 chat model 사용. 임베딩 모델은 `OPENROUTER_EMBEDDING_MODEL`, 기본
  `openai/text-embedding-3-small`이며 1536차원 계약을 요구한다.
- **anydoc:** PDF/Word/PPT/CSV 등 업로드 형식을 Markdown으로 변환한다.
- **LadybugDB 0.19.1:** embedded Cypher graph store. Explorer 이미지 버전도
  동일해야 하며 Explorer는 read-only로 실행한다.
- **OpenTelemetry/Jaeger:** chat, schema, extraction, discovery/analysis,
  embedding 호출을 span으로 기록한다. 연결 오류(`ModelConnectionError`)는
  기본 2회 재시도하며 no-op exporter에서도 호출이 실패하지 않는다.

### 배포

로컬 backend는 `backend/data`와 `backend/app`을 bind mount하고 uvicorn reload로
실행한다. 프론트엔드는 Vite proxy를 사용한다. Render backend는 Docker와 `$PORT`,
persistent disk를 사용하고 frontend는 `npm install && npm run build`의 정적 산출물을
호스팅한다. Render의 실제 public URL은 각각 `CORS_ALLOWED_ORIGINS`와
`VITE_API_BASE_URL`에 일치시켜야 한다.

## 8. 테스트와 검증 경계

backend pytest는 parse/auth/chat/files/graphdb/graphrag/ontology/telemetry/config를
검증한다. 외부 LLM·임베딩·anydoc는 mock하고, graphdb 테스트는 격리된 실제
LadybugDB 파일을 사용한다. `ONTOLOGY_DATA_DIR`로 테스트 data를 별도 디렉터리에
둔다. frontend에는 자동화 테스트가 없으므로 build와 수동 브라우저 검증이 기준이다.

## 9. 알려진 설계 한계

- 하나의 DB 파일과 flat-file metadata를 공유하며 multi-user isolation이 없다.
- 다른 스키마로 재추출할 때 더 이상 사용하지 않는 LadybugDB type table은 삭제하지 않는다.
- 구형 `nodes.json`/`edges.json`에서 현재 LadybugDB로 자동 migration하지 않는다.
- 추출 결과가 schema type과 일치하는지 의미/도메인 검증하지 않는다.
- 문서 전체를 프롬프트에 보내므로 문서 길이·토큰 예산 관리가 없다.
- chat은 streaming이 아니며 서버 측 취소도 없다.
