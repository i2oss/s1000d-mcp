---
name: review-data-module
description: Run a full review of one or more S1000D data modules -- schema validation, cross-reference integrity, and applicability checking -- and propose fixes for every problem found, using the s1000d-mcp tools.
---

# Review a data module

This skill chains the individual `s1000d-mcp` tools into one review
workflow: it's the difference between having four separate checks and
having something that behaves like an actual reviewer. Use it when the
user asks you to "review", "check", or "find problems with" a data module
(or a directory of them), or asks what's wrong with one before they hand
it off.

## When NOT to use this

If the user only wants one specific check (e.g. "just validate this
against the schema" or "does this module have any dangling refs"), call
that one tool directly instead of running the whole chain -- it's faster
and the extra output isn't what they asked for.

## Inputs you need

- `dm_path`: the data module file to review. If the user gives a
  directory instead of a single file, run the workflow below once per
  `*.XML` file directly in that directory (cross-reference and
  applicability checks are directory-scoped anyway -- see step 2).
- `directory`: the directory `dm_path` lives in, needed for
  `check_cross_references` and `check_applicability`, which check a
  whole directory's worth of modules at once rather than one file. If
  the user only gave a file, use its parent directory.
- Whether the user wants fix suggestions at all -- `suggest_fix` calls
  the Anthropic API and costs a little money and time per error. Default
  to yes (that's the point of this skill), but if the user says "just
  tell me what's wrong, don't fix it" or `ANTHROPIC_API_KEY` isn't set
  (a `suggest_fix` call will come back with `ok: false` and a reason
  saying so), skip straight to reporting the raw findings without
  suggested fixes.

## Steps

1. **Schema validation.** Call `validate_xml_schema(dm_path)`. If
   `valid` is `false`, each entry in `errors` is one problem to
   potentially fix.

2. **Cross-reference and applicability checks (directory-scoped).**
   Call `check_cross_references(directory)` and
   `check_applicability(directory)` once for the module's directory (not
   once per file -- both tools already return results for every module
   in the directory, so re-run them only if you're reviewing a
   different directory). From each result, keep only the entries that
   belong to the module under review:
   - `check_cross_references` result: filter `dangling` (if present) or
     inspect whether this module's DMC appears in `orphans` -- an
     orphan isn't necessarily a defect (see samples/corpus module E,
     a maintenance schedule that's deliberately not referenced by
     anything), so note it but don't treat it as an error on the same
     level as a dangling reference.
   - `check_applicability` result: filter `violations` where `file`
     matches this module.

3. **Propose a fix for each concrete problem.** For every schema error
   and every dangling reference / applicability violation found above
   (skip orphans -- there's nothing to "fix", it may just be intentional,
   flag it as a note instead), call:
   `suggest_fix(dm_path, error=<that finding's dict>)`.
   Pass the finding exactly as the other tool returned it; `suggest_fix`
   only needs a JSON-serializable dict describing the problem, it
   doesn't require a specific shape beyond that.
   If `suggest_fix` comes back with `ok: false`, report the `reason`
   (e.g. missing API key) alongside the raw finding rather than silently
   dropping it -- the user should know a fix was attempted and why it
   didn't produce a suggestion.

4. **Summarize.** Report back in this shape, not a wall of raw tool
   output:
   - One line per module reviewed: schema valid/invalid, dangling ref
     count, applicability violation count, orphan status.
   - For each concrete problem: the error/violation itself (line number
     when available), and, if a fix was generated, the `explanation`
     and `confidence`, with the `corrected_xml` snippet shown so the
     user can apply it themselves (this skill proposes fixes, it does
     not silently edit files -- always let the user review and apply a
     suggested change before it touches their file).
   - If everything came back clean (schema valid, no dangling refs, no
     applicability violations), say so plainly -- a review that finds
     nothing wrong is still a completed review, not a failure to find
     something.

## Example

```
review-data-module samples/corpus/DMC-MERM100-A-049-00-00AA-00A-041A-A_001-00_EN-US.XML
```

1. `validate_xml_schema("samples/corpus/DMC-...-041A-...XML")` ->
   `valid: true`, no errors.
2. `check_cross_references("samples/corpus")` -> this module (the
   "install inlet filter" procedure) has no dangling refs itself, and
   it's referenced by the APU description module, so it isn't an
   orphan either.
3. `check_applicability("samples/corpus")` -> no violations for this
   module.
4. Nothing to send to `suggest_fix`.
5. Report: "Schema-valid, no dangling cross-references, no
   applicability violations. No issues found."

For a module with a real problem (e.g. one of the
`samples/schema-invalid`, `samples/broken-refs`, or
`samples/applicability-invalid` fixtures), the same steps produce one or
more findings, each run through `suggest_fix`, summarized as described
above.
