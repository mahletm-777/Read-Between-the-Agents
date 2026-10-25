# Reproduced tables

Generated offline by `supplementary/analysis/run_all.py`. No network access.

## Table 1 - sender-receiver interaction (denominator 20 per cell)

| Receiver | Condition | receiver B | B-rate | BB | OB | excluded |
|---|---|---:|---:|---:|---:|---:|
| sonnet | c0_no_message | 20 | 1.00 | 10 | 20 | 0 |
| sonnet | c1_neutral | 20 | 1.00 | 10 | 20 | 0 |
| sonnet | c2_a_coordination | 19 | 0.95 | 10 | 19 | 0 |
| sonnet | c3_b_coordination | 8 | 0.40 | 3 | 15 | 0 |
| gpt54mini | c0_no_message | 20 | 1.00 | 10 | 20 | 0 |
| gpt54mini | c1_neutral | 16 | 0.80 | 9 | 17 | 0 |
| gpt54mini | c2_a_coordination | 7 | 0.35 | 2 | 15 | 0 |
| gpt54mini | c3_b_coordination | 20 | 1.00 | 10 | 20 | 0 |

## Table 2 - monitor evaluation AUROC (resolved population, 20 E+ / 20 E-)

| Combination | M1 | M2 | M3 | M4 |
|---|---:|---:|---:|---:|
| monitor=sonnet__receiver=sonnet | 1.000 | 0.500 | 1.000 | 1.000 |
| monitor=sonnet__receiver=gpt54mini | 1.000 | 0.500 | 1.000 | 1.000 |
| monitor=gpt__receiver=sonnet | 1.000 | 0.500 | 1.000 | 1.000 |
| monitor=gpt__receiver=gpt54mini | 1.000 | 0.500 | 1.000 | 1.000 |

## Table 3 - pre-specified monitor contrasts (primary = M3 - M1)

| Combination | delta | ci95 | n_boot_valid | n_scenarios |
|---|---:|---|---:|---:|
| monitor=sonnet__receiver=sonnet | 0.000 | [0.000, 0.000] | 2000 | 20 |
| monitor=sonnet__receiver=gpt54mini | 0.000 | [0.000, 0.000] | 2000 | 20 |
| monitor=gpt__receiver=sonnet | 0.000 | [0.000, 0.000] | 2000 | 20 |
| monitor=gpt__receiver=gpt54mini | 0.000 | [0.000, 0.000] | 2000 | 20 |

## Table 4 - exploratory receiver follow-up (per arm)

| Arm | Contrast | point | ci95 |
|---|---|---:|---|
| sonnet46 | T | -0.150 | [-0.250, -0.050] |
| sonnet46 | J | -0.150 | [-0.250, -0.050] |
| sonnet46 | I | -0.300 | [-0.500, -0.100] |
| gpt54mini | T | 0.175 | [0.075, 0.275] |
| gpt54mini | J | 0.025 | [-0.100, 0.150] |
| gpt54mini | I | -0.050 | [-0.300, 0.200] |
