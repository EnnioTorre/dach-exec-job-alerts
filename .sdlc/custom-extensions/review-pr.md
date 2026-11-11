> Team custom extension for review-pr. Managed by /capture-rules.

## Rules added 2026-11-11

## Never delete a pending review to unblock posting

- **Never delete an existing pending review to post your own comment.** Posting a single inline
  comment via `gh api repos/.../pulls/<n>/comments` creates/uses a pending review; if one already
  exists you get HTTP 422 *"user_id can only have one pending review per pull request"*. That pending
  review may be a **human's in-progress draft** — deleting it destroys their unsubmitted comments,
  unrecoverably. On the 422, **stop and ask the user** to submit or discard their draft, or post the
  findings as an issue comment / summary instead.
  *Why: during PR #140 review an automated inline comment hit the 422 and the human's pending review
  draft was deleted to unblock — their review was lost and had to be re-done.*
