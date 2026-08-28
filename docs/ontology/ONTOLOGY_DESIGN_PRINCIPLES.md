# 온톨로지 설계 원칙

이 문서는 팀장님이 공유한 온톨로지 설계 가이드를 정리한 것이다. 이 프로젝트의
`ontology.py`가 LLM으로 스키마(`node_types`/`edge_types`)를 제안하고 그 스키마로
그래프를 추출하는 방식 자체가 여기서 말하는 "실용적 온톨로지" 접근에 해당하므로,
스키마를 검토하거나 프롬프트를 조정할 때 참고 기준으로 삼는다.

온톨로지 스키마를 설계할 때는 처음부터 거대한 지식 그래프를 만들려고 하기보다,
"문서에 존재하는 개념과 그 관계를 어떻게 안정적으로 표현할 것인가"를 중심으로
잡는 것이 좋다.

특히 LLM/RAG 시스템에서 사용할 온톨로지라면 전통적인 학술적 온톨로지보다
검색·추론·업무 활용에 최적화된 실용적 온톨로지가 더 중요하다.

## 1. 가장 중요한 원칙: "명사"보다 "의미"를 모델링한다

예를 들어 보험 문서에서 다음과 같은 문장이 있다고 해보자.

> "암 진단 확정 시 가입금액의 50%를 암진단보험금으로 지급한다."

단순히 entity를 뽑으면:

- 암
- 가입금액
- 암진단보험금
- 50%

정도가 된다.

하지만 온톨로지는 다음과 같이 의미적 관계를 표현해야 한다.

```
[보험상품]
    └─ hasCoverage → [암진단보장]
                         ├─ covers → [암]
                         ├─ triggers → [암 진단 확정]
                         └─ pays → [암진단보험금]
                                      └─ amount → [가입금액 × 50%]
```

즉, 다음 순서로 발전시키는 것이 핵심이다.

```
Entity extraction → Relationship modeling → Business semantics
```

## 2. Ontology의 계층을 분리하는 것이 좋다

문서 기반 온톨로지는 최소한 다음 4개 계층으로 나눈다.

```
                    Ontology
                       │
        ┌──────────────┼──────────────┐
        ↓              ↓              ↓
     Concept        Relation       Attribute
        │              │              │
    보험상품        covers          가입금액
    계약자          hasCoverage     보험료
    피보험자        appliesTo       가입일
    보장            triggers        지급률
    질병            excludes        면책기간
```

그리고 별도로 Document/Provenance 계층을 둔다.

```
Document
   │
   ├── contains → Section
   │                │
   │                └── mentions → Concept
   │
   └── sourceOf → Relation
```

이 네 번째 부분이 실제 시스템에서는 상당히 중요하다.

## 3. "Concept"와 "Document Entity"를 구분한다

문서에서 가장 흔히 발생하는 문제가 이것이다.

예를 들어 여러 문서에서 "삼성생명"이라는 표현이 등장한다고 해보자. 문서상의
mention과 실제 개념은 다르다.

```
Document
   │
   └── mentions
          ↓
     "삼성생명"
          │
          ↓ resolves_to
     Company: Samsung Life
```

즉, `Mention ≠ Entity ≠ Concept`로 보는 것이 좋다.

### 추천 구조

```
Document
 └── Mention
       ├── surface_form
       ├── page
       ├── character_offset
       └── refers_to → Entity
                            │
                            └── instance_of → Concept
```

이렇게 해두면 나중에 LLM이 추출한 결과를 원문까지 추적(traceability)할 수 있다.

## 4. Relation은 "동사" 중심으로 설계한다

온톨로지를 설계할 때 가장 중요한 부분 중 하나다.

나쁜 예:

```
보험상품
 └── 관련정보
```

좋은 예:

```
보험상품
 ├── hasCoverage → 보장
 ├── hasPremium → 보험료
 ├── targets → 가입대상
 ├── excludes → 면책사항
 ├── requires → 가입조건
 └── pays → 보험금
```

즉 관계 이름을 명사보다 동사/동사구 형태로 만드는 것이 좋다.

