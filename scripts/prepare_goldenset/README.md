# Markdown 골드 QA 데이터셋 생성기

지정한 폴더의 `*.md` 파일을 재귀적으로 읽어 문서별 중요 질문과 원문 근거가
포함된 답지를 생성합니다. 질문 생성과 답지 생성을 별도의 LLM 호출로 분리하며,
답지의 인용문은 코드가 원문에 정확히 존재하는지 다시 확인합니다.

## 실행

백엔드 가상환경과 OpenRouter 설정을 사용합니다.

루트 폴더에서는 Makefile을 이용하는 것이 가장 간단합니다.

```bash
export OPENROUTER_API_KEY="..."
make goldenset INPUT_DIR=./documents
```

출력 경로, 질문 수, 모델을 변경할 수 있습니다.

```bash
make goldenset \
  INPUT_DIR=./documents \
  OUTPUT_DIR=./goldenset \
  QUESTIONS_PER_DOCUMENT=12 \
  MODEL=openai/gpt-4o-mini
```

기존 결과까지 다시 생성하려면 다음을 실행합니다.

```bash
make goldenset-overwrite INPUT_DIR=./documents
```

아래와 같이 Python 스크립트를 직접 실행할 수도 있습니다.

```bash
cd backend
source .venv/bin/activate
cd ..
export OPENROUTER_API_KEY="..."
python scripts/prepare_goldenset/prepare_goldenset.py ./docs \
  --output-dir ./goldenset \
  --questions-per-document 10
```

기본 모델은 `OPENROUTER_MODEL`이며, 환경변수가 없으면
`openai/gpt-4o-mini`입니다. 명령행에서 바꿀 수도 있습니다.

```bash
python scripts/prepare_goldenset/prepare_goldenset.py ./documents \
  --model openai/gpt-4o-mini \
  --questions-per-document 12 \
  --overwrite
```

주요 옵션:

- `--output-dir`: 결과 폴더, 기본값 `./goldenset`
- `--questions-per-document`: 문서당 질문 수, 기본값 `10`
- `--question-context-chars`: 질문 생성에 전달할 최대 Markdown 문자 수, 기본값 `24000`.
  큰 문서는 헤더별 내용을 유지하면서 이 범위로 결정론적으로 축약합니다. 답변 생성은
  원문 전체를 사용해 근거 인용을 검증합니다.
- `--no-recursive`: 입력 폴더 바로 아래의 Markdown만 처리
- `--overwrite`: 기존 문서별 결과를 다시 생성
- `--max-process-files`: 정렬된 Markdown 파일 중 최대 처리 개수. 생략하면 전체 파일을
  처리합니다. `MAX_PROC_FILEN` Make 변수로도 지정할 수 있습니다.
- `--log-file`: 전체 실행 로그를 기록할 단일 파일. 기본값은
  `<output-dir>/prepare_goldenset.log`

## 결과

입력이 `guide/a.md`라면 다음 결과가 만들어집니다.

```text
goldenset/
├── guide/a.golden.json  # 문서별 질문, 답, 원자 사실, 근거 및 검증 결과
├── goldenset.jsonl      # 모든 QA를 한 줄당 한 항목으로 합친 파일
├── manifest.json        # 모델, 생성 시각, 실패 및 경고 요약
└── prepare_goldenset.log # 문서별 처리 단계와 오류를 포함한 전체 실행 로그
```

각 QA 항목은 다음 정보를 포함합니다.

- `question_type`: 개체, 속성, 관계, 다중 홉, 목록, 참·거짓, 무응답
- `answerable`: 문서만으로 답할 수 있는지 여부
- `answer_facts`: 정답을 이루는 원자적 `(subject, predicate, object)` 사실
- `evidence`: 원문의 정확한 인용문과 코드가 다시 계산한 줄 번호
- `validation`: 근거 인용 검증 상태

근거 인용이 원문과 정확히 일치하지 않으면 해당 근거를 제거합니다. 답할 수 있다고
생성됐지만 검증된 근거가 하나도 없으면 안전하게 `answerable=false`로 바꾸고
`manifest.json`에 경고를 기록합니다. 이 검사는 인용문의 존재만 확인하므로, 최종
골드 데이터로 확정하기 전에는 도메인 전문가가 중요 질문과 의미적 정답을 표본
검수하는 것이 좋습니다.
