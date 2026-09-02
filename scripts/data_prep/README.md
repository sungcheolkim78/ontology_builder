# 삼성생명 보험약관 데이터 준비

삼성생명 상품공시에서 `개인` 대분류의 중분류별 보험상품을 선택하고 보험약관
PDF를 내려받는 스크립트입니다.

기본 중분류는 다음과 같습니다.

- 보장성
- 저축성
- 어린이

각 분류에서 현재 판매 중인 서로 다른 상품을 우선 선택합니다. 현재 상품이 요청한
수보다 적으면 전체 상품공시에서 판매 시작일이 최근인 과거 상품을 보충합니다.

## 요구사항

- Python 3.10 이상
- 인터넷 연결
- 별도 Python 패키지는 필요하지 않습니다.

## 실행

저장소 루트에서 실행합니다.

```bash
python3 scripts/data_prep/download_samsunglife_terms.py
```

기본 설정은 중분류마다 5개씩, `data/raw/pdf` 아래에 저장합니다.

```text
data/raw/pdf/
├── 보장성/
├── 저축성/
├── 어린이/
└── manifest.json
```

`manifest.json`에는 상품명, 판매 시작일, 상품 코드, 약관 문서 ID, 원본 URL,
로컬 파일 경로, SHA-256 체크섬 및 현재 판매목록 포함 여부가 기록됩니다.

## 주요 옵션

### 상품 수와 출력 폴더 변경

```bash
python3 scripts/data_prep/download_samsunglife_terms.py \
  --per-category 5 \
  --output-dir data/raw/pdf
```

### 일부 중분류만 처리

```bash
python3 scripts/data_prep/download_samsunglife_terms.py \
  --categories 보장성 저축성
```

### 기존 파일 다시 다운로드

기본적으로 같은 이름의 PDF가 있으면 재사용합니다.

```bash
python3 scripts/data_prep/download_samsunglife_terms.py --overwrite
```

### 파일을 저장하지 않고 선정 결과 확인

```bash
python3 scripts/data_prep/download_samsunglife_terms.py --dry-run
```

드라이런에서도 상품 API와 약관 뷰어를 조회하므로 인터넷 연결은 필요하며,
조회 결과는 출력 폴더의 `manifest.json`에 기록됩니다.

### 요청 제한시간 변경

```bash
python3 scripts/data_prep/download_samsunglife_terms.py --timeout 60
```

## 선택 및 검증 방식

1. 삼성생명 판매상품 API에서 `개인 > 중분류` 상품을 조회합니다.
2. 상품명이 같은 여러 판매 기간은 하나의 상품으로 처리합니다.
3. 현재 판매 상품이 부족하면 전체 상품 API를 페이지 단위로 조회합니다.
4. 판매 시작일이 최근인 상품부터 부족한 수를 보충합니다.
5. 공식 PCMS 뷰어에서 보험약관의 실제 PDF 경로를 확인합니다.
6. 내려받은 파일이 `%PDF-` 시그니처로 시작하는지 검사합니다.
7. 완전한 파일을 받은 뒤에만 `.part` 파일을 최종 PDF 이름으로 바꿉니다.

사이트의 API 또는 뷰어 구조가 변경되면 스크립트가 오류를 출력하고
`manifest.json`의 `failures` 항목에 실패 원인을 기록합니다.

## PDF를 Markdown으로 변환

한글 본문과 표를 보존하도록 `pdfplumber` 기반 변환기를 제공합니다.

```bash
python3 -m pip install -r scripts/data_prep/requirements.txt
python3 scripts/data_prep/convert_pdfs_to_markdown.py
```

기본적으로 `data/raw/pdf`를 재귀적으로 읽어 같은 분류 구조로
`data/raw/md`에 저장합니다. 기존 Markdown을 다시 만들려면 다음과 같이
실행합니다.

```bash
python3 scripts/data_prep/convert_pdfs_to_markdown.py --overwrite
```

변환기는 다음 작업을 수행합니다.

- PDF의 한글 텍스트 레이어를 직접 추출
- 페이지 좌표를 이용해 표와 일반 본문 분리
- 표의 줄바꿈을 `<br>`로 보존한 Markdown 표 생성
- 장·조 번호와 글머리 기호를 Markdown 구조로 변환
- 원본 페이지 위치를 `<!-- page: N -->` 주석으로 기록
- 파일별 페이지 수, 인식한 표 수, SHA-256을 `manifest.json`에 기록

이미지로만 구성된 스캔 PDF는 별도의 한글 OCR이 필요합니다. 이 변환기는
텍스트 레이어가 있는 삼성생명 공시 약관을 대상으로 합니다.

## Markdown을 조(條) 단위 JSON으로 청킹

`convert_pdfs_to_markdown.py`가 붙이는 `### 제N조(...)` 헤딩은 조문 경계
외에도 세 가지 이유로 오탐이 섞입니다: (1) 조문 안의 번호 목록(`### 1. ...`)이
같은 헤딩 레벨을 씀, (2) 목차 페이지가 같은 패턴을 페이지 번호와 함께
중복 생성함, (3) PDF 줄바꿈 때문에 문장 중간의 조문 인용이 헤딩으로
잘못 인식됨. 또한 조문 번호(`제1조`부터)는 특약마다 새로 시작되므로 조문
번호만으로는 문서 전체에서 유일한 id를 만들 수 없습니다. 이 스크립트는
이 네 가지를 보정해 조문 단위 JSON을 만듭니다.

```bash
python3 scripts/data_prep/chunk_terms_markdown.py
```

기본적으로 `data/raw/md`를 재귀적으로 읽어 같은 분류 구조로 `data/chunks`에
파일당 하나의 JSON을 씁니다. 각 JSON은 다음 구조입니다.

```json
{
  "source": "보장성/삼성치아보험_2501_약관.md",
  "preamble": {"line_start": 1, "line_end": 1297, "text": "..."},
  "chunks": [
    {
      "id": "0::제1조",
      "section_index": 0,
      "section_label": "주계약",
      "article_no": "1",
      "sub_no": null,
      "title": "목적",
      "path": "주계약 > 제1조(목적)",
      "line_start": 1298,
      "line_end": 1302,
      "text": "..."
    }
  ]
}
```

- `section_index`/`section_label`은 특약마다 리셋되는 `제1조`를 만날 때마다
  새 구간으로 취급해서 붙입니다. `section_label`은 해당 구간이 시작되기
  직전 텍스트에서 "특약"이 포함된 마지막 줄을 찾는 휴리스틱이라 확정적이지
  않습니다 — 못 찾으면 `특약_N`으로 대체됩니다.
- 조문으로 인식되지 않은 `### 제N조...` 줄(목차 중복, 문장 중간 오염)은
  삭제되지 않고 그 시점에 열려 있던 조문의 `text`에 원문 그대로 흡수됩니다.
- 첫 조문 이전의 표지·이용가이드·목차 텍스트는 `preamble`에 남습니다.

알려진 한계: 특약 이름 추출은 최선 노력(best-effort) 휴리스틱이라 일부
특약에서는 이름 대신 본문 문장 한 줄이 잡힐 수 있습니다(예: 실손의료비
약관에서 관찰됨). `section_index`/`id`의 유일성은 이 휴리스틱과 무관하게
항상 보장됩니다.
