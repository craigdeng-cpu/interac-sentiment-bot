# Kimi K2.5 — Prompt Engineering Research & Prompt Reasoning

## What Is Kimi K2.5

Kimi K2.5 is an open-source multimodal agentic model from Moonshot AI. It is built on the Kimi K2 base — a 1 trillion parameter mixture-of-experts (MoE) transformer (32B activated) pretrained on ~15 trillion mixed visual and text tokens. It supports a 256,000 token context window and is available via API (moonshotai/Kimi-K2.5).

Pricing: $0.60/1M input tokens ($0.10 cached), $3.00/1M output tokens.

### What Makes It Different

- **Native multimodal**: Vision and language were jointly pretrained from the start, not bolted on. Early fusion at a moderate vision-text ratio throughout training beats aggressive late-stage vision tuning.
- **Agent Swarm**: Can autonomously decompose a task into up to 100 parallel sub-agents running up to 1,500 tool calls concurrently — up to 4.5x faster than single-agent for "wide" tasks. The swarm is self-directed; no predefined subagents needed.
- **Instruction following**: Benchmark performance on SWE-Bench Verified (76.8%), AIME 2025 (96.1%), BrowseComp (74.9% with context management). Among the strongest open-source models for agentic and reasoning tasks.
- **Context coherence**: Maintains coherent tool use across 100+ sequential calls, including interleaved images.

### When to Use Chat vs. Agent Swarm

| Mode | Best For |
|------|----------|
| Chat (single-agent) | Sequential, stateful tasks — coding, report generation, structured analysis |
| Agent Swarm | Wide, parallelizable tasks — deep research, bulk extraction, multi-source synthesis |

For the Interac bot's scheduled report pipeline, **Chat mode is correct** — reports are sequential and stateful (scrape → analyze → format). Swarm would add coordination overhead without benefit.

### Recommended Inference Settings

From Moonshot AI official guidance:
- Temperature: **0.6** (reduces repetition and incoherence)
- min_p: **0.01** (suppresses low-probability tokens)

---

## What Kimi's Own Docs Say About Prompting

Source: https://platform.moonshot.ai/docs/guide/prompt-best-practice

Key guidance directly from Moonshot:

1. **Explicit instructions beat implicit ones.** "The model can't read your mind." If output is too long, say so. If format is wrong, show the format you want.
2. **Outline steps explicitly.** "Writing these steps explicitly makes it easier for the model to follow and produces better output."
3. **Specify output length in structural units** (paragraphs, bullets) — not word counts. Word count targets are imprecise for this model.
4. **Ground answers in provided context.** Providing the model with explicit source material and instructing it to use that material (and only that material) reduces hallucination.
5. **Few-shot examples are more efficient than exhaustive rule lists** when you want a consistent output style.
6. **Classify before instructing.** For multi-case tasks, have the model identify the case type first, then apply case-specific instructions. (Relevant if query routing is added later.)

---

## Research Findings From Third-Party Sources

Source: https://promnest.com/blog/mastering-kimi-k2-how-to-write-expert-prompts-for-ai-agents/

- Kimi K2 (the base) requires **strict data sourcing rules** to mitigate hallucinations. Vague citation permissions ("cite your sources") are insufficient. The prompt should mandate specific citation formats and restrict the model to only sources it received.
- Use **clear section headers** (e.g., `## 1. Grounding Rules`) — structured data is easier for the model's attention mechanism to process accurately.
- Avoid ambiguous commands. Instead of "be accurate," write "distinguish between confirmed data and estimates using specific tags."
- **Avoid "language drifting"** in complex reasoning tasks by specifying output format expectations explicitly and early.

Source: https://www.datacamp.com/tutorial/kimi-k2-agent-swarm-guide

- When given complex multi-part prompts, K2.5 returns "crisp, immediately usable" output — it handles structured analytical prompts well.
- The model will autonomously adjust behavior when instructions conflict with task coherence (e.g., it spun up 25 sub-agents when asked for 20, correctly prioritizing correctness).
- Implication: **rules need to be unambiguous**. The model will interpret ambiguous rules in ways that seem locally correct but may not match intent.

---

## Prompt Reasoning — Why Each Decision Was Made

### Analysis Prompt

**Role framing as a full sentence**
The original opened with a fragment: `"Product intelligence analyst for Interac Corp Money Movement team."` Kimi's docs say to establish a clear identity. A full declarative sentence ("You are a senior product intelligence analyst...") sets role, authority, and purpose in one shot and gives the model a stronger persona to maintain across the output.

**Grounding rules placed at the top, before format**
Models weight early tokens more heavily. If the most important behavioral constraint (only use data you received, never fabricate URLs) is buried after the format template, it competes with the format instructions for attention. Moved to the top as its own section.

**Anti-hallucination rule made explicit**
The original had no instruction about fabricated sources. For a bot that delivers clickable URLs to a PM making product decisions, a hallucinated URL is a silent trust-killer — the PM clicks it, gets a 404, and loses confidence in the whole report. Added: "Only reference sources, URLs, and mentions that exist in the raw data provided to you."

**Sentiment score rubric**
The original had `SENTIMENT SCORE: [0-100]` with no definition of what those numbers mean. Without a rubric, Kimi picks scores that feel right but are inconsistent run-to-run, making week-over-week trend analysis meaningless. Added a five-band rubric with concrete descriptors tied to observable conditions (outage = crisis, balanced signals = mixed, etc.).

