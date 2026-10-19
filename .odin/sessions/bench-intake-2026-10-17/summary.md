# Intake tier bench — 2026-10-17

Sonnet (`standard`) vs Opus (`deep`, reference, same bench run). 2 stored runs
(bills tracker, pastebin) + 4 synthetic requests (`.odin/bench/requests/`).

| stage | tier | n | ok% | p50 s | mean $ | key-Jaccard | locked-J | default-agree% |
|---|---|---|---|---|---|---|---|---|
| intake | deep | 6 | 100 | 42.3 | 0.1148 | - | - | - |
| intake | standard | 6 | 100 | 13.6 | 0.0336 | 0.42 | 0.61 | 67 |

Per request (standard vs deep): 60cd5166f16a J .41 / locked .89 / agree 100%;
c6973d74ac0e .55 / 1.00 / 100%; cli-tool .45 / .20 / 60%; go-service .36 / .60 / 60%;
node-api .36 / .50 / 80%; static-site .38 / .44 / 0%.

Decision: keep `deep` as the intake default (`PIPEZ_INTAKE_TIER` stays available).
Raw outputs were not persisted, so question quality was not compared.
