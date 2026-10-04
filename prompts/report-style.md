# Report style

## Structure

1. **Executive summary** - two paragraphs, then three to five key findings as bullets.
2. **Dataset overview** - what the data is, and what it covers.
3. **Data-quality assessment** - missingness, duplicates, and what was cleaned.
4. **Key findings** - one block per finding: statement, evidence, confidence, limitations.
5. **Anomalies** - what was flagged, by which method, against which baseline.
6. **Methodology** - tools used, dataset versions, artifact identifiers.
7. **Limitations** - what the data cannot support.
8. **Reproducibility** - hashes, versions and row counts.
9. **Suggested next questions** - questions, not actions.

## Formatting rules

- Every finding names its evidence artifact. A claim without an artifact reference is
  a bug.
- Every anomaly section carries the caveat that a flagged record is not an error.
- Numbers use the unit they were computed with. If no unit was supplied, do not invent
  one.
- Percentages and counts are shown together so the reader can judge scale.
- Limitations appear under the finding they qualify, not only in the limitations
  section.

## Language rules

- No causal verbs. "alongside", "differed from", "coincided with".
- No absolute quantifiers unless the data covers the whole population.
- No accusatory words for flagged records.
- Report failed steps. A missing analysis is part of the story.