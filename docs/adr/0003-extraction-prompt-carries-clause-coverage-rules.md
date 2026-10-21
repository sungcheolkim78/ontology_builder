# The extraction prompt carries clause-coverage rules

The base instructions turn a short clause into one node that states its gist and drop the clause's other conditions, consequences and list items, which is most of what the golden questions that remain unanswered ask for. `EXTRACT_COVERAGE_RULES` (every clause gets a node stating what it provides, separate rules get separate nodes, list items go in the `detail`) is appended to the end of `EXTRACT_PROMPT`, exactly as it was evaluated. On two documents it raised the answerable golden questions from 59 to 66 of 83, mostly list and multi-clause questions.

## Consequences

- About 40-50% more output tokens per extraction, about 36% more nodes and 44% more edges, and a smaller share of nodes that quote their evidence (56% to 42%, 60% to 49%). The effect of the longer chat context was not measured.
- Do not weave the rules into the base instructions: they refer to the `detail` limit above them and were measured appended.
- The prompt before this change is the one in commit `6434cb3`, for any baseline.
- A second extraction pass that adds what the first left out was tried and dropped: smaller gain, twice the calls.
