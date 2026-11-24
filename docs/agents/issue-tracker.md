# GitHub Issues

Publish specs and tickets to GitHub Issues in `manchoren/SuperHarry2`. Verify the target with `gh repo view --json nameWithOwner` before the first write of a session. Use `--repo manchoren/SuperHarry2` for writes. On Windows, if `gh` is missing from PATH, call `& 'C:\Program Files\GitHub CLI\gh.exe'` in place of `gh` when that executable exists.

- Read a ticket with `gh issue view <number> --json number,title,body,labels,comments,blockedBy`. Use `gh issue list` with state and label filters for queues.
- Publish with `gh issue create --title <title> --body-file <path>`. Apply labels using the [triage mapping](triage-labels.md).
- Link issues to their parent with `gh issue create --parent <number>` when there is a parent issue.
- Use GitHub's native dependencies with `gh issue create --blocked-by <n>,<n>` or `gh issue edit <child> --add-blocked-by <blocker>`. A ticket is unblocked when all blockers are closed. If native dependencies are unavailable, list blocker issue references under `## Blocked by` in the issue body.
- Use `gh issue edit` to add or remove labels. Post the resolution with `gh issue comment <number> --body-file <path>` before closing a ticket with `gh issue close <number>`.

**PRs as a request surface: no.**

For `/wayfinder`, use a `wayfinder:map` issue as the parent, link tickets as sub-issues, and use `wayfinder:<type>` labels. Pick the first unassigned child with all blockers closed. Assign it before work and record the resolution in the map when closing it.