특히 영어로 relation을 정의하면 상당히 깔끔해진다.

```
hasCoverage
covers
appliesTo
requires
excludes
triggers
causes
precedes
follows
belongsTo
definedBy
derivedFrom
```

## 5. Relation에도 Attribute가 필요하다

여기서 한 단계 더 나가야 한다.

예를 들어:

> 암 진단 후 90일 이내에는 보험금을 지급하지 않는다.

단순히

```
Policy ── excludes ── Cancer
```

라고 하면 정보가 손실된다.

실제로는:

```
Policy
   │
   └── hasWaitingPeriod
           │
           ├── condition → Cancer
           ├── duration → 90 days
           └── appliesFrom → ContractStart
```

또는 관계 자체에 property를 붙일 수도 있다.

```
Policy ──[excludes]── Cancer
             │
             ├── waitingPeriod = 90 days
             └── source = document_123
```

즉, **Relationship도 Entity처럼 정보가 붙을 수 있다**는 관점이 중요하다.

## 6. 문서 온톨로지에서는 "시간"을 반드시 고려한다

보험·금융 문서라면 특히 중요하다.

예를 들어 약관이 변경될 수 있다.

```
InsuranceProduct
       │
       ├── version 1
       │      └── validFrom → 2024-01-01
       │
       └── version 2
              └── validFrom → 2026-01-01
```

따라서 relationship에도 가능하면 다음을 고려하는 것이 좋다.

```
valid_from
valid_to
effective_date
created_at
updated_at
```

이렇게 하면 나중에 LLM에게 "2025년 기준으로 이 상품의 암진단보험금 지급 조건은?"
같은 질문을 할 수 있다.

## 7. Provenance를 1급 시민으로 만든다

문서 기반 온톨로지를 설계할 때 가장 강조하고 싶은 부분이다.

모든 중요한 지식은 다음 질문에 답할 수 있어야 한다.

> "이 사실은 어느 문서의 어디에서 나온 것인가?"

예:

```
CancerCoverage
     │
     ├── payoutRate → 50%
     │
     └── provenance
             ├── document_id
             ├── page = 17
             ├── section = "보험금 지급"
             └── text_span
```

이 구조가 있으면 RAG와 결합했을 때 상당히 강력해진다.

```
User Question
      ↓
Ontology Search
      ↓
Relevant Entity / Relation
      ↓
Provenance
      ↓
Original Document
      ↓
LLM
      ↓
Answer + Evidence
```

결국 Knowledge Graph + RAG 구조가 된다.

## 8. Ontology와 Taxonomy를 구분한다

이것도 설계할 때 많이 혼동한다.

### Taxonomy

```
보험
 ├── 생명보험
 │    ├── 종신보험
 │    └── 정기보험
 │
 └── 건강보험
      ├── 암보험
      └── 질병보험
```

주로 is-a 관계다.

### Ontology

```
암보험
 ├── isA → 건강보험
 ├── covers → 암
 ├── requires → 보험료
 ├── hasCoverage → 암진단보장
 ├── excludes → 특정암
 └── pays → 암진단보험금
```

즉,

- Taxonomy = 분류 체계
- Ontology = 개념 + 관계 + 의미 + 제약

이라고 생각하면 된다.

실제 프로젝트에서는 Taxonomy부터 만들고 Ontology로 확장하는 접근이 상당히
안정적이다.

## 9. 너무 상세하게 만들지 않는 것도 중요한 원칙이다

처음부터 다음과 같이 만들면 실패하기 쉽다.

```
Policy
 ├── hasPolicyNumber
 ├── hasPolicyHolder
 ├── hasPolicyHolderName
 ├── hasPolicyHolderAge
 ├── hasPolicyHolderGender
 ├── hasPolicyHolderAddress
 ├── ...
```

수백 개의 class와 property가 생긴다. 그러면 ontology 자체가 또 하나의 legacy
system이 된다.

초기에는 다음 정도로 시작하는 것을 권한다.

```
Document
Person
Organization
Product
Contract
Coverage
Benefit
Condition
Event
Disease
Amount
Date
Clause
```

