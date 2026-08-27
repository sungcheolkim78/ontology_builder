# 법률/보험 문서 특화 스키마 프롬프트 비교

`generate_schema()` (`backend/app/ontology.py:161`)는 `document_type` 인자에 따라
두 프롬프트 중 하나를 사용해 문서의 온톨로지 스키마(node_types/edge_types)를
제안한다 (`SCHEMA_PROMPTS`, `backend/app/ontology.py:123`):

- `"general"` — 기존 범용 프롬프트, `SCHEMA_PROMPT` (`backend/app/ontology.py:48`)
- `"legal"` — 법률/보험 문서 특화 프롬프트, `LEGAL_SCHEMA_PROMPT`
  (`backend/app/ontology.py:73`). 법률/계약/보험 약관처럼 조/항/호로 이루어진
  내부 구조와, 문서 앞부분에서 명시적으로 정의된 용어가 본문 전체에서
  재사용되는 문서를 대상으로, 정의 조항·당사자/역할·의무/권리/급부·조건/면책·
  조문 간 상호참조·구조 자체(조항)를 놓치지 않도록 유도하는 지시문이 추가되어
  있다.

이 문서는 실제 보험 약관을 두 프롬프트에 각각 넣어 생성된 스키마를 비교한
기록이다.

## 테스트 대상 문서

`backend/data/1775193768309_raw.md` — 삼성생명 "삼성 탄탄한 변액연금보험(2601)
(무배당)[최저연금보증형]" 보험약관 (원본 592,212자). 제1조부터 시작하는
140,000자 구간(정의 조항 및 여러 급부/의무 조항을 포함하는 구간)을 대상으로
`document_type="legal"` 스키마를 새로 생성하고, 같은 문서에 대해 이전에
`document_type="general"`(구 기본 동작)로 생성돼 `backend/data/graph/
1775193768309_raw/schema.json`에 저장되어 있던 스키마와 비교했다.

> **주의:** 두 스키마는 정확히 동일한 조건(같은 텍스트 구간, 같은 시점의 모델
> 응답)에서 생성된 것이 아니다 — general 스키마는 이 비교를 위해 다시 생성한
> 것이 아니라 이전에 저장되어 있던 결과를 그대로 사용했다. 따라서 이 비교는
> "두 프롬프트가 동일 입력에 대해 내는 정확한 diff"가 아니라, 실제 약관
> 문서에 대해 legal 프롬프트가 의도한 항목들을 실제로 포착하는지를 보여주는
> 정성적 검증이다.

## 결과 요약

| | general (기존, 저장된 스키마) | legal (신규) |
|---|---|---|
| node_types 수 | 8 | 9 |
| edge_types 수 | 8 | 11 |

### general 프롬프트 결과

**node_types:** `InsuranceProduct`, `PolicyClause`, `BenefitType`,
`ContractPartyRole`, `InvestmentFund`, `MedicalCondition`, `BodyPart`,
`LegalAct`

**edge_types:** `HAS_CLAUSE`, `PROVIDES_BENEFIT`, `INCLUDES_RIDER`,
`OFFERS_FUND`, `COVERS_CONDITION`, `INVOLVES_ROLE`, `GOVERNED_BY`,
`AFFECTS_BODY_PART`

### legal 프롬프트 결과

**node_types:** `Article`, `AttachedTable`, `DefinedTerm`, `PartyRole`,
`InsuranceBenefit`, `ClaimCondition`, `ExclusionCondition`, `PolicyOption`,
`AccountOrFund`

**edge_types:** `DEFINES`, `REFERENCES_PROVISION`, `REFERENCES_TABLE`,
`GOVERNS_BENEFIT`, `GOVERNS_OPTION`, `TRIGGERED_BY`, `BARRED_BY`,
`PAYABLE_TO`, `HAS_DUTY_UNDER`, `EXERCISABLE_BY`, `APPLIES_TO_ACCOUNT`

## 관찰된 차이

- **정의 조항 분리 (`DefinedTerm` + `DEFINES`)** — legal 프롬프트는 "계약자적립액",
  "연금기준금액" 같이 정의 조항에서 명시적으로 정의되는 용어를 별도
  `DefinedTerm` 타입으로 분리하고, 어느 조항이 그 용어를 정의하는지
  (`Article --DEFINES--> DefinedTerm`)까지 모델링한다. general 프롬프트는
  이런 용어를 별도로 구분하지 않고 `PolicyClause`에 뭉뚱그렸다.

- **조문 간 상호참조 (`REFERENCES_PROVISION`)** — legal 프롬프트는 조항이
  서로를 인용하는 관계(예: "제15조에 따라", "전항에도 불구하고")를 위한
  엣지 타입을 제안했다. general 프롬프트의 결과에는 조문-조문 관계 타입
  자체가 없었다.

- **면책사유와 지급조건의 분리 (`ClaimCondition` vs `ExclusionCondition`)** —
  legal 프롬프트는 급부를 발생시키는 조건(`ClaimCondition`)과 급부를
  막거나 무효화하는 면책사유(`ExclusionCondition`)를 별도 타입으로
  나누고, `TRIGGERED_BY`/`BARRED_BY`로 방향을 구분했다. general
  프롬프트는 `COVERS_CONDITION` 하나로 지급조건만 표현했고 면책사유에
  해당하는 타입이 없었다.

- **절차적 권리 (`PolicyOption` + `EXERCISABLE_BY`)** — 청약철회, 계약자
  임의해지, 중도인출, 부활 같이 당사자가 능동적으로 행사하는 절차/옵션을
  legal 프롬프트는 별도 타입으로 포착했다. general 프롬프트 결과에는
  대응하는 타입이 없었다.

- **별표 참조 (`AttachedTable` + `REFERENCES_TABLE`)** — 보험금 지급기준표,
  재해분류표 등 약관 본문이 참조하는 첨부 별표를 legal 프롬프트는 별도
  타입으로 분리했다. general 프롬프트는 이를 반영하지 않았다.

- **당사자 의무 (`HAS_DUTY_UNDER`)** — legal 프롬프트는 당사자가 조항에
  따라 지는 의무/통지 책임 방향의 엣지를 별도로 제안했다. general
  프롬프트의 `INVOLVES_ROLE`은 방향 없이 조항과 역할을 단순 연결하는
  데 그쳤다.

- **공통으로 잘 잡힌 부분** — 두 프롬프트 모두 상품/특약 구조
  (`InsuranceProduct`/`Article`), 급부 종류(`BenefitType`/
  `InsuranceBenefit`), 당사자 역할(`ContractPartyRole`/`PartyRole`),
  펀드/계정(`InvestmentFund`/`AccountOrFund`)은 유사한 수준으로
  포착했다 — 이 문서가 애초에 보험 약관이라는 성격이 뚜렷해 general
  프롬프트도 어느 정도 도메인에 맞는 타입을 유추했기 때문으로 보인다.
  차이는 "구조가 뚜렷한 도메인 개체"보다는 "법률 문서 특유의 구조
  (정의, 조문 참조, 면책, 절차적 권리)"에서 뚜렷하게 나타났다.

## 결론

법률/보험 약관처럼 정의 조항과 조/항/호 구조, 면책사유·절차적 권리가
명시적으로 존재하는 문서에서는 `legal` 프롬프트가 `general` 프롬프트보다
문서 고유의 구조를 더 세밀하게 스키마에 반영한다. 특히 GraphRAG 검색
품질에 직접 영향을 주는 정의-용어 연결과 조문 간 참조가 `general`
프롬프트에는 아예 빠져 있었다는 점이 가장 실질적인 차이다.
