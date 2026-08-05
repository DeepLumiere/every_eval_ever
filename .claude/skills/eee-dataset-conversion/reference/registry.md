# Canonicalization — the eval-card-registry

*Scope: making model/benchmark ids canonical. The registry-side mechanics live in
the registry repo (pointer at the bottom).*

Model **and** benchmark ids in your output must be **canonical**, resolved through
the eval-card-registry. Unresolved slugs auto-create `draft` canonicals and
fragment the data (two ids for one thing), so this is part of shipping an adapter.

What an adapter author needs here:
- **Search the registry first; alias your raw slug to the *existing* canonical;
  create a new canonical only if the entity is genuinely absent** — a new canonical
  is a lasting namespace decision, so **ask the operator** before deliberately minting
  one (SKILL.md step 7). (A *batch* adapter can't gate per-id: resolve-by-default will
  auto-create drafts for the tail — surface those in the decision log rather than block,
  see next bullet.)
- **Resolve by default; flag what stays unverified.** The registry is a separate repo,
  but its resolver is a **hosted, no-auth endpoint**:
  `POST https://evaleval-entity-registry.hf.space/api/v1/resolve` with
  `{"raw_value","entity_type"}` (`entity_type` ∈ `model`/`benchmark`/`metric`/`harness`/
  `org`/…), returning `canonical_id` + `strategy`/`confidence`/`created_new`/
  `review_status`. **Prefer resolving live** and use `canonical_id` for the join-key
  field (`model_info.id`); record the provenance fields in `additional_details`. Give
  the adapter an **opt-out flag** (e.g. `--no-registry-resolve`) for speed/offline/
  determinism, and on opt-out **or any network error fall back to the path id, marked
  unverified — never fatal** (a converter must not die because a Space was asleep). Use
  `requests` (already a dep) so the flag is about speed, not a new dependency. Whatever
  the resolver couldn't confidently place — `created_new` drafts, low `confidence`,
  non-`reviewed` status — goes in the decision log for a follow-up alias PR.
- **Never key `evaluation_id` on the resolved canonical id** — the registry can re-map
  a draft later. Resolved id = join key; raw source identity = record identity. Rule and
  reasoning: `fields.md` model_info.
- **Disambiguate look-alikes** — `arc` (AI2 Reasoning Challenge, `allenai/ai2_arc`)
  is a *different* dataset from `arc-agi` (Chollet). Confirm from the paper.
- Adding aliases/canonicals is a **separate PR to the registry repo** (not the
  adapter repo, not the datastore).

The registry-side mechanics — which YAML file, when the `normalized` matcher already
covers a variant, the id standards (HF-true casing, closed-model form, etc.), and the
`seed --local` + resolver verification — live in the **registry repo's own
`CONTRIBUTING.md`**, not in this skill. This split is deliberate: registry
contribution is a different task with a different audience.
