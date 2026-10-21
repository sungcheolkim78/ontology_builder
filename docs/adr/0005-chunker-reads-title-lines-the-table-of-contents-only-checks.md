# The chunker reads title lines; the table of contents only checks it

A policy prints its own table of contents ("상품 목차": the main contract, each rider, the appendix), which is a ready-made list of what the chunker should find. Building the chunks from it would be shorter than reading title lines out of the body, but a wrong or unreadable table of contents (a wrapped entry that lost its page number, a list that runs over several pages) would then cut the document in the wrong places with nothing to say so, and checking the chunks against the same list would prove nothing. So the chunker finds riders from the body itself (a `OOO특약 약관` title line followed by the rider's first article, or numbering that starts over), and `app.preprocess.chunking_eval` reads the table of contents as the reference to score that against: rider names, article sequence, heading coverage, 편/관 hierarchy, reported side by side (`docs/ontology/chunking_quality.md`).

The cited laws at the end of a policy are kept, not dropped, as `appendix` sections, one per law: they are text a question can be about, and deciding whether extraction should skip them belongs to extraction, not to the chunker. A chunk's `path` keeps its old shape (`rider > 편/관 > 조`) so `ChunkView` and the chunk groups that read it are untouched; `rider_type`, `rider_name`, `rider_name_source`, `variant` are added beside it.

## Consequences

- `rider_name_source` says how a name was found (`title`, `guess`, `fallback`, `main`, `appendix`); `fallback` (`특약_N`) is counted in the report instead of being passed off as a name.
- A rider whose title the converter lost is still split off where its numbering starts over, but named from the text above it, so it can read wrong; the report's `match` and `susp` columns are where that shows.
- Chunk ids changed (`부록 > …`, `@plan`); a `chunks.json` written before this is not regenerated until someone asks for it. Nothing stored refers to a chunk id; `ChunkView` uses it only as a list key, which is now unique where it used to repeat.
