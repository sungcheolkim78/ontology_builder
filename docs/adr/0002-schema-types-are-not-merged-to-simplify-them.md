# Schema types are not merged to simplify the schema

The idea was that a smaller schema (fewer node and edge types) would extract and retrieve better. It was tried on two documents by merging the types with a handful of instances into their nearest backbone type and dropping edge types nothing uses (17 to 12 and 13 to 9 node types). Relabelling the existing graph left the score unchanged or lower by 2 questions, and extracting again under the smaller schema lost 7 of 83 questions in all; node and edge counts barely moved, so nothing was gained in size either. Merging types is therefore not part of the pipeline, and no refinement step is wired into the app.

`app.ontology.refinement` (a merge mapping applied to a schema and its graph) and the level-1 and level-2 mappings in `scripts/refinement_mappings/` stay as tools. The level-2 mappings were never run. Reopen this only with a reason other than accuracy (a schema people must read), and measure with repeated runs: the loss is near the run-to-run noise.

Not verified: that small types help retrieval because a type with few nodes is returned whole, which a merge into a large type breaks.