그리고 업무 질문을 해결하는 데 필요한 관계만 추가한다.

## 10. "질문"을 기준으로 Ontology를 검증한다

이게 실무적으로 가장 좋은 방법이다.

Ontology를 만들고 "이 ontology가 완전한가?"라고 묻지 않는다. 대신 "우리가
답하고 싶은 질문에 답할 수 있는가?"를 물어야 한다.

예를 들어 보험 업무에서:

```
Q1. 이 상품은 어떤 질병을 보장하는가?
Q2. 특정 질병의 보험금은 얼마인가?
Q3. 보험금 지급 조건은 무엇인가?
Q4. 면책 조건은 무엇인가?
Q5. 특정 상품의 2025년 약관과 2026년 약관은 무엇이 다른가?
Q6. 이 조건은 어느 약관에 근거하는가?
```

이 질문들을 ontology query로 표현해 본다. 답할 수 없다면 ontology에 필요한
개념이나 관계가 빠진 것이다.

## 추천하는 전체 구조

문서 기반 AI 시스템이라면 다음 구조를 추천한다.

```
                    ┌──────────────┐
                    │   Document   │
                    └──────┬───────┘
                           │
                        contains
                           ↓
                    ┌──────────────┐
                    │   Section    │
                    └──────┬───────┘
                           │
                        mentions
                           ↓
                    ┌──────────────┐
                    │   Mention    │
                    └──────┬───────┘
                           │
                       refers_to
                           ↓
              ┌─────────────────────────┐
              │         Entity          │
              └────────────┬────────────┘
                           │
                       instance_of
                           ↓
              ┌─────────────────────────┐
              │        Concept          │
              └────────────┬────────────┘
                           │
                 ┌─────────┴─────────┐
                 ↓                   ↓
            Attributes           Relations
                 │                   │
                 ↓                   ↓
          amount, date       covers, requires,
          status, etc.       excludes, appliesTo
```

그리고 모든 Entity/Relation에 provenance를 연결한다.

```
Entity / Relation
       │
       └── supportedBy
                ↓
             Evidence
                │
                ├── document_id
                ├── page
                ├── section
                ├── text_span
                └── extraction_confidence
```

## 특히 LLM 시대에는 한 가지 원칙을 더 추가한다

**Ontology를 LLM이 추출하기 쉽게 설계한다.**

전통적인 ontology는 사람이 논리적으로 완벽하게 만드는 것을 중요하게 생각했다면,
LLM 기반 시스템에서는 "LLM이 안정적으로 추출할 수 있고, 검색할 수 있고, 검증할
수 있는 구조인가?"가 매우 중요하다.

그래서 다음과 같은 3-layer architecture를 추천한다.

```
                 ┌────────────────────┐
                 │   Business Ontology │
                 │                    │
                 │ Concept / Relation │
                 └─────────▲──────────┘
                           │
                     normalization
                           │
                 ┌─────────┴──────────┐
                 │   Extraction Layer │
                 │                    │
                 │ LLM / NER / RE     │
                 └─────────▲──────────┘
                           │
                        Evidence
                           │
                 ┌─────────┴──────────┐
                 │   Document Layer   │
                 │                    │
                 │ PDF / Word / HTML  │
                 └────────────────────┘
```

이렇게 하면 문서 → LLM 추출 → Ontology → Graph/RAG → 업무 질문이라는 전체
파이프라인을 만들 수 있다.

## 핵심 원칙만 압축하면

- Entity보다 의미와 관계를 중심으로 설계
- Concept / Entity / Mention을 구분
- Relation은 동사 중심
- Relation에도 속성을 붙일 수 있게 설계
- 시간과 버전을 처음부터 고려
- 모든 지식에 provenance를 연결
- Taxonomy와 Ontology를 구분
- 처음부터 너무 크게 만들지 않기
- 실제 업무 질문을 ontology의 테스트 케이스로 사용
- LLM이 추출·검색·검증하기 쉬운 구조로 설계

