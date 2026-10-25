# Stage definitions: engineering baseline, experimental pilot, scientific evaluation

These three stages must be kept separate and must never be blurred. This distinction was previously
recorded in `docs/open_questions.md` §H (lines 626–642 of that file); it is preserved verbatim here
because `AGENTS.md` depends on it and the register file was retired in the release tidy-up.

**The full text of the retired register remains recoverable from git history:**

```bash
git show 14bbd1806fdde2b655b40057cdf74869ae388e59:docs/open_questions.md
```

## The three stages

> **Milestones (unchanged in substance)**
>
> 1. **Engineering baseline.** Verifies the pipeline executes and records can be written and read.
>    None of the decisions above block this.
> 2. **Experimental pilot.** Generates a small batch of trial traces under defined conditions.
>    Requires the ground-truth definition (C.3), the trace schema (C.1, C.5), the protocol authority
>    decision (F.1), message stimuli (F.3), and pilot parameters (F.4).
> 3. **Scientific evaluation.** Supports a claim about monitor performance or communication effects.
>    Requires all of the above plus the labeling procedure (C.4) and the analysis plan (F.4).

## Dependency graph (verbatim)

```
C.3 ground truth ──→ C.4 labeling ──→ monitor evaluation
C.1 trace schema ──→ C.4 labeling
C.1 trace schema ──→ C.5 missing-evidence policy ──→ M1–M4 views
F.1 protocol authority ──→ F.2 annotation schema
F.3 message stimuli ──→ F.4 pilot parameters
F.4 pilot parameters ──→ primary comparison (M4 vs M3-ens)
```

> The ground-truth definition and the trace schema are prerequisites for labeling; labeling is a
> prerequisite for any monitor evaluation. Message stimuli and pilot parameters form a separate chain.
> All of them feed into the monitor comparison, but the left-hand items must be settled first.

## Consequences for how results may be described

- Passing an engineering baseline is **not** evidence for any hypothesis. "The pipeline ran" carries
  no scientific content.
- An experimental pilot produces traces for inspection. It does not license a claim about
  communication effects, safety violations, deception, coordination, or monitor performance.
- Only a scientific evaluation — with the labeling procedure and analysis plan settled **before**
  the data are scored — supports such a claim.

## Where each stage currently stands in this repository

| Stage | Status | Evidence |
|---|---|---|
| Engineering baseline | Met | the test suite runs offline; the pipeline records and re-reads runs |
| Experimental pilot | Met | `results/runs_e1/`, `runs/phase-a-live-*/` |
| Scientific evaluation | **Not met** | the analysis was not pre-registered against a settled labeling procedure and analysis plan; see the limitations in the top-level `README.md` and `supplementary/README.md` |

**The reported numbers are therefore at most pilot-stage.** Nothing in this repository should be
described as a validated safety monitor.
