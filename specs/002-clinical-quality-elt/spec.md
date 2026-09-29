# Feature Specification: Synthetic clinical quality ETL

**Feature**: `002-clinical-quality-elt` | **Created**: 2026-09-29 | **Status**: Implemented

## User journeys

1. **Facility LOS**: Given synthetic Clarity-like hospital encounters and Caboodle-like encounter, patient and facility extracts, load a normalized inpatient encounter mart and report discharged-encounter counts, total hours and average elapsed hours by facility. Retain transfers in LOS.
2. **Readmissions**: For index inpatient encounters discharged home with full 30-day follow-up as of an operator-supplied cutoff, report the count and percentage having at least one unplanned, non-transfer-source inpatient admission at any sample facility strictly after discharge and within 720 hours. Exclude transfer and expired index discharges. Planned admissions do not count as readmissions but their home discharges may themselves be index encounters. Subsequent encounters can be both a readmission and a later index. Each index contributes at most one readmission; attribute it to the **index facility**.
3. **Grounded automation**: Translate facility, admission-type, admission-source and discharge codes using a versioned triple graph; validate LOS source fields and derive readmission window and cohort rules from measure facts. Preserve source keys, graph version and mapping fact IDs on each encounter, and metric fact IDs for each run; allow DeepAgents to inspect evidence then run the bounded ETL tool.

## Requirements and acceptance

- Keep prior synthetic sales POC working under `--pipeline sales`.
- Load illustrative `PAT_ENC_HSP`, `EncounterFact`, `PatientDim` and `FacilityDim` CSVs into raw SQLite tables, join by encounter and patient keys, and reject missing/duplicate/orphan/contradictory records.
- Require valid timestamps with positive length, no overlapping same-patient encounters, and no encounters beyond the as-of cutoff. Reject unknown or ambiguous graph facts and invalid graph concept targets.
- One transaction replaces raw tables, encounter mart, facility LOS and readmission mart; invalid input leaves prior snapshots intact; reruns are idempotent.
- Exclude immature index discharges rather than silently counting their future as no readmission. Null denominators produce a NULL rate; the cutoff and graph version are stored with results.
- Use `data/knowledge_graph.json` as the canonical clinical graph; keep sales facts separate. Reject absent or unsupported measure facts instead of reverting to hard-coded defaults.
- Sample at `2026-05-31T00:00:00` yields North Hospital 7 LOS discharges, 44.57 average hours, 3 / 6 (50%) readmission indices; South Hospital 2 LOS discharges, 48 average hours, 0 / 2 (0%) readmission indices.

## Assumptions / exclusions

- **Not actual Epic schemas or clinical-quality certification**: field names and join keys are synthetic illustrative contracts, not assertions about a particular installation; validate against local Epic licensing/data dictionaries before connecting to real data.
- All records are inpatient stays, UTC timestamps without timezone offsets, no patient identifiers outside fabricated P1/P2/P3. This POC does not risk-adjust, exclude unrelated planned index encounters, handle outside-network admissions, or implement CMS/HEDIS measures.
