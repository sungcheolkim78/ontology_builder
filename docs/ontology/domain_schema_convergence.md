# 도메인 스키마 수렴 및 평가 (Domain Schema Convergence & Evaluation)

## 1. 배경

현재 스키마 생성(`ontology.py`의 `generate_schema`)은 문서 1개 단위로만 동작한다.
같은 도메인(예: 보험 약관)에 속한 문서가 여러 개 있어도 각 문서마다 독립적으로
스키마를 생성하며, 아래 세 가지 선택지 중 어느 것도 지금은 만족스럽지 않다.

- **문서마다 다른 스키마**: 같은 도메인 문서인데 타입 이름/구조가 조금씩 달라지면
  `graphrag.py`의 타입 기반 검색이 문서마다 다르게 동작하고, 여러 문서를 묶어
  질의하기 어렵다.
- **전역 스키마 하나로 모든 문서를 커버**: 보험 약관과 전혀 무관한 문서까지
  같은 스키마를 강제하면 스키마가 지나치게 일반화되어(`Entity`/`RELATED_TO`
  수준의 `DEFAULT_SCHEMA`에 가까워짐) 정보 손실이 커진다.
- **수동으로 각 문서의 스키마를 복사**: 지금 `ontology.py`의 extract 단계는
  "문서 자신의 스키마, 복사된 스키마, 또는 `DEFAULT_SCHEMA`" 중 하나를 쓸 수
  있게 되어 있어 스키마를 다른 문서에 재사용하는 것 자체는 가능하지만, *어떤*
  스키마가 그 도메인에 잘 맞는지 판단하는 기준이 없다.

원하는 것은 "보험 약관"처럼 좁게 정의된 도메인에 속한 문서 집합에 대해 잘
맞는 스키마 하나를 찾고, 그 적합도를 감으로가 아니라 수치로 평가하는 것이다.

## 2. 접근 방식: 반복적 스키마 수렴 (iterative convergence)

도메인의 모든 문서를 한 번에 LLM에 넣어 스키마를 뽑는 방식은 문서 길이 제한
(`_check_document_length`/`MAX_DOCUMENT_CHARS`) 때문에 현실적이지 않다. 대신
문서를 하나씩 순차적으로 반영하며 스키마를 진화시키는 방식을 택한다. 이미
존재하는 온톨로지 진화 인프라(`EVOLUTION_PROMPT`, `propose_evolution`/
`apply_evolution` 계열 함수, "검증 결과 → 변경 제안 → 사람 리뷰 → 적용" 흐름)를
**같은 문서의 버전 이력이 아니라 같은 도메인의 다른 문서에 적용**하는 방식으로
재사용한다.

```
캘리브레이션 문서 집합 (도메인 = 보험 약관, N개 대표 문서)
        │
        ▼
  문서 1개로 초기 스키마 생성 (generate_schema)  ──▶ 도메인 스키마 v0
        │
        ▼
  ┌─────────────────────────────────────────────┐
  │  다음 문서에 현재 도메인 스키마 적용해 추출   │
  │  → validate_ontology로 검증                  │
  │  → MISSING_*/부정합 이슈 수집                │
  │  → propose_evolution으로 변경 제안 생성       │
  │  → (사람 리뷰) → apply_evolution으로 반영     │
  │  → 도메인 스키마 v(n+1)                       │
  └─────────────────────────────────────────────┘
        │ (캘리브레이션 문서 집합을 1~수 회 순회)
        ▼
  변경 제안이 더 이상 나오지 않거나 미미해지면 수렴 → 최종 도메인 스키마
```

이 방식의 장점은 기존에 이미 사람이 검토하는 안전장치(evolution의
`NEEDS_HUMAN_REVIEW`, "규제/계약 해석이 걸린 변경은 자동 승인 금지" 원칙)를
그대로 물려받는다는 점이다. 새 기능이 아니라 기존 파이프라인의 새로운
적용 방식에 가깝다.

## 3. 정량 평가 지표

스키마 하나를 "점수"로 압축하기보다, 아래 지표들을 대시보드처럼 같이 본다.
지표 간에 트레이드오프가 있어서(예: coverage를 올리려고 타입을 늘리면
redundancy가 올라감) 단일 스칼라로 합치면 정보 손실이 크다.