특히 문서 → Knowledge Layer → LLM/RAG/Agent 구조라면, 단순히 "문서에서
entity를 추출해서 graph로 만든다"보다 **Document Ontology와 Business
Ontology를 분리**하는 것이 상당히 중요하다. 이 구분을 잘 해두면 나중에
문서가 바뀌어도 업무 지식 모델은 안정적으로 유지할 수 있다.

## 이 프로젝트에 대한 참고

이 저장소의 현재 구현은 위 원칙 중 일부를 이미 반영하고 있고, 일부는 아직
반영하지 않은 단순화된 모델이다. 스키마/추출 프롬프트나 데이터 모델을 바꿀
때 참고할 수 있도록 대응 관계를 적어둔다.

- **원칙 1(의미 중심), 4(동사 중심 relation), 9(과도한 세분화 방지)** —
  `backend/app/ontology.py`의 `_SCHEMA_OUTPUT_INSTRUCTIONS`가 스키마 생성
  단계에서 이미 강제하고 있다: edge_type은 문서에 실제 근거가 있어야 하고,
  node_type은 최소 하나의 edge_type과 연결되는 것을 권장하며(단, 독립적으로도
  유용한 타입은 예외), 타입 개수는 5~12(node)/5~15(edge) 정도를 기본
  목표로 삼는다.
- **원칙 5(relation의 attribute)** — 현재 데이터 모델은 edge에 `type`과
  자유 텍스트 `detail` 필드만 있고, `waitingPeriod`/`amount`처럼 구조화된
  property는 없다. `detail`에 조건·수치·기간을 자연어로 담는 것으로 부분적으로만
  대체하고 있다 (`graphdb.py`의 REL 테이블 스키마 참고).
- **원칙 3(Mention vs Entity vs Concept)** — 아직 구분하지 않는다. 현재는
  추출된 각 node가 곧 최종 entity이며, 별도의 Mention 계층이나 동일 개념에
  대한 명시적 병합(coreference resolution) 레이어는 없다. 다만
  `EXTRACT_PROMPT`가 "같은 대상을 가리키는 여러 표기는 하나의 node로
  병합하라"는 지시를 통해 LLM 수준에서 최소한의 mention→entity 병합을
  시도한다.
- **원칙 6(시간/버전)** — 아직 반영되어 있지 않다. 문서가 개정되면 새 문서로
  취급되어 별도의 `source_document`로 저장될 뿐, 버전 간 `validFrom`/
  `validTo` 관계는 없다.
- **원칙 7(Provenance)** — 부분적으로 반영되어 있다. 모든 node/edge는
  `source_document`(문서 stem)를 갖고 있어 "어느 문서에서 나왔는가"에는
  답할 수 있지만, 페이지/섹션/문자 오프셋 수준의 `text_span`은 없다.
  `detail` 필드가 원문 근거를 자연어로 담는 방식으로 대체 역할을 한다.
- **원칙 8(Taxonomy vs Ontology)** — `isA`류의 명시적 계층 관계는 스키마에서
  강제하지 않는다. LLM이 필요하다고 판단하면 스스로 `IS_A`/`SUBTYPE_OF` 같은
  edge_type을 제안할 수는 있지만, 시스템이 taxonomy 계층을 별도로 관리하지는
  않는다.
- **원칙 10(질문 기반 검증)** — `graphrag.py`가 실제로 이 원칙을 따르는
  구조다: 질문이 들어오면 스키마의 어떤 node/edge type이 관련 있는지부터
  판단(`determine_relevant_types`)하므로, 스키마에 필요한 타입이 없으면
  검색 자체가 실패한다. 새 문서 유형을 추가할 때는 "이 문서에 대해 실제로
  받을 질문들"을 몇 개 적어보고, 그 질문이 스키마의 타입/관계만으로 답변
  가능한지 확인하는 것을 권한다.

이 문서에 없는 개선(예: Mention 계층 도입, edge property 구조화, 버전 관리)은
별도 설계 논의가 필요한 규모의 변경이므로, 이 문서는 "왜 그런 설계가
바람직한가"에 대한 참고 자료로 우선 두고 실제 반영 여부는 팀 논의를 거쳐
결정한다.
