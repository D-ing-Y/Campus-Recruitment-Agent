# Campus Job Agent Instructions

## Agent skills

### Issue tracker

Work is tracked in GitHub Issues for `D-ing-Y/Campus-Recruitment-Agent`. See `docs/agents/issue-tracker.md`.

### Domain docs

This is a single-context project with an existing document-driven layout. Read the glossary, project docs, architecture docs, ADRs, contracts, requirements, and eval plans listed in `docs/agents/domain.md`.

### Installed Matt Pocock skills

Use `grill-with-docs`, `to-spec`, and `to-tickets` for demand analysis and planning. Use `improve-codebase-architecture`, `codebase-design`, and `domain-modeling` for architecture scans. Use `implement`, `tdd`, `diagnosing-bugs`, and `code-review` for implementation feedback loops.

## Project workflow

Follow the existing project order before code changes:

1. Requirements
2. RFC
3. ADR
4. Contracts
5. Tasks
6. Eval plan
7. Implementation
8. Tests
9. Eval report
10. Documentation closeout

For each vertical workflow, keep the subgraph closed and connected across contract, evidence, model, Validator, projection or policy, persistence, Graph, CLI or observability, and Eval.

After cross-module work, replay the downstream typed handoff before advancing to the next work package. Automated checks do not replace authenticated-live validation or human confirmation.
