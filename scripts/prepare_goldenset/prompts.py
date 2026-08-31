"""Prompt templates for generating document-grounded golden QA data."""

QUESTION_PROMPT = """You are designing a golden evaluation set for an ontology and
knowledge-graph question answering system.

Read the Markdown document and propose exactly {question_count} important,
non-duplicative questions that a real user would ask. Cover the document's main
facts, concepts, relationships, rules, conditions, exceptions, numbers, dates,
and multi-step connections where the text supports them. Prefer questions whose
answers matter for understanding or using the document, rather than trivia about
formatting or headings.

Use a balanced mix of these question types when the document permits it:
- entity: identify an important person, organization, object, or concept
- attribute: retrieve a property, number, date, condition, or definition
- relation: retrieve a direct relationship between entities or concepts
- multi_hop: combine two or more facts explicitly stated in the document
- list: return all matching items from a clearly bounded set
- boolean: verify or refute a claim using the document
- unanswerable: an important-sounding question that this document alone does not answer

At least one question should be unanswerable when {question_count} is 5 or more.
Do not answer the questions. Do not rely on outside knowledge. Write questions in
the document's primary language.

Return ONLY valid JSON in this exact shape:
{{"questions": [{{"id": "q001", "question": "...", "question_type":
"entity|attribute|relation|multi_hop|list|boolean|unanswerable",
"importance": "high|medium", "rationale": "why this tests an important
document capability"}}]}}

Source file: {source_file}

Document:
<document>
{document}
</document>
"""


ANSWER_PROMPT = """You are producing auditable gold answers for a document QA
evaluation set. Answer each question using ONLY the Markdown document below.

Rules:
1. If the document does not contain enough information, set answerable=false,
   answer to null, evidence to [], and briefly state what information is missing.
2. If answerable=true, every material claim in the answer must be supported by
   one or more evidence entries.
3. Each evidence.quote must be an exact, contiguous substring copied from the
   document. Keep it as short as possible while still proving the answer.
4. line_start and line_end are 1-based inclusive line numbers for that exact quote.
5. Do not use outside knowledge or fill gaps with plausible assumptions.
6. For list questions, include every item supported within the scope of the
   question. answer_facts should contain normalized atomic facts, not prose.
7. For multi-hop questions, answer_facts must expose every intermediate fact
   needed to derive the answer.
8. Preserve exact units, dates, thresholds, exceptions, and negation.
9. Write answer and notes in the document's primary language.

Return ONLY valid JSON in this exact shape:
{{"answers": [{{"id": "q001", "answerable": true, "answer": "... or null",
"answer_facts": [{{"subject": "...", "predicate": "...", "object": "..."}}],
"evidence": [{{"quote": "exact source substring", "line_start": 1,
"line_end": 1}}], "notes": "..."}}]}}

Questions:
{questions}

Source file: {source_file}

Document:
<document>
{document}
</document>
"""