| 지표 | 정의 | 신호 |
|---|---|---|
| Coverage | 캘리브레이션 문서별 `validate_ontology`의 `MISSING_*` 이슈 수 / completeness 플래그 | 낮을수록 좋음 |
| 타입 활용도 | 스키마의 각 타입이 실제 인스턴스를 가진 캘리브레이션 문서의 비율 | 특정 문서에서만 쓰이는 타입은 과적합 후보 |
| 타입 중복도 | 타입 설명 임베딩 간 코사인 유사도(`node_embedding_text` 방식 재사용) | 0.9 이상이면 사실상 동일 개념 후보 |
| 문서 간 일관성 | 문서 길이로 정규화한 타입별 노드 수의 분산 | 분산이 크면 스키마 미스매치이거나 서브도메인 분리 필요 신호 |
| 다운스트림 QA 성공률 | `validate_ontology`가 생성하는 competency question을 `graphrag.search_graph()`로 실제 답변 가능한지 | 검색 유용성의 외재적 지표 |
| 안정성 | 동일 문서로 `generate_schema()`를 N회 반복, 타입 이름 집합의 Jaccard 유사도 | 낮으면 도메인 설명 자체가 불충분 |

이 지표들은 evolution 루프의 각 반복(iteration)마다 계산해 "수렴 곡선"으로
남기면, 몇 번째 문서를 반영한 뒤 스키마가 안정되기 시작했는지 확인할 수 있다.

**구현 상태 (완료):** `ontology.py`의 `evaluate_domain_schema(schema, iterations)`가
`converge_domain_schema()`가 이미 만든 iteration 로그만으로 coverage/타입
활용도/문서 간 일관성/QA 성공률(다운스트림 QA는 별도 GraphRAG 호출 대신
`validate_ontology`가 이미 생성하는 competency_questions의 `answerable`
플래그를 재사용 — 추가 LLM 호출 없음)을 계산한다. 타입 중복도는 임베딩
호출이 별도로 필요해 `find_redundant_type_pairs(schema, threshold=0.9)`로
분리했고, 안정성은 같은 문서로 스키마를 반복 생성해야 해서
`measure_schema_stability(document_text, runs=3)`로 분리했다 — 둘 다 호출
비용이 있으므로 `converge` 호출 시 자동으로 실행되지 않고 명시적으로
요청해야 한다. `POST /api/ontology/domain-schema/converge`의 응답에는
`evaluation`(비용 없는 지표)이 자동 포함되고, `POST
/api/ontology/domain-schema/redundant-types`와 `POST
/api/ontology/{filename}/schema/stability`가 각각 별도 엔드포인트다.

## 4. 도메인 스키마 저장/재사용 구조

현재는 스키마가 문서별로만 존재한다(`graph_dir_for(stem)/schema_v{N}.json`,
`manifest.active_version`). 도메인 스키마는 특정 문서에 종속되지 않으므로
별도 저장 공간이 필요하다.

```
backend/data/domain_schemas/{domain_name}/
    schema.json          # 현재 수렴된 도메인 스키마 (node_types/edge_types)
    manifest.json         # 반복 이력: 각 iteration에서 반영한 문서, 제안된
                           # 변경, 사람 리뷰 결과, 그 시점의 평가 지표 스냅샷
    calibration_set.json  # 이 도메인 스키마 학습에 쓰인 문서 stem 목록
```

새 문서가 이 도메인에 속한다고 판단되면(자동 판별은 범위 밖으로 두고, 우선은
사용자가 업로드 시 도메인을 선택하는 방식으로 시작) 문서 자신의 스키마를
새로 생성하는 대신 `domain_schemas/{domain}/schema.json`을 "복사된 스키마"로
바로 사용한다 — 이는 이미 존재하는 코드 경로(문서가 스키마를 자체 생성하지
않고 다른 스키마를 그대로 쓰는 것)를 그대로 재사용할 수 있다.

**구현 상태 (완료):** `ontology.py`에 `save_domain_schema`/`load_domain_schema`/
`list_domains`가 위 파일 레이아웃(`schema.json`, `manifest.json` — 캘리브레이션
문서 stem 목록과 실행 이력, `pending_review.json` — 아직 사람이 검토하지
않은 NEEDS_HUMAN_REVIEW 변경들)을 그대로 구현한다.

