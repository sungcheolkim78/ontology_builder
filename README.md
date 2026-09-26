# Ontology Builder

문서를 업로드하면 LLM이 온톨로지 스키마(엔티티/관계 타입)를 제안하고, 그 스키마에 맞춰 문서에서 노드와 엣지를 추출해 지식 그래프로 만들어줍니다. 챗봇에 질문하면 이 그래프에서 관련 정보를 찾아(GraphRAG) 답변에 활용하기 때문에, 문서 내용에 기반한 더 정확한 답변을 받을 수 있습니다.

![실행 화면](docs/screenshots/app-overview.jpg)

🔗 **서비스 중**: [ontology-builder-frontend.onrender.com](https://ontology-builder-frontend.onrender.com/)

## 주요 기능

- **문서 업로드 → 마크다운 변환**: PDF, Word, PPT, CSV 등 다양한 형식을 지원 (`anydoc` 기반), 원본 파일명은 별도 매니페스트에 보존
- **온톨로지 발견 → 스키마 생성 → 그래프 추출**: 3단계 파이프라인으로 문서에서 지식 그래프를 만듭니다. 자세한 내용은 아래 [온톨로지 파이프라인](#온톨로지-파이프라인) 참고
- **그래프 시각화**: 추출된 그래프를 인터랙티브하게 확인 (줌/팬/드래그, 노드·엣지 타입별 필터). "온톨로지 그래프"와 "스키마/그래프DB" 두 컬럼을 동시에 확인 가능
- **GraphRAG 챗봇**: 질문과 관련된 그래프 노드를 찾아 주변 정보를 함께 참고해서 답변 (키워드 매칭 → 임베딩 유사도 → 해당 타입 전체, 3단계 폴백). 답변에 표시되는 관련 타입/노드를 클릭하면 그래프에서 해당 타입을 켜고 끄거나 관련 노드를 하이라이트·자동 포커스. 응답을 기다리는 동안 ESC로 취소 가능
- **백그라운드 작업 알림**: 온톨로지 발견/스키마 생성/그래프 추출은 백그라운드에서 진행되며, 다른 화면으로 이동해도 취소되지 않고 상단 바에서 진행 상태와 완료 알림을 확인할 수 있습니다
- **관측성(Observability)**: 모든 LLM 호출(채팅/스키마 생성/그래프 추출/키워드 추출)을 Langfuse로 추적해 프롬프트·응답·토큰 사용량·비용을 기록합니다. 그래프DB(LadybugDB)는 Ladybug Explorer에서 Cypher로 직접 조회할 수 있고, 장애 시 리셋할 수 있습니다
- **공유 비밀번호 로그인 (선택)**: 배포 환경에서 `APP_PASSWORD`를 설정하면 하나의 비밀번호로 접근을 제한하는 로그인 화면이 뜹니다. 계정 구분 없이 모든 사용자가 동일한 데이터를 봅니다. 로컬 개발에서는 설정하지 않으면 그대로 비활성 상태입니다.

## 온톨로지 파이프라인

문서 하나에 대해 아래 3단계를 순서대로 실행합니다 (프론트엔드의 "1. 온톨로지 발견" / "2. 스키마 생성" / "3. 그래프 추출" 버튼과 대응):

1. **온톨로지 발견 (discover)** — 선택 단계. 문서에서 후보 개념(classes)·관계(relationships)·속성(attributes)·이벤트(events)·규칙(rules)·용어(terminology)·역량 질문(competency questions)·주의사항(warnings)을 탐색적으로 도출합니다. 스키마를 처음부터 잘 잡기 어려운 문서에서 "이 문서에 어떤 개념들이 있는지" 먼저 파악하고 싶을 때 사용합니다. 결과는 스키마 생성 시 참고 자료로만 쓰이고 강제되지 않습니다.
2. **스키마 생성 (schema)** — 문서에 맞는 노드 타입(`node_types`)과 엣지 타입(`edge_types`)을 LLM이 제안합니다. 이미 만들어둔 다른 문서의 스키마를 재사용하거나, 발견 단계의 결과를 참고해서 생성할 수도 있습니다. 문서마다 여러 스키마 버전을 저장해두고 그중 하나를 "활성 버전"으로 선택해 사용합니다.
3. **그래프 추출 (extract)** — 활성 스키마에 맞춰 문서에서 실제 노드/엣지 인스턴스를 추출하고, 그래프DB(LadybugDB)에 저장합니다.

### 긴 문서는 어떻게 처리하나

문서에 `chunks.json`(조/항 단위로 분할된 청크)이 있으면 위 3단계 모두 문서 전체를 한 번에 LLM에 보내는 대신, 청크를 문자 수 기준으로 묶은 그룹(chunk group) 단위로 나눠 처리합니다 (map). 발견/스키마 생성은 그룹별 결과를 하나로 합치는 별도의 통합(consolidation) LLM 호출을 한 번 더 거치고(reduce), 그래프 추출은 그룹마다 노드 id가 독립적이라는 점을 이용해 통합을 코드로만 처리합니다 (같은 타입+라벨을 가진 노드를 같은 개체로 병합). 그룹은 여러 개를 동시에 병렬로 처리하며, 처리 중간에 실패해도 이미 끝난 그룹의 결과는 캐시되어 있어서 재시도 시 실패한 그룹만 다시 처리하면 됩니다 — 문서 하나 전체를 처음부터 다시 돌릴 필요가 없습니다.

### 그 외 온톨로지 관련 기능

- **스키마 검증/진화**: 추출된 그래프가 실제 문서 내용을 잘 표현하는지 검증하고, 스키마 개선안을 제안·적용할 수 있습니다.
- **도메인 스키마 수렴**: 같은 종류의 문서(예: 보험 약관)를 여러 개 반복 처리하면서 개별 스키마를 하나의 "도메인 스키마"로 수렴시키는 기능도 있습니다.
- **법률 문서 특화 검증**: 법률/약관류 문서에서 구조적으로 애매한 캐치올(catch-all) 노드를 탐지하거나 엣지 형태가 맞는지 검증하는 별도 가드가 있습니다.

온톨로지 설계 철학(단순 개체명 추출이 아니라 의미/관계 중심으로 모델링하는 이유 등)은 [`docs/ontology/ONTOLOGY_DESIGN_PRINCIPLES.md`](docs/ontology/ONTOLOGY_DESIGN_PRINCIPLES.md)에 정리되어 있습니다.

## 아키텍처

```
┌───────────────────────────┐         ┌──────────────────────────────┐
│  frontend (Vue 3 + Vite)  │  HTTP   │      backend (FastAPI)        │
│  :5173, /api/* 를 백엔드로 │ ──────► │      :8000, uvicorn --reload  │
│  프록시                    │         └──────────────┬─────────────────┘
└───────────────────────────┘                          │
                              ┌──────────────┬──────────┼──────────┬──────────────┐
                              ▼              ▼          ▼          ▼              ▼
                       OpenRouter API   anydoc (Rust) backend/data/         Langfuse
                     (langchain, 채팅 +  문서 → 마크다운  graph.ladybugdb   (자체 호스팅,
                   스키마/추출/임베딩)                  (노드/엣지)        이 compose 밖의
                                                              ▲          별도 서버 —
                                                              │        LLM 호출 프롬프트/
                                                   Ladybug Explorer      응답/토큰/비용 추적)
                                                     (:8001, 읽기 전용
                                                      Cypher 조회 UI)
```

세 서비스(frontend/backend/ladybug-explorer)는 `podman-compose.yml`로 각각 별도 컨테이너에서
실행되며, 개발 중 핫리로드를 위해 소스가 볼륨 마운트됩니다. 더 자세한 구조는
[`docs/SPEC.md`](docs/SPEC.md), 개념 설명은 [`docs/presentation.html`](docs/presentation.html)
를 참고하세요.

## 사전 준비물

- [Podman](https://podman.io/) + `podman machine` (컨테이너 실행). **기본 메모리(2GB)로는 부족할 수 있으니 최소 4GB, 가급적 8GB 이상 할당하세요** (`podman machine set --memory 8192`, 머신을 먼저 정지한 뒤 실행). 스키마의 노드/엣지 타입 수가 많은 문서일수록 그래프DB에 쓰는 데 필요한 메모리가 늘어나는데, 메모리가 부족하면 쓰기 도중 강제 종료되면서 그래프DB 전체가 응답 불가 상태에 빠질 수 있습니다. 별개로, **30MB 이상의 대용량 PDF**(수백~1000페이지대 문서)를 "MD 생성"으로 변환할 때도 메모리를 많이 씁니다 — 실측 기준 30MB/1,484페이지 문서 변환 중 podman VM이 몇 분간 응답 불가 상태에 빠졌다가(권장 메모리 미만이었을 때) 스스로 복구된 사례가 있습니다. 이런 대용량 문서를 자주 다룬다면 6GB보다 8GB 이상을 권장합니다. 자세한 내용은 [`CLAUDE.md`](CLAUDE.md) 참고
- [podman-compose](https://github.com/containers/podman-compose)
- [OpenRouter](https://openrouter.ai/) API 키

## 설치 및 실행

```bash
# 1. podman machine이 없다면 생성 및 시작 (메모리는 최소 4GB, 30MB 이상의
# 대용량 PDF를 다룰 계획이라면 8GB 권장)
podman machine init --memory 8192
podman machine start

# 2. 백엔드 환경변수 설정
cp backend/.env.example backend/.env
# backend/.env를 열어 OPENROUTER_API_KEY에 실제 키를 입력

# 3. 데이터 디렉토리 준비 (최초 1회)
mkdir -p backend/data && touch backend/data/.gitkeep

# 4. 전체 스택 빌드 및 실행
podman-compose up --build -d
```

실행되면 브라우저에서 `http://localhost:5173`으로 접속합니다.

- 그래프DB(`backend/data/graph/graph.ladybugdb`)를 Cypher로 직접 조회하려면 `http://localhost:8001` (Ladybug Explorer, 읽기 전용)
- LLM 호출 추적(Langfuse)은 별도로 자체 호스팅된 서버가 필요합니다 — `backend/.env`에 `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/`LANGFUSE_HOST`를 설정하지 않으면 추적 없이 그대로 동작합니다. 자세한 내용은 [`docs/features/langfuse/LANGFUSE-spec.md`](docs/features/langfuse/LANGFUSE-spec.md) 참고

바로 사용해볼 문서가 없다면 `samples/`에 준비된 삼성생명 약관 5종을
`backend/data/`에 복사한 뒤 스택을 재시작하면 됩니다 (`samples/README.md` 참고):

```bash
cp samples/*.md backend/data/
podman-compose down && podman-compose up --build -d
```

## 사용 방법

1. 좌측 패널에서 문서를 업로드
2. 업로드된 문서를 목록에서 선택
3. (선택) "1. 온톨로지 발견"으로 문서의 후보 개념·관계를 먼저 탐색
4. "2. 스키마 생성" → "3. 그래프 추출" 순서로 클릭 (또는 라이브러리의 기존 스키마를 재사용). 각 작업은 백그라운드에서 진행되므로 다른 화면으로 이동해도 계속 진행되고, 완료되면 상단 바에서 알림을 확인할 수 있습니다
5. "온톨로지 그래프" / "스키마·그래프DB" 두 컬럼에서 추출된 그래프와 스키마를 확인
6. 가운데 채팅창에서 문서 내용에 대해 질문 (선택된 문서의 그래프를 참고해서 답변)
7. 답변에 표시된 타입/노드 칩을 클릭해 그래프에서 확인 (타입 칩은 필터 토글, 노드 칩은 하이라이트+자동 포커스)

## 테스트

백엔드 테스트는 컨테이너 없이 별도 가상환경에서 실행합니다 (모든 LLM 호출은 목(mock) 처리되므로 실제 API 키가 필요 없습니다):

```bash
cd backend
python3.14 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
OPENROUTER_API_KEY=dummy python -m pytest tests/ -v
```

프론트엔드 컴포넌트/유닛 테스트 (Vitest, 백엔드나 컨테이너 없이 실행):

```bash
cd frontend
npm test
```

## 종료

```bash
podman-compose down
```

## 배포 (Render)

`render.yaml`로 백엔드(FastAPI, Docker)와 프론트엔드(정적 사이트)를
별도 서비스로 배포합니다. Render 대시보드에서 `OPENROUTER_API_KEY`를
설정해야 하고, 접근을 하나의 공유 비밀번호로 제한하려면
`APP_PASSWORD`도 함께 설정하세요 (둘 다 `render.yaml`에는 값 없이
`sync: false`로만 선언되어 있어, 실제 값은 대시보드에서 직접 입력해야
합니다). 자세한 내용은 [`docs/SPEC.md`](docs/SPEC.md)의 "Deployment
(production, Render)" 절을 참고하세요.

## 더 알아보기

- 온톨로지·그래프DB·GraphRAG·프론트/백엔드 구조를 쉽게 설명한 프레젠테이션: [`docs/presentation.html`](docs/presentation.html) (브라우저로 열기)
- 온톨로지 설계 원칙: [`docs/ontology/ONTOLOGY_DESIGN_PRINCIPLES.md`](docs/ontology/ONTOLOGY_DESIGN_PRINCIPLES.md)
- 전체 아키텍처, API 엔드포인트, 컴포넌트 구조: [`docs/SPEC.md`](docs/SPEC.md)
- LLM 호출 추적(Langfuse) 설정: [`docs/features/langfuse/LANGFUSE-spec.md`](docs/features/langfuse/LANGFUSE-spec.md)
- 개발 환경에서 자주 겪는 문제(podman/virtiofs 마운트 이슈, 그래프DB 메모리 이슈 등), 모듈 구조, 커맨드 모음: [`CLAUDE.md`](CLAUDE.md)
