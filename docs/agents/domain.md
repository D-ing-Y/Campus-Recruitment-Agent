# Domain Docs

How Matt Pocock engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- `docs/00_project/glossary.md` for canonical domain vocabulary.
- `docs/00_project/project-charter.md` and `docs/00_project/roadmap.md` for scope and version direction.
- `docs/01_architecture/agent-architecture.md` for the overall Agent, Graph, evidence, and profile architecture.
- Relevant requirements under `docs/03_requirements/`.
- Relevant RFCs under `docs/04_rfc/`.
- Relevant ADRs under `docs/05_adr/`.
- Relevant contracts under `docs/06_contracts/`.
- Relevant eval plans and reports under `docs/07_evaluation/`.
- `docs/02_development/dev-workflow.md`, `docs/02_development/definition-of-done.md`, and `docs/02_development/testing-strategy.md` before claiming readiness.

If a referenced file does not exist for a new version, treat that as a planning gap rather than silently inventing implementation scope.

## File structure

This is a single-context repo with project-specific document folders:

```text
docs/
  00_project/
  01_architecture/
  02_development/
  03_requirements/
  04_rfc/
  05_adr/
  06_contracts/
  07_evaluation/
  08_deployment/
  09_versions/
```

Do not create a parallel root `CONTEXT.md` or `docs/adr/` unless the user explicitly asks to migrate the documentation layout.

## Use the glossary's vocabulary

When naming a concept in an issue title, spec, ticket, refactor proposal, hypothesis, test, or architecture report, use the terms from `docs/00_project/glossary.md`.

If a concept is missing from the glossary, either reuse an existing project term or flag the vocabulary gap for `domain-modeling`.

## Respect project readiness boundaries

For this project, keep these states distinct:

- planned
- Ready for Implementation
- implemented in code
- offline tests passed
- eval report completed
- authenticated-live validated
- human-confirmed
- production-ready

Do not claim a version or workflow is implemented solely because design documents or fixture tests exist.

## Flag ADR and contract conflicts

If an output contradicts an existing ADR or contract, surface the conflict explicitly and propose whether to update the document or change the implementation plan.
