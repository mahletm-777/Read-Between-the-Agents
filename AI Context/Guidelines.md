In this 3-day research sprint, you will design and run experiments on collusion between AI agents: when it emerges in markets and everyday agent deployments, how an auditor can tell it from coincidence, how coordination is carried, and what a defender can do to break it. Co-organized by Apart Research, Poseidon Research and AE Studio, with additional support from Modal and The AI Alignment Foundation, the sprint runs online with in-person hubs hosted by Collider in New York City and AI Safety Hong Kong in Hong Kong. No prior background in AI safety is required.


## What this Sprint is about
Collusion between AI agents is a failure mode in high-stakes environments, markets and economics first among them, where misaligned coordination could cause substantial harm. Previous experiments have already produced it: models forming prices that violate economic expectations on competitiveness, agents independently engaging in anti-competitive practices, and over half of tested models showing a coalition advantage when given the opportunity to speak privately in a realistic workplace task. In an era where agent communication and collusion are on the rise, as recently exemplified in the OpenAI/Hugging Face incident, the kind of work done at this sprint is directly relevant to understanding and preventing that behaviour.

This sprint asks three questions. When does collusion emerge, and which levers cause it? What should count as convincing evidence that agents are colluding, with evidence drawn from logged agent trajectories? And how is the coordination needed for collusion mediated, and how can it be broken, ideally before it happens or otherwise while it is occurring?

## What participants will do
Over three days you will pick one open question about collusion between AI agents, build the controlled experiment that answers it, and write up what you found.

The format. Teams of one to five people pick a track, come up with a project, and ship. There is no requirement to have participated before, no requirement to have a team in advance and no specific background needed.

Starting points. Every research question below is tied to existing papers and open-source code. The Resources tab has a practical start for each one: starter code, a weekend-sized project, and a rough cost estimate. All default routes use a normal 16 GB laptop and no GPU.

## Research Tracks
Pick one track to anchor your project. Each research question below is developed in full on the Resources tab, with the papers it builds on and a practical start (starter code, weekend project, budget).


### Detection and Audit of Collusion
This track is about definitions, detection and evidentiary standards in general, with evidence drawn from logged agent trajectories. Markets could still be relevant settings, but we aim at being more general, such as in more commonplace workflows. This track is important for understanding collusion in general. Can we say things about what causes collusion in general?

Research questions:

2.1. Which kinds of tasks lead to collusion?
**Which kinds of tasks lead to collusion? Some examples from previous work that generated collusion:**
Can you think of new tasks/environments/pressures that lead to collusive behavior outside of the market structure?
Models forming prices that violate economic expectations on competitiveness (Fish et al. 2024).
Agents can independently engage in anti-competitive practices (Lin et al. 2024).
In a setting called Colosseum, over half of tested models showed "coalition advantage" when given the opportunity to speak privately in a realistic workplace task (Nakamura et al. 2026).

2.2. Does combining what agents say with what they do beat either alone?
**Does combining what agents say with what they do beat either alone? Conversations and actions are known to disagree**. One way to measure this is via action-level coalition advantage, which has a weak but non-trivial anti-correlation in some collusion settings against LLM-judge conversation scores (Nakamura et al. 2026). Other related results include:
What are other "hybrid techniques" that look for inconsistencies between words and actions, or within their internal attributes like verbosity or action structure, and can we set up examples that show whether we can detect cross-agent consistency, e.g. whether A's behavior is consistent with B's stated plans or has deviated.
In various repeated n-player games, more than 90% of deviations from public announcements were already stated in models' private plans (Shi et al. 2026).
Work in the sabotage setting has set up monitors that look for multiple agents coordinating independently on what they say and do (Radev et al. 2026).
Analyzing an agent's actions from multiple dimensions can improve a single-dimension monitor from 0% to >70% (Nguyen et al. 2026).
Less can be more: sometimes giving a monitor too much detail degrades its performance (Arike et al. 2026).



2.3. What should count as convincing evidence of collusion?

**What should count as convincing evidence of collusion?** Most of the literature cited earlier has documented limitations or failure modes in their respective limitations sections. Antitrust law met the problem of collusion detection with noisy evidence a century ago and answered with the plus-factors doctrine, under which parallel conduct alone is lawful and inferring agreement needs additional evidence (Berger et al. 2025). The ML literature is largely re-deriving this without engaging that doctrine or similar principles by name. Which signals, alone or combined, actually separate deliberate coalitions from innocent correlation, talk without action, and harm without agreement? Is there previous work we can leverage from adjacent literature, like anti-trust law, and how would we operationalize that to detect collusion between AI agents?
This track is about definitions, detection and evidentiary standards in general, with evidence drawn from logged agent trajectories. Markets could still be relevant settings, but we aim at being more general, such as in more commonplace workflows. This track is important for understanding collusion in general. Can we say things about what causes collusion in general?

