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