**Signal quality definition**
"Prioritize new developments over generic commentary" is ambiguous. What is a development? What disqualifies a signal? Added explicit criteria: a signal qualifies if it represents an outage, policy change, feature launch, competitive move, or credible detailed complaint. Generic opinions without scale or specificity do not qualify. This prevents Kimi from filling the format with weak signals just to hit 3 entries.

**Per-signal format template**
The original listed required fields in a prose rule. A structured template with `>` blockquote format is more effective because it shows Kimi exactly how a signal should look, not just what fields it needs. This is the "few-shot" principle from Moonshot's docs applied at the micro level.

**PRODUCT HEALTH specificity**
Changed "one line" to a concrete good/bad example inline. "Complaint spike around send limits from Wealthsimple users on r/PersonalFinanceCanada — not yet trending" vs "Seems stable." The model needs to see what good looks like to produce it consistently.

**COMPETITIVE WATCH trigger criteria**
"If relevant" is doing too much interpretive work. Added specific triggers: only include if a competitor is framed as superior, a user is described as switching, or a press story directly positions a competitor against Interac. Otherwise omit the section entirely.

**Voice instruction**
Added: "Write like a senior analyst briefing a time-pressed PM on Monday morning. Direct sentences. No hedging." Without this, Kimi defaults to hedged, corporate-sounding language ("it appears that sentiment may be trending slightly negative"). The voice instruction gives the model a concrete persona to inhabit that produces tighter output.

---

### Followup Prompt

**The original was one sentence — why that's dangerous**
`"You are a product intelligence analyst... Be direct and specific."` One sentence gives Kimi no guardrails on: when to say "I don't know," how to handle questions outside the data, whether to fabricate URLs when asked to cite, or how to handle requests for trend comparison when prior data is unavailable. A PM querying this live can ask anything. The model needs behavioral rails for the ambiguous cases.

**Lead-with-the-answer instruction**
Added explicit instruction to answer first, support second. LLMs naturally front-load context before the answer. For a PM follow-up bot, that's backwards — the answer is what matters, context is supplementary.

**Epistemic humility gate**
Added: "If the question cannot be answered from available data, say 'Not enough signal in the current data to answer this reliably.' Do not speculate or extrapolate." Without this, Kimi will generate a plausible-sounding answer for any question, including ones the data can't actually support.

**Prior period comparison handling**
Added explicit instruction: if asked to compare to a prior period, check whether historical data is available and if not, say so explicitly. Prevents the model from interpolating fake historical comparisons.

---

### Historical Prompt

**The original was already the strongest of the three.** It had the right instinct: explicit instructions about not discarding weak signals, the "must produce 1 finding if data exists" rule, and platform attribution. The optimizations were more about tightening what was already there.

**"What makes a good finding" section**
The original just listed required fields. Added a qualitative definition: a good finding distinguishes between recurring (multiple posts/sources) and isolated signals, and names the product, FI, and platform. This prevents the model from producing shallow one-liners that technically satisfy the field requirements but convey nothing actionable.

**Recurring themes formalization**
Added: themes must appear in at least two separate posts or time periods, produce 2–5 themes, and name which timeframes each appears in. Without a minimum recurrence threshold, any single mention can become a "theme." Without a count range, Kimi makes arbitrary choices about how many to surface.

**ACTIONABLE INSIGHT — bad/good example**
This is the highest-leverage change in the historical prompt. Added an explicit bad example ("Interac should improve the user experience") and a good example ("Send limit complaints on Reddit have persisted across all three timeframes and are disproportionately from Wealthsimple users — worth a targeted FI-side investigation"). The model reliably produces output that matches the style of the example when one is provided.

**OVERALL TREND grounding**
Changed from just the label to `[label] — [one sentence explaining why]`. Forces the model to commit to a reason, which makes it more likely to ground the trend call in the actual data rather than picking a label arbitrarily.

---

## Prompt Length — Why This Range

| Prompt | Original (est. tokens) | Optimized (est. tokens) |
|--------|------------------------|-------------------------|
| analysis_prompt | ~200 | ~480 |
| followup_prompt | ~50 | ~220 |
| historical_prompt | ~250 | ~500 |

Moonshot's docs say to be explicit. Third-party research on K2 agents confirms that structured, detailed system prompts with clear headers and section separation produce better-organized outputs. The risk of going too long is instructions near the bottom getting underweighted — but all three optimized prompts stay well below the threshold where that becomes a concern (~1000+ tokens). The sweet spot for structured analytical tasks is 400–600 tokens: enough to be unambiguous, short enough that nothing gets lost.

The followup prompt's jump from 50 to 220 tokens is the most impactful per-token improvement of the three — it went from dangerously underspecified to adequately guarded.

---

## Things Still To Consider

- **`{timestamp}`**: Verify this is injected by the bot code before the string reaches Kimi. If it's printing literally in reports, it needs to be formatted server-side (`datetime.now().strftime(...)`) and interpolated into the prompt string.
- **Few-shot signal examples**: If output quality is still inconsistent after these prompts, the next step is adding 1–2 complete example signal blocks directly in the analysis prompt. Moonshot's docs say few-shot examples are more efficient than rule lists for style consistency.
- **Temperature**: Confirm the bot is using temperature=0.6 as Moonshot recommends. Higher temperatures produce more variable outputs which is bad for a consistently-formatted scheduled report.