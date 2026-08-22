# Ontology Builder

문서를 업로드하면 LLM이 온톨로지 스키마(엔티티/관계 타입)를 제안하고, 그 스키마에 맞춰 문서에서 노드와 엣지를 추출해 지식 그래프로 만들어줍니다. 챗봇에 질문하면 이 그래프에서 관련 정보를 찾아(GraphRAG) 답변에 활용하기 때문에, 문서 내용에 기반한 더 정확한 답변을 받을 수 있습니다.

## 주요 기능

- **문서 업로드 → 마크다운 변환**: PDF, Word, PPT, CSV 등 다양한 형식을 지원 (`anydoc` 기반)
- **온톨로지 스키마 생성**: LLM이 문서에 맞는 노드/엣지 타입을 제안, 다른 문서의 스키마를 재사용도 가능
- **그래프 추출**: 스키마에 따라 문서에서 실제 엔티티(노드)와 관계(엣지)를 추출
- **그래프 시각화**: 추출된 그래프를 인터랙티브하게 확인 (줌/팬/드래그, 타입별 필터)
- **GraphRAG 챗봇**: 질문과 관련된 그래프 노드를 찾아 주변 정보를 함께 참고해서 답변

## 사전 준비물

- [Podman](https://podman.io/) + `podman machine` (컨테이너 실행)
- [podman-compose](https://github.com/containers/podman-compose)
- [OpenRouter](https://openrouter.ai/) API 키

## 설치 및 실행

```bash
# 1. podman machine이 없다면 생성 및 시작
podman machine init
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

## 사용 방법

1. 좌측 패널에서 문서를 업로드
2. 업로드된 문서를 목록에서 선택
3. "스키마 생성" → "그래프 추출" 순서로 클릭 (또는 라이브러리의 기존 스키마를 재사용)
4. 우측 패널에서 추출된 그래프 확인
5. 가운데 채팅창에서 문서 내용에 대해 질문 (선택된 문서의 그래프를 참고해서 답변)

## 종료

```bash
podman-compose down
```

## 더 알아보기

- 전체 아키텍처, API 엔드포인트, 컴포넌트 구조: [`docs/SPEC.md`](docs/SPEC.md)
- 개발 환경에서 자주 겪는 문제(podman/virtiofs 마운트 이슈 등): [`CLAUDE.md`](CLAUDE.md)