## How to get started
Practical weekend projects, starter code and resource estimates for the research questions on the Overview tab.

Planning assumptions. Repository and paper links were checked on 31 August 2026. Start with 3 to 5 seeds per condition and expand to 10 to 20 only after the positive control works. Estimates assume short outputs, retries and low-cost API models; they exclude participant time and coding-assistant subscriptions. All default routes use a normal 16 GB laptop and no GPU.

### Does combining what agents say with what they do improve detection? (2.2)
Starter code: LLM-Trust-Breaking; paper; TraceGuard. Shi et al.'s official harness records private plans, public announcements, final actions and outcomes across several social dilemmas, and supports OpenAI/OpenRouter and heterogeneous groups. TraceGuard is a single-agent monitoring system, not a drop-in collusion detector, but its action-trace consistency rubric is reusable.

Weekend project: Generate a small balanced set from one game, then give the same monitor four views: messages only; actions only; raw messages plus actions; and a structured report comparing claims with outcomes. Freeze the monitor model, prompt and threshold before evaluation, and report both accuracy and false-positive rate.

Resources: Low setup risk. About 20 to 40 short trajectories and 160 to 250 monitor calls are enough for a pilot. Use one agent API and one inexpensive monitoring model, a 16 GB laptop and no GPU. Budget about $5 to $30, or $30 to $75 if stronger agent models generate the logs.

# APART Sprint Judging Criteria
## Dimension 1: Impact Potential & Innovation
How much would this matter for the field if it worked? How innovative is it?
For scores of 4-5: is this actually new to the field, or replicating recent work?

Score	Description
1	Negligible. No clear problem addressed, or no meaningful novelty.
2	Limited. Addresses a real problem but with a generic or well-trodden approach. Incremental at best.
3	Moderate. Clear problem with a reasonable approach; some novelty in framing or method beyond routine application of existing tools.
4	Significant. Important problem with an original approach, or identifies a neglected problem area. A valuable contribution others could build on.
5	Exceptional. Tackles a critical problem with a genuinely novel approach, or opens a new research direction. Clear theory of change. You'd be excited to share this with researchers in the area.

## Dimension 2: Execution Quality
How sound are methodology, implementation, and findings?

Score	Description
1	Seriously flawed. Methodology broken, results uninterpretable, or implementation doesn't work.
2	Weak. Approach has significant gaps: missing validation, flawed experimental design, or incomplete implementation.
3	Competent. Technically solid given the short duration. Methodology makes sense, results are interpretable, limitations acknowledged, work builds toward clear conclusions.
4	Strong. Thorough methodology with convincing validation. Results clearly support conclusions. Immediately useful for future work.
5	Exceptional. Ambitious scope executed rigorously. Surprising findings, novel methods, or unusually robust validation.
## Dimension 3: Presentation & Clarity
How clearly are work, findings, and impact potential communicated?

Score	Description
1	Incomprehensible. Cannot determine what the project is actually claiming or doing.
2	Hard to follow. Key information buried, missing, or diluted by excessive length. Significant effort to extract main points.
3	Clear enough. Can understand the problem, approach, and results without undue effort. Core content clearly present: problem, method, findings, limitations.
4	Well presented. Easy to follow, well-structured, appropriate level of detail. Target audience would get it quickly.
5	Exceptionally clear. A pleasure to read. Complex ideas made accessible. Could serve as a model for how to present this type of work.
# Submission Requirements
Required:

Research report (PDF) using the official template.
Project title and abstract, 150 words or fewer.
Author names and affiliations.
A "Limitations and Dual-Use / Ethical Considerations" appendix (required, see below).
Optional:

Public GitHub repo.
A 3 to 5 minute video demo.

