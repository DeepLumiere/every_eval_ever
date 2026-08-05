<!-- Thanks for contributing! Delete sections that don't apply. -->

## What / source
<!-- What does this change do? For an adapter: what source does it convert, and to what grain? -->

## Checklist
- [ ] `python -m every_eval_ever validate <files>` passes at the final
      `data/<collection>/<dev>/<model>/` path — that's where the **semantic** checks run
- [ ] no warnings either: `deployment_type`/`model_availability` set to real values,
      every score inside its declared `[min_score, max_score]`, namespaced `metric_id`
- [ ] records published via `save_evaluation_logs` / `publish_evaluation_logs`, and every
      unconvertible row is in `adapter_reports/` with a non-zero exit
- [ ] offline unit test added + full `pytest tests` green
- [ ] `ruff check` clean
- [ ] model/benchmark ids resolve in the registry (or an alias PR is prepared)
- [ ] content spot-checked (no answer leak, not double-counted, stable `evaluation_id`)

## Decisions & coverage
<!-- Skip this section if this isn't a data/conversion or skill change.
     Otherwise: this PR should be ready to merge, and this section makes the non-obvious
     calls visible so a maintainer can comment and the skill/schema can improve. Log every
     non-obvious CHOICE (not just where it was hard — a confident wrong choice has no
     "friction"). "None" is a valid answer. General items (would recur on other
     datasets) → also a separate `skill`-labeled PR or a `skill-gap` issue. -->

- Decision / where: 
  Chose / instead of: 
  Confidence (high/med/low): 
  General? (yes/no): 

**Coverage:** N source rows → N records, M dropped (reason) — <!-- no silent caps -->

**Operator asked about policy calls?** <!-- new canonical id / big data drop /
ambiguous or unbounded metric / re-hosting large data — which, and what was decided -->

