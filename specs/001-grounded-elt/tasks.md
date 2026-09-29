# Tasks: Grounded synthetic sales ELT

**Input**: [spec.md](./spec.md), [plan.md](./plan.md)

## Phase 1: Spec Kit and setup

- [x] T001 Initialize the isolated project with Spec Kit's Copilot integration and record a project constitution.
- [x] T002 Write the feature specification, technical plan, and Python dependency manifest.

## Phase 2: Reproducible ELT (US1)

- [x] T003 [US1] Add fabricated orders and versioned graph facts in `data/`.
- [x] T004 [US1] Implement CSV validation, SQLite staging, mart generation, and atomic replacement in `grounded_elt/pipeline.py`.

## Phase 3: Grounding (US2)

- [x] T005 [US2] Implement graph path traversal, currency lookup, and graph-fact lineage in `grounded_elt/pipeline.py`.
- [x] T006 [US2] Test totals, reruns, graph changes, and failed loads in `tests/test_pipeline.py`.

## Phase 4: Agent and convergence (US3)

- [x] T007 [US3] Connect DeepAgents domain tools and offline CLI in `grounded_elt/cli.py`.
- [x] T008 [US3] Document run instructions, model credentials, and limitations in `README.md`.
- [x] T009 [US3] Verify sample output, tests, and agent construction; record convergence results in `specs/001-grounded-elt/convergence.md`.
