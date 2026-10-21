# Golden questions are judged by the answer reading, not by quoted evidence

A golden question can be scored by whether a retrieved node quotes the passage that answers it (strict), by embedding similarity to that passage (semantic), by whether the retrieved text states the facts of the golden answer (answer), or by an LLM (judge). The first was the original and is the one a reader would assume, but it stood at 5% on a graph that answered 39% of the questions, and the same extraction scored 26% and 13% on two runs, because a node often states a fact while quoting the clause beside it. The answer reading is free, deterministic and stable, and agrees with the LLM judge in total (13 and 13, 23 and 24 of 38). It is the one to decide with; the other readings are kept, reported side by side and never merged.

## Consequences

- The text it reads is what the chat is shown, including the quoted evidence (`근거:`): leaving that out understated every earlier answer figure (fixed 2026-10-16).
- It is a heuristic (figures must match, the rest by character overlap) and misses a fact that is paraphrased; the judge is the check for that, at one LLM call per question.
- Differences of two or three questions are noise whatever the reading.
