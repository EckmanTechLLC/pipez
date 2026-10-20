# Plan-stage bench — 2026-10-20

5 stored first plans + 2 revision rounds, replayed through deep@high (reference),
deep@medium and standard (Sonnet, medium). Raw outputs in the JSON.

| stage | tier | n | ok% | p50 s | mean $ | mean out-tok | tasks | Δtasks | deploy-match% | forbidden% |
|---|---|---|---|---|---|---|---|---|---|---|
| plan | deep@high | 5 | 100 | 234.4 | 0.5956 | 27305 | 6.6 | - | - | 0 |
| plan | deep@medium | 5 | 100 | 143.8 | 0.3963 | 17338 | 6.6 | +0.0 | 100 | 20 |
| plan | standard | 5 | 100 | 35.7 | 0.0761 | 5132 | 5.2 | -1.4 | 100 | 0 |
| plan_revision | deep@high | 2 | 100 | 119.6 | 0.4979 | 18475 | 6.5 | - | - | 0 |
| plan_revision | deep@medium | 2 | 100 | 118.6 | 0.4961 | 18388 | 6.5 | +0.0 | 100 | 0 |
| plan_revision | standard | 2 | 100 | 59.7 | 0.1794 | 11518 | 6.5 | +0.0 | 100 | 0 |

Task-prompt volume (chars, all tasks): deep@high 9.7–21.5k; deep@medium 7.7–10k;
standard 2.1–3.7k. Same task counts high vs medium; standard merges tasks (4–7).
The one "forbidden" scaffold (deep@medium, child run da31df2bfc2a: `git checkout
pipez/<parent>`) was produced from a replayed prompt that lacked the
`# Existing repository` section (the parent bundle is not in the local artifacts
dir), so it is a bench artefact, not a verdict on medium.
Revision replays: stored plan already reflects the note, so all tiers mostly
reproduce it; standard matched task count and deploy spec at ~1/3 the cost.

Recommendation: PIPEZ_PLAN_REVISION_TIER=standard; PIPEZ_PLAN_EFFORT=medium for
first plans (half cost/time, same structure, shorter task prompts — watch the next
real build's leaf failure rate); do not move first plans to standard.