- `run_domain_convergence(domain, documents, max_chars=None)` — (a)의
  `converge_domain_schema()`를 감싸서, 도메인에 이미 저장된 스키마가 있으면
  그걸 시드로 나머지 문서를 계속 반영하고(같은 도메인에 새 문서가 생길
  때마다 다시 호출하면 스키마가 계속 다듬어짐), 없으면 첫 문서로 새로
  시드를 만든다. 결과 스키마를 저장하고, 실행 이력을 manifest에 남기고,
  NEEDS_HUMAN_REVIEW 항목을 `pending_review.json`에 누적한다(여러 번의
  실행에 걸쳐 계속 쌓이며, 사람이 검토하기 전까지는 사라지지 않는다).
- `apply_domain_schema_changes(domain, changes)` — evolution의
  `apply_evolution`과 동일한 계약(호출자가 이미 사람이 승인한 변경만
  걸러서 넘겨야 함)으로 도메인 스키마에 타입 변경을 반영하고, 적용된
  change_id를 pending queue에서 제거한다.
- `use_domain_schema(stem, domain, document_type="general")` — 도메인
  스키마를 특정 문서의 새 스키마 버전으로 복사한다. 기존 `/schema/use`
  엔드포인트(다른 *문서*의 스키마를 복사)와 대칭을 이루는, 도메인
  라이브러리에서 복사하는 버전이다.

엔드포인트: `GET /api/ontology/domain-schemas`(도메인 목록),
`GET /api/ontology/domain-schema/{domain}`(스키마+캘리브레이션 이력+대기
리뷰), `POST /api/ontology/domain-schema/{domain}/converge`(영속화되는
수렴 실행 — (a)에서 만든, 저장하지 않는 `POST
/api/ontology/domain-schema/converge`와는 별개로 유지), `POST
/api/ontology/domain-schema/{domain}/pending-review/apply`(사람이 승인한
변경 반영), `POST /api/ontology/{filename}/schema/use-domain`(문서에
도메인 스키마 적용).

## 5. 실행 계획

1. **(a) 반복적 진화 루프 자동화** — 캘리브레이션 문서 집합을 순회하며
   추출 → 검증 → 진화 제안 → (리뷰) → 적용을 자동 실행하는 스크립트/함수.
   사람 리뷰 지점은 자동 승인하지 않고 대기하도록 남긴다. **완료** —
   `converge_domain_schema()` (백엔드 함수 + `POST
   /api/ontology/domain-schema/converge`).
2. **(b) 정량 평가 지표 계산** — 위 6개 지표를 계산하는 로직. (a)의 각
   iteration 결과에서 이미 나오는 `validate_ontology` 리포트를 재사용하므로
   (a)가 먼저 있어야 자연스럽게 붙는다. **완료** — `evaluate_domain_schema()`
   (무비용, converge 응답에 자동 포함), `find_redundant_type_pairs()`/
   `measure_schema_stability()`(별도 비용, 별도 엔드포인트).
3. **(c) 도메인 스키마 저장/재사용 구조** — `domain_schemas/` 디렉터리,
   문서 업로드 시 도메인 선택 UI, "이 도메인 스키마 재사용" 옵션. **완료
   (백엔드까지)** — `run_domain_convergence()`/`apply_domain_schema_changes()`/
   `use_domain_schema()`와 대응 엔드포인트. 프런트엔드 UI(도메인 선택,
   pending review 화면)는 아직 없음 — 아래 "다음 단계" 참고.

세 가지 모두 결국 필요했고, (a) 없이는 (b)를 측정할 대상도, (c)에 저장할
결과물도 없어 이 순서로 진행했다.

## 7. 다음 단계 (프런트엔드 미구현)

백엔드 API는 모두 준비되었지만, 지금 이걸 쓰려면 API를 직접 호출해야 한다.
실제로 쓸모 있으려면 최소한 다음이 필요하다.

- 문서 업로드/스키마 생성 화면에 "도메인 선택 또는 새 도메인 시작" UI —
  선택 시 `/schema/use-domain`으로 즉시 스키마를 붙이거나,
  `/domain-schema/{domain}/converge`로 캘리브레이션에 포함
- 도메인 스키마 대시보드 — `evaluate_domain_schema`가 내는 coverage/타입
  활용도/일관성/QA 성공률을 표로/차트로 보여주는 화면(2절의 "수렴 곡선"을
  실제로 보여주는 부분)
