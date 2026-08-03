# Issue tracker: GitHub

Issues, specs, and tickets for this repo live in GitHub Issues for `D-ing-Y/Campus-Recruitment-Agent`.

Use the `gh` CLI from the repo root so it can infer the repository from `git remote -v`.

## Conventions

- Create an issue: `gh issue create --title "..." --body "..."`
- Read an issue: `gh issue view <number> --comments`
- List issues: `gh issue list --state open --json number,title,body,labels,comments`
- Comment on an issue: `gh issue comment <number> --body "..."`
- Apply or remove labels: `gh issue edit <number> --add-label "..."` / `gh issue edit <number> --remove-label "..."`
- Close an issue: `gh issue close <number> --comment "..."`

## Pull requests as a triage surface

PRs as a request surface: no.

If this changes later, flip this to `yes` and use `gh pr` equivalents for reading, commenting, labeling, and closing PRs.

## When a skill says "publish to the issue tracker"

Create a GitHub issue unless the user explicitly asks for local Markdown only.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.

## Local fallback

If GitHub access is unavailable, write draft specs or tickets under `.scratch/<feature>/` and mark them as local drafts until they are published to GitHub.
