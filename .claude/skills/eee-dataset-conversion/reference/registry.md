# Canonicalization — the eval-card-registry

*Scope: making model/benchmark ids canonical. The registry-side mechanics live in
the registry repo (pointer at the bottom).*

Model **and** benchmark ids in your output must be **canonical**, resolved through
the eval-card-registry. Unresolved slugs auto-create `draft` canonicals and
fragment the data (two ids for one thing), so this is part of shipping an adapter.

What an adapter author needs here:
- **Search the registry first; alias your raw slug to the *existing* canonical;
  create a new canonical only if the entity is genuinely absent** — and since a new
  canonical is a lasting namespace decision, **ask the operator** before doing so
  (SKILL.md step 7), don't mint one silently.
- **Verify or flag, don't assume.** You usually can't resolve ids from inside this
  repo (the registry is separate). Either check against its resolver (`POST /resolve`)
  or record each id as **"unverified — maintainer confirm"** in your decision log; an
  assumed-canonical id that validates but doesn't resolve silently fragments the data.
- **Disambiguate look-alikes** — `arc` (AI2 Reasoning Challenge, `allenai/ai2_arc`)
  is a *different* dataset from `arc-agi` (Chollet). Confirm from the paper.
- Adding aliases/canonicals is a **separate PR to the registry repo** (not the
  adapter repo, not the datastore).

The registry-side mechanics — which YAML file, when the `normalized` matcher already
covers a variant, the id standards (HF-true casing, closed-model form, etc.), and the
`seed --local` + resolver verification — live in the **registry repo's own
`CONTRIBUTING.md`**, not in this skill. This split is deliberate: registry
contribution is a different task with a different audience.