- Pending review 화면 — `GET /api/ontology/domain-schema/{domain}`의
  `pending_review`를 사람이 보고 승인/거부해 `pending-review/apply`로
  넘기는 UI (지금은 API로 changes를 직접 만들어 보내야 함)

## 6. 리스크 및 미해결 사항

- **엔티티 해상 없음**: 여러 문서를 하나의 도메인 스키마로 묶어도, 같은
  실체(예: 같은 보험사)를 가리키는 서로 다른 문서의 노드를 자동으로 병합하지는
  않는다. 이는 별도 논의 대상(선행 대화에서 다룬 멀티 문서 통합 이슈)이며
  이 문서의 범위 밖이다.
- **도메인 판별 자동화 없음**: 어떤 문서가 "보험 약관 도메인"에 속하는지
  자동 분류하는 로직은 이번 범위에 없다. 우선 사람이 지정한다.
- **평가 지표의 임계값**: "타입 활용도 20% 미만이면 과적합"처럼 위에 제시한
  임계값은 초기 추정치이며, 실제 캘리브레이션 결과를 보면서 조정이 필요하다.
- **LLM 호출 비용**: 캘리브레이션 문서가 많아지고 반복 횟수가 늘어나면 문서당
  최대 3~4회의 LLM 호출(추출, 검증, 진화 제안, 필요 시 재추출)이 곱해진다.
  캘리브레이션 셋은 전체 도메인 문서가 아니라 대표 문서 5~10개로 제한할 것을
  권장한다(위 2절 참고).

## 8. 스키마 버전 vs 문서 유효기간 (2026-09-04, flexible ontology graph schema)

이 절은 서로 다른 두 가지 "버전"을 혼동하지 않기 위한 것이다 — 이 문서(도메인
스키마 수렴)의 버전 개념과, `docs/superpowers/specs/2026-09-04-flexible-ontology-graph-schema-design.md`
가 도입하는 `valid_from`/`valid_to`(법적 유효기간)는 서로 다른 축이다.

- **문서 스키마 버전** (`schema_v{N}.json`, `manifest.active_version`,
  `graphdb`의 `version` 컬럼): "이 그래프가 어떤 스키마로 추출됐는가"를
  답한다. `create_schema_version`이 매길 때마다 증가하고, 이전 버전은
  `activate_version`으로 언제든 다시 활성화할 수 있는 채로 남는다(삭제하지
  않는 한). `POST /api/ontology/{filename}/extract`는 항상 그 시점의
  `get_active_version(stem)`으로 그래프를 저장하므로, 그래프의 `version`은
  곧 그 그래프를 만든 스키마 버전이다 — 이 관계는 이미 성립해 있었고, 이번
  확장으로 새로 만든 것은 아니다.
- **도메인 스키마 계약 버전** (`app.schema_validation.SCHEMA_CONTRACT_VERSION`,
  `run_domain_convergence`가 매 수렴 실행마다 `manifest.json`의 history
  항목에 남기는 `schema_contract_version`/`schema_validation_summary`):
  "이 수렴 결과가 어떤 스키마 *계약 형태*(이름/설명만 있는 레거시 형태 vs
  typed properties/validation을 선언할 수 있는 이번 설계의 형태)로
  검증됐는가"를 답한다. 도메인 스키마 자체에는 문서 스키마 같은 버전
  정수가 없다(`schema.json` 하나만 계속 갱신됨) — 대신 이 계약 버전이
  "언제부터 이 도메인 스키마가 typed properties를 지원하는 형태로
  검증되기 시작했는지"를 이력에서 구분해 준다.
- **`valid_from`/`valid_to`** (그래프 노드/엣지 레벨, `graphdb.py`의 envelope
  컬럼): 위 두 버전과 전혀 다른 질문에 답한다 — "이 조항이 실제로 언제부터
  언제까지 적용되는가"라는, 문서/스키마의 표현 형태와 무관한 법적 유효성
  질문이다. 스키마나 추출을 다시 하지 않아도(즉 문서 스키마 버전이나
  계약 버전이 그대로여도) 한 조항의 유효기간은 개정으로 바뀔 수 있고,
  반대로 스키마 버전이 올라가도(재추출해도) 유효기간 자체는 그대로일 수
  있다. 세 값 중 어느 것도 다른 것으로부터 유도할 수 없다.
