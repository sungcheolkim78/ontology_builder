# 문서 Preview — 청크 보기 기능 스펙

## 배경

`app.chunking.chunk_markdown_file`가 만드는 `documents/{stem}/chunks.json`
(`GET /api/documents/{filename}/chunk`로 조회)을 `DocumentPreview.vue`에서
바로 확인할 수 있게 한다. 청크가 아직 생성되지 않은 문서에서는 이 기능
자체가 보이지 않는다.

## 결정 사항 (사용자 확인 완료)

- **배치**: `DocumentPreview.vue` 패널 헤더에 "원문 | 청크" 토글을 추가해
  같은 패널 안에서 뷰를 전환한다. 청크가 없는 문서는 토글 자체가 보이지
  않는다.
- **기본 상태**: 청크 목록은 전부 접힌 상태로 시작한다.
- **테스트**: Vitest + `@vue/test-utils`를 새로 도입해 TDD로 진행한다.

## 동작 스펙

1. `DocumentPreview`가 `file`을 받으면 기존 원문 텍스트 조회에 더해
   `GET /api/documents/{filename}/chunk`를 함께 시도한다.
   - 404 → `chunkData`는 `null`. 토글 버튼 자체를 렌더링하지 않는다
     (에러 취급하지 않음 — "청크 없음"은 정상 상태).
   - 200 → `chunkData`에 `{source, preamble, chunks}` 저장, 토글 버튼 노출.
   - `file`이 바뀌면 뷰 모드는 항상 "원문"으로 초기화한다 (청크가 있어도
     자동으로 청크 뷰를 열지 않음).
2. "청크" 토글을 누르면 패널 본문이 청크 뷰로 전환된다:
   - 최상단에 `source`와 preamble의 **줄 수**만 표시한다 (preamble의
     `text` 내용 자체는 표시하지 않음). 줄 수 = `line_end - line_start + 1`
     (기존 `line_start`/`line_end` 컨벤션과 동일하게 1-based 포함 구간으로
     계산).
   - 그 아래로 `chunks` 배열을 순서대로 접이식 행으로 나열한다.
     - 접힘(기본): 그 청크의 `path` 문자열만 보인다.
     - 펼침: `path` 아래에 해당 청크의 `text`를 마크다운으로 렌더링해
       보여준다 (원문 뷰와 동일한 `marked` + `.markdown` 스타일 재사용).
   - 각 행은 독립적으로 토글된다 (아코디언처럼 하나만 열리는 게 아니라
     여러 개를 동시에 펼칠 수 있음).
3. "원문" 토글을 누르면 기존 마크다운 미리보기 뷰로 돌아간다 (스크롤
   위치·라인 표시 등 기존 동작은 변경 없음).

## 컴포넌트/모듈 구성

- `frontend/src/utils/chunkFormat.js` (신규, 순수 함수) —
  `preambleLineCount(preamble)`, `chunkLineCount(chunk)`. 줄 수 계산
  공식을 한 곳에 두어 컴포넌트와 별도로 단위 테스트한다.
- `frontend/src/components/ChunkView.vue` (신규) — `data` prop
  (`{source, preamble, chunks}`)을 받아 위 2번 동작 전체를 렌더링하는
  프레젠테이션 컴포넌트. 자체적으로 `marked`를 사용해 펼쳐진 청크의
  `text`를 렌더링한다.
- `DocumentPreview.vue` — 청크 fetch, `viewMode`("raw"|"chunk") 상태,
  헤더 토글 버튼, `viewMode`에 따라 기존 원문 뷰 또는
  `<ChunkView :data="chunkData" />`를 렌더링.

## 테스트 계획 (Vitest)

1. **Vitest 설정**: `vitest`, `@vue/test-utils`, `jsdom`을 devDependency로
   추가하고 `vite.config.js`(또는 별도 `vitest.config.js`)에 `test` 설정,
   `package.json`에 `"test": "vitest run"` 스크립트 추가.
2. `chunkFormat.test.js` — `preambleLineCount`/`chunkLineCount`의 줄 수
   계산(일반 케이스, 0줄 케이스).
3. `ChunkView.test.js` (컴포넌트 마운트) —
   - source/preamble 줄 수만 보이고 preamble 텍스트는 안 보임.
   - 청크마다 `path`가 보이는 행이 하나씩 렌더링됨.
   - 초기 상태: 모든 청크의 `text` 마크다운 렌더링 결과가 DOM에 없음(접힘).
   - 한 행 클릭 → 그 청크의 `text`가 마크다운으로 렌더링되어 나타남,
     다른 행은 여전히 접혀 있음.
   - 같은 행 다시 클릭 → 다시 접힘.
4. `DocumentPreview.test.js` (컴포넌트 마운트, `apiFetch` 모킹) —
   - 청크 엔드포인트가 404 → 토글 버튼이 렌더링되지 않음.
   - 청크 엔드포인트가 200 → 토글 버튼이 보이고 기본값은 "원문" 뷰.
   - "청크" 클릭 → 청크 뷰 내용(예: 첫 청크의 `path`)이 보임.
   - "원문" 클릭 → 다시 원문 마크다운이 보임.
   - `file` prop이 바뀌면 뷰 모드가 "원문"으로 리셋됨.