## Submission Template:
[PROJECT TITLE




Author name 1
Affiliation
Author name 2
Affiliation
Author name 3
Affiliation
Author name 4
Affiliation
Author name 5
Affiliation
Author name 6
Affiliation

With
Apart Research

Abstract
Summarize your project in 150–250 words. A strong abstract lets a reviewer understand what you did and why it matters without reading anything else. Make sure to cover: the problem, your approach, key results, and the main takeaway. Polish it last: the abstract should reflect your final results, not your initial plan.





How to use this template: Replace the italicized guidance text under each section with your content. The section structure is strong guidance but not rigid. If your project requires a different organization, feel free to adapt. Delete all guidance text including this info box before submitting. Make sure the project title and author information above are up to date.
How your submission will be evaluated: Your project will be judged on the quality of this written report. To understand what we look for, see our Evaluation Rubric.
Recommended length: 4 pages excluding references and appendix. 
Rough guide: Intro & Related Work 1p, Methods and Results: 2.5p, Discussion 0.5p.

1. Introduction
What problem are you addressing and why does it matter? 
Connect it to your work: we want to know why your work is practically valuable. 

Provide enough background for readers to understand your work. 
If relevant, briefly describe the threat model or failure mode you're addressing; reference prior work that motivates why it is worth addressing, or explain it yourself. 

Aspire to clearly list your most important contributions that go beyond what exists today.
Our main contributions are:

[First contribution — what new thing did you create, discover, or demonstrate?]
[Second contribution]
[Third contribution, if applicable]

2. Related Work
What prior work is most similar, and how does your work differ? 
Cite the most relevant papers, tools, or projects. Explain what gap your work addresses.

Some questions which may help:
When and why would someone use your method over the existing state-of-the-art?
What information/insight does your method provide which we did not have before?

3. Methods
Describe your approach clearly enough that someone could replicate it. Include key design choices and justify them where relevant (hint: the more you can back up your design choices by referencing prior work, the better).

What models/datasets/tools did you use and why? What were key parameters or design decisions? What did you try that didn't work? Could someone reproduce your work from this description?

4. Results
Present your main findings with appropriate evidence. Use figures and tables where appropriate (we strongly encourage at least one figure, see tips below). Distinguish between observations and interpretations. 

Argue why your claims are robust. E.g., if your approach “performs better” than alternatives:
Do you have enough data? Is the difference statistically significant?
Is it robust? Or do small changes to your setup cause substantial changes to your results?

Tips for figures and tables:

Number all figures (Figure 1, Figure 2...) and tables (Table 1, Table 2...)
Include descriptive captions that can be understood without the main text
Place figures/tables near where they're first referenced
Ensure text in figures is legible!

5. Discussion and Limitations
Discuss the broader implications for AI safety. 
What do your results mean? What trends do you notice and what might they indicate?  

Limitations
What are the limitations of your work? What threat models or failure modes did you not address? Be honest about constraints — methodological limitations, scope limitations, or aspects you couldn't fully address in the hackathon timeframe. Explicitly note the assumptions you made, whether implicitly or explicitly, and how the interpretation of your results would change if a given assumption did not hold.

Future Work
What are the natural next steps? How could this work be extended?

6. Conclusion
Briefly summarize your main findings and their implications (1–2 paragraphs).

Code and Data
Include links if applicable. If your project doesn't involve code (e.g., policy analysis) or if there are info-hazard considerations, note that here.

Code repository: [Link to GitHub/GitLab if applicable]
Data/Datasets: [Link if applicable]
Other artifacts (optional): [Demo link, video walkthrough, Hugging Face Space, etc.]

Author Contributions (optional) 
[e.g., "A.B. led the project and designed experiments. C.D. implemented the code. All authors contributed to writing and reviewed the final manuscript."]

References
Use a consistent citation format. Include: Author(s), Year, Title, Venue/Publisher, and URL or DOI where available.

[Reference 1]
[Reference 2]
...

Appendix (optional)
Supplementary material such as additional figures, detailed methodology, prompts used, extended results, etc.
LLM Usage Statement
If you used LLM assistance in developing your project or writing this report, briefly note how. Ensure all claims and results have been verified.

NOTE: We strongly encourage that the final version of the submission is primarily written by your team. 

[e.g., "We used Claude to brainstorm approaches and help draft sections. All results and claims were independently verified."]
]

### Most strong projects are 4 to 8 pages.

Introduction: the question and why it matters.
Related Work: what you build on (see Resources).
Methodology: enough detail to replicate (models, prompts, environments, seeds, metrics).
Results: quantitative where possible; report variance, seeds and baselines, and always include the positive control.
Discussion: implications, limitations, and future work.
Limitations and Dual-Use / Ethical Considerations (required): state what your result does and does not establish, and note any way your setup or findings could make real-world collusion easier to carry out or harder to detect.
References.
AI tools and your report
Use AI tools the way you would use a colleague: to check your reasoning, find gaps in a draft, or debug code. The report itself has to be your team's own writing about your team's own work. Judges read every submission, and a report that reads as generated rather than written (generic framing, padded sections, claims without sources, no trace of what you actually did) will not be scored. Keep it short, say what you did in your own words, and link the sources for every factual claim.