# Offline reproduction report

Checks: **35** | OK: **35** | MISMATCH: **0**

| Item | Verdict | Detail |
|---|---|---|
| phase_a sonnet/c0_no_message | OK | got (20, 10, 20) want (20, 10, 20) |
| phase_a sonnet/c1_neutral | OK | got (20, 10, 20) want (20, 10, 20) |
| phase_a sonnet/c2_a_coordination | OK | got (19, 10, 19) want (19, 10, 19) |
| phase_a sonnet/c3_b_coordination | OK | got (8, 3, 15) want (8, 3, 15) |
| phase_a gpt54mini/c0_no_message | OK | got (20, 10, 20) want (20, 10, 20) |
| phase_a gpt54mini/c1_neutral | OK | got (16, 9, 17) want (16, 9, 17) |
| phase_a gpt54mini/c2_a_coordination | OK | got (7, 2, 15) want (7, 2, 15) |
| phase_a gpt54mini/c3_b_coordination | OK | got (20, 10, 20) want (20, 10, 20) |
| phase_b auroc monitor=sonnet__receiver=sonnet/M1 | OK | got 1.000 want 1.000 |
| phase_b auroc monitor=sonnet__receiver=sonnet/M2 | OK | got 0.500 want 0.500 |
| phase_b auroc monitor=sonnet__receiver=sonnet/M3 | OK | got 1.000 want 1.000 |
| phase_b auroc monitor=sonnet__receiver=sonnet/M4 | OK | got 1.000 want 1.000 |
| phase_b auroc monitor=sonnet__receiver=gpt54mini/M1 | OK | got 1.000 want 1.000 |
| phase_b auroc monitor=sonnet__receiver=gpt54mini/M2 | OK | got 0.500 want 0.500 |
| phase_b auroc monitor=sonnet__receiver=gpt54mini/M3 | OK | got 1.000 want 1.000 |
| phase_b auroc monitor=sonnet__receiver=gpt54mini/M4 | OK | got 1.000 want 1.000 |
| phase_b auroc monitor=gpt__receiver=sonnet/M1 | OK | got 1.000 want 1.000 |
| phase_b auroc monitor=gpt__receiver=sonnet/M2 | OK | got 0.500 want 0.500 |
| phase_b auroc monitor=gpt__receiver=sonnet/M3 | OK | got 1.000 want 1.000 |
| phase_b auroc monitor=gpt__receiver=sonnet/M4 | OK | got 1.000 want 1.000 |
| phase_b auroc monitor=gpt__receiver=gpt54mini/M1 | OK | got 1.000 want 1.000 |
| phase_b auroc monitor=gpt__receiver=gpt54mini/M2 | OK | got 0.500 want 0.500 |
| phase_b auroc monitor=gpt__receiver=gpt54mini/M3 | OK | got 1.000 want 1.000 |
| phase_b auroc monitor=gpt__receiver=gpt54mini/M4 | OK | got 1.000 want 1.000 |
| e1 sonnet46/T | OK | got (-0.150, [-0.250, -0.050]) want (-0.150, [-0.250, -0.050]) |
| e1 sonnet46/J | OK | got (-0.150, [-0.250, -0.050]) want (-0.150, [-0.250, -0.050]) |
| e1 sonnet46/I | OK | got (-0.300, [-0.500, -0.100]) want (-0.300, [-0.500, -0.100]) |
| e1 gpt54mini/T | OK | got (0.175, [0.075, 0.275]) want (0.175, [0.075, 0.275]) |
| e1 gpt54mini/J | OK | got (0.025, [-0.100, 0.150]) want (0.025, [-0.100, 0.150]) |
| e1 gpt54mini/I | OK | got (-0.050, [-0.300, 0.200]) want (-0.050, [-0.300, 0.200]) |
| phase_a valid trials | OK | got 160 want 160 |
| phase_b resolved sonnet | OK | got 40 want 40 |
| phase_b resolved gpt54mini | OK | got 40 want 40 |
| e1 trials sonnet46 | OK | got 80 want 80 |
| e1 trials gpt54mini | OK | got 80 want 80 |
