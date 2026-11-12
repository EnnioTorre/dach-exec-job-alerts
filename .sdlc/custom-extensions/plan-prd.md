> Team custom extension for plan-prd. Managed by /capture-rules.

## Rules added 2026-11-12

## Copy external-system canonical strings verbatim

- **When a plan or spec carries a value that will be matched LITERALLY against an external system
  (LinkedIn "Industries" names, API enum values, HTTP header names, provider response fields), copy
  the canonical string VERBATIM from the PRD / source — never paraphrase, reword, or drop
  punctuation.** A paraphrased value loads fine and passes "non-empty list" validation, then silently
  never matches at runtime.
  *Why: the it-role-classification plan paraphrased the PRD's LinkedIn industry
  `"Technology, Information and Internet"` as `"Technology Information and Internet"` (comma dropped)
  and split `"...Internet/Media"` into a bare `"Media"`; the degraded string would have made S7's
  whole-value match silently miss the largest tech bucket — caught by the gap-critic on PR #161 and
  fixed in `fa256a1`.*