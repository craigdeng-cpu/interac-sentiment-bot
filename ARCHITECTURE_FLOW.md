# Interac Intelligence Bot — Target Architecture

## 1. Overview

### 1.1 Purpose of this document

This document describes how the Interac Intelligence Bot should be built — from **MVP launch** through **longer-term ideal state**. It is written for onboarding and design review.

For how the code works today, see [PROJECT_SYSTEM_OVERVIEW.md](PROJECT_SYSTEM_OVERVIEW.md) and [README.md](README.md). Section 1.4 summarizes what ships at launch vs what comes later.

### 1.2 What the system does

On a fixed schedule, the service:

1. **Collects** public mentions of Interac e-Transfer and competing Canadian payment products from Reddit, news sites, forums, and X/Twitter — using keyword search and APIs, not AI-driven web browsing.
2. **Filters** low-value noise with an LLM scoring pass so only useful signal reaches analysis.
3. **Analyzes** the remaining material with focused AI calls — two parallel tracks for biweekly (chatter + market), or a map-reduce path for quarterly trends.
4. **Stores** scan outputs and source records (MVP: file-based; ideal: full database — see §1.4).
5. **Delivers** a two-column HTML email digest with footer links to underlying data.

Longer-term capabilities (ideal state — see §1.4): Microsoft Teams follow-up Q&A, automated evals, full data storage, and ingestion of Interac proprietary market studies alongside public web scanning.

### 1.3 Core building blocks

The system has five layers. Each layer has one job; together they form a repeatable intelligence pipeline.

| Layer | Role | MVP | Ideal |
|---|---|---|---|
| **Scheduler** | Runs biweekly and quarterly scans on a calendar | Job runner on container host | Independent of any chat platform |
| **Collector** | Fetches and classifies raw mentions | Reddit API, DuckDuckGo, optional X API | + Interac proprietary studies (§1.4) |
| **Agent / LLM** | Scores, filters, and writes the report | Moonshot Kimi + `prompts/` | Same; eval-gated prompt deploys (§3.5) |
| **Data store** | Persists scans, sources, reports | `state/` files + optional S3 exports | Postgres + object storage (§4.1) |
| **Delivery** | Gets the report to people | HTML email (SMTP/Resend) | + Microsoft Teams bot (§3.4) |

```mermaid
flowchart TB
    subgraph People["Stakeholders"]
        EMAIL_USER["Email recipients"]
        TEAMS_USER["Teams users<br/>(channel + 1:1 chat)"]
    end

    subgraph App["Application runtime"]
        SCHED["Scheduler<br/>biweekly + quarterly"]
        PIPE["Intelligence pipeline<br/>collect → filter → analyze"]
        STORE["Data store<br/>DB + object storage"]
        EVAL["Eval runner<br/>(planned)"]
    end

    subgraph Config["Configuration"]
        PROMPTS["prompts.json + prompts/*.md"]
        SECRETS["Env vars / key vault"]
    end

    subgraph External["External APIs"]
        WEB["Reddit · DuckDuckGo · X/Twitter"]
        KIMI["Moonshot Kimi"]
        MAIL["SMTP / Resend"]
        TEAMS_API["Microsoft Teams / Bot Framework"]
    end

    SCHED --> PIPE
    PIPE --> WEB
    PIPE --> KIMI
    PIPE --> STORE
    PIPE --> MAIL
    PIPE --> TEAMS_API
    STORE --> EVAL
    PROMPTS --> PIPE
    SECRETS --> App
    STORE --> PIPE
    MAIL --> EMAIL_USER
    TEAMS_API --> TEAMS_USER
    TEAMS_USER --> TEAMS_API
    TEAMS_API --> PIPE
```

The diagram above reflects the **ideal state**. At MVP launch, Teams, the eval runner, and the full database layer are out of scope — see §1.4.

### 1.4 MVP vs ideal state

This project ships in two phases. **MVP** is the minimum needed to deliver reliable biweekly and quarterly intelligence digests from public web signal. **Ideal state** adds conversational access, quality measurement, durable infrastructure, and internal data sources.

#### MVP (launch)

Everything required to run the core pipeline and email stakeholders:

| Capability | What it includes |
|---|---|
| **Public web collection** | Reddit API, DuckDuckGo (web/news/X), optional X API — keyword queries from `prompts.json` (§3.1) |
| **LLM filter + analysis** | Kimi value scoring, biweekly dual-column reports, quarterly map-reduce trends (§3.2, §3.3) |
| **Scheduling** | Automated biweekly (14-day guard) and quarterly (calendar) scan triggers |
| **Email delivery** | HTML two-column digest via SMTP or Resend; footer with scan metadata and download links to exported workbooks |
| **Launch-grade persistence** | Scan memory, source ledgers, and mention-pool exports on disk (e.g. `state/` + optional S3 upload) — enough to dedupe URLs, track trends, and audit a run, but not a full application database |
| **Configuration** | `prompts.json`, versioned prompt files, environment-based secrets |

MVP delivery is **email only**. Operators may use existing dev tooling (e.g. the Telegram bot in the current codebase) to trigger manual scans during rollout; that is not the stakeholder-facing product.

#### Ideal state (longer term)

Capabilities deferred past launch:

| Capability | What it adds |
|---|---|
| **Full data storage** | Postgres (or equivalent) as system of record — scans, sources, conversations, eval results; object storage for exports; survives redeploys and supports multiple instances (§4.1) |
| **Microsoft Teams bot** | Channel notifications, Adaptive Cards, and conversational follow-up Q&A over the latest report (§3.4) |
| **Evals** | Automated measurement of retrieval recall, filter quality, analysis faithfulness, and end-to-end usefulness; regression alerts and footer quality summaries (§3.5) |
| **Proprietary data ingestion** | Load Interac private market studies, internal research, and other non-public material into the analysis pool alongside public web mentions — with access controls, separate source tagging, and prompts that distinguish public chatter from internal evidence |

**Proprietary data (ideal).** Internal PDFs, slide decks, and market-study extracts would be ingested into a dedicated bucket (e.g. `internal_research`), tagged `source_type: proprietary`, and never mixed into public-facing bullets without explicit labeling. Kimi analysis prompts would gain a third input track or inline section for "Internal context" where product team material informs market-pulse interpretation without being quoted as public sentiment. Access to raw proprietary files stays inside Interac's network; only derived bullets approved for distribution appear in email.

#### Summary

```
MVP launch                          Ideal state (later)
─────────────────────────────       ─────────────────────────────────────
Public web scan                     + Interac proprietary studies
Kimi filter + analyze               (same)
Biweekly + quarterly reports        (same)
Email delivery                      + Teams notifications + Q&A
File/Excel persistence              → Full DB + object storage
Manual operator triggers OK         + Automated evals + quality dashboard
```

The repository today is closest to **MVP**, with a Telegram-based operator interface and partial persistence. See [PROJECT_SYSTEM_OVERVIEW.md](PROJECT_SYSTEM_OVERVIEW.md) for current implementation details.

---

## 2. Technology

### 2.1 Stack overview

| Area | Technology |
|---|---|
| Language | Python 3.12 |
| Runtime | Container host (Railway, Azure, etc.) — `app.py` monolith today |
| Web collection | Reddit JSON API, DuckDuckGo (`ddgs`), optional twitterapi.io |
| LLM | Moonshot Kimi (`kimi-k2.5-preview` or later) via OpenAI-compatible chat API |
| Email | SMTP or Resend — table-based HTML, inline CSS |
| Conversational UI (ideal) | Microsoft Teams via Bot Framework + `manifest.json` |
| Persistence | MVP: `state/` files + optional S3 · Ideal: Postgres + object storage |
| Config | `prompts.json`, `prompts/*.md`, environment variables / key vault |

### 2.2 Kimi vs other AI models

**Kimi** is the large language model from **Moonshot AI** (Chinese AI lab). This project calls it through Moonshot's OpenAI-compatible API (`/v1/chat/completions`), the same interface shape used by GPT-4 and many other providers.

**Why Kimi for this service:**

| Consideration | Kimi (Moonshot) | Typical alternatives (GPT-4o, Claude, Gemini) |
|---|---|---|
| **Role in this system** | Value scoring, report writing, quarterly synthesis, Teams Q&A | Could fill the same roles technically |
| **Long context** | Strong context windows — useful for quarterly map-reduce and large mention pools | Comparable on flagship tiers; costs vary |
| **Cost / throughput** | Chosen for this project's budget and batch-scan workload | Often higher per-token on premium tiers |
| **API compatibility** | Drop-in chat-completions format — minimal integration code | Native SDKs differ; swapping requires prompt retuning |
| **Factual grounding** | Instructed to cite URLs from provided text only; evals (§3.5) measure adherence | All models hallucinate without retrieval + eval guardrails |

**Important distinction:** Kimi does **not** discover posts. Keyword search and APIs collect mentions; Kimi only **scores**, **filters**, **summarizes**, and **answers questions** over text the pipeline already fetched. Collection is deterministic; AI steps are post-collection.

All Kimi calls should go through one shared gateway (e.g. `call_kimi()` in the current codebase) so timeouts, retries, token limits, and logging stay consistent. Model version is pinned via `KIMI_MODEL` env var so evals can compare runs across prompt and model changes.

### 2.3 LLM agent touchpoints

Kimi is invoked at several points — different **jobs**, same API:

| When | What it does | Prompt |
|---|---|---|
| After collection | Score mentions 1–5; drop noise | Inline value-filter prompt |
| Biweekly analysis | Write chatter column | `etransfer_chatter_prompt.md` |
| Biweekly analysis | Write market column | `market_pulse_prompt.md` |
| Quarterly (if needed) | Compress chunks, then write trends report | Inline compress + `quarterly_market_trends_prompt.md` |
| Teams follow-up (ideal) | Answer user questions | `followup_prompt.md` |

### 2.4 Deployment

```
┌─────────────────────────────────────────────────────┐
│  Container host (Railway, Azure, etc.)              │
│  ┌───────────────┐  ┌──────────────────────────┐  │
│  │  app.py       │  │  Scheduler (daily check)  │  │
│  │  pipeline +   │  │  biweekly guard · quarterly│  │
│  │  Teams bot    │  └──────────────────────────┘  │
│  └───────┬───────┘                                  │
└──────────┼──────────────────────────────────────────┘
           │
     ┌─────┴─────┬─────────────┬──────────────┬─────────┐
     ▼           ▼             ▼              ▼         ▼
  Postgres   Object storage   Kimi API    Teams / Email  Eval store
  (scans,    (exports,        (Moonshot)  (delivery +    (metrics,
   sources)    footer URLs)               follow-up)     dashboards)
```

Secrets (API keys, Teams app password, email credentials) live in environment variables or a managed vault — never in the repo.

---

## 3. Workflows and Functionality

### 3.1 How posts are collected (not AI)

Posts are found by **keyword search and public APIs**, not by an AI agent browsing the web.

| Source | Mechanism |
|---|---|
| Reddit | JSON API — subreddit search + `/new` feed browse on configured keywords |
| DuckDuckGo | Text, news, and X/Twitter searches from `etransfer_queries` and `competitor_queries` in `prompts.json` |
| X/Twitter | twitterapi.io or DDG X search when configured |

Each result is de-duplicated by URL, filtered by recency and heuristics, then classified into three buckets:

- **e-Transfer Community** — Reddit, X, forums — personal e-Transfer experiences.
- **e-Transfer News** — press and news articles.
- **Competitor Intelligence** — PayPal, Wise, KOHO, Wealthsimple, Revolut, etc.

Only **after** this deterministic collection does Kimi score mentions for insight value (see §3.2, step 2).

### 3.2 Biweekly workflow

Runs every two weeks. Produces the two-column intelligence digest stakeholders receive by email.

```mermaid
flowchart LR
    A["1. Collect<br/>search + classify"] --> B["2. Filter<br/>LLM value scores"]
    B --> C["3. Analyze<br/>two parallel Kimi calls"]
    C --> D["4. Store<br/>report + every source URL"]
    D --> E["5. Deliver<br/>email (+ Teams ideal)"]
```

**Step 1 — Collect.** Queries from `prompts.json` run across Reddit, DuckDuckGo, and optional X API. Results land in the three buckets above.

**Step 2 — Filter.** Kimi scores each mention 1–5; items below threshold are dropped. Diversity floors ensure the pool is not all one platform or empty on competitor news.

**Step 3 — Analyze.** Filtered text splits into two tracks, analyzed in parallel:

| Track | Input | Prompt | Output |
|---|---|---|---|
| Chatter | e-Transfer Community only | `etransfer_chatter_prompt.md` | Left column — pain points, quotes, fraud/hold stories |
| Market Pulse | News + Competitor | `market_pulse_prompt.md` | Right column — launches, pricing, competitive moves |

Output merges into one report: scan date, both columns, and **Trend vs Last Scan** (themes vs previous run in the data store).

**Step 4 — Store.** Scan record, per-URL source rows, sent-URL history, and optional export files (see §4.1).

**Step 5 — Deliver.** HTML email (two columns). Teams channel post is ideal state (§1.4).

### 3.3 Quarterly workflow

Complements biweekly with a **90-day trends narrative**. Fires Nov 1, Feb 1, May 1, Aug 1, or on operator request.

```mermaid
flowchart LR
    A["1. Collect<br/>~90-day window"] --> B["2. Filter<br/>LLM value scores"]
    B --> C["3. Analyze<br/>map-reduce → trends report"]
    C --> D["4. Store<br/>report + sources + digest"]
    D --> E["5. Deliver<br/>email (+ Teams ideal)"]
```

**Differences from biweekly:**

| Setting | Biweekly | Quarterly |
|---|---|---|
| Lookback | ~30–120 days (configurable) | ~90 days |
| URL dedupe | Skips previously sent URLs | No dedupe — full window needed for trends |
| Volume caps | Tighter | Higher |
| Output | Two-column digest | Single long-form narrative |

**Analyze step:** If the filtered pool fits one API context window, one Kimi call writes the report. Otherwise **map-reduce**: compress ~3k-char chunks in parallel → merge evidence digest → final Kimi call with `quarterly_market_trends_prompt.md`.

Quarterly does not replace biweekly — it zooms out while biweekly catches fresh signal.

### 3.4 Microsoft Teams integration (ideal state)

Teams is the planned conversational interface: scan notifications and follow-up Q&A. Not in MVP — email is the launch delivery channel (§1.4).

**Components:**

| Piece | Role |
|---|---|
| `manifest.json` | Registers the bot with Microsoft 365 — name, icon, scopes |
| Bot Framework endpoint | HTTPS webhook; receives messages from Teams |
| Azure Bot registration | App ID + secret connecting Teams client to backend |
| Adaptive Cards | Rich channel posts — scan date, summary, action buttons |

**On scan complete:** report saved → email sent → Adaptive Card posted to e.g. `#interac-intelligence` with **View full report** and **Ask a question** actions.

**Follow-up conversation:**

```mermaid
sequenceDiagram
    participant User as Teams user
    participant Teams as Microsoft Teams
    participant Bot as Bot Framework endpoint
    participant Store as Data store
    participant Kimi as Kimi API

    User->>Teams: "Why did fraud mentions spike this scan?"
    Teams->>Bot: Message activity
    Bot->>Store: Load latest scan + source pool
    Bot->>Kimi: followup_prompt + report + relevant sources
    Kimi-->>Bot: Answer
    Bot-->>Teams: Reply in thread
    Teams-->>User: Answer + optional source links
    Bot->>Store: Log question, answer, scan_id
```

Users ask in natural language. The model sees the latest report, a sample of stored source mentions, and the question (`followup_prompt.md`). Rate limits and conversation history are persisted in the data store.

### 3.5 Evals (ideal state)

No eval system exists in the codebase today. Ideal state includes **automated evaluations** to measure whether the pipeline is retrieving the right material, filtering wisely, and writing accurate reports. Deferred past MVP (§1.4).

**Goals:**

| Stage | What to measure | Example question |
|---|---|---|
| **Retrieval** | Did we find posts we should have found? | "Was this known Reddit thread in the mention pool?" |
| **Filtering** | Did we keep signal and drop noise? | "Should this mention have scored ≥3?" |
| **Analysis** | Are bullets faithful to sources? | "Does this quote appear in the linked URL's text?" |
| **End-to-end** | Is the digest useful to stakeholders? | "Does this bullet match human reviewer judgment?" |

**Assumed design:**

```
┌─────────────────────────────────────────────────────────┐
│  Eval suite (runs after each scan or on a schedule)     │
│                                                         │
│  1. Golden set     — curated URLs + expected labels     │
│  2. Auto-checks    — URL resolvable, quote ⊆ snippet    │
│  3. LLM-as-judge   — rubric-scored faithfulness (Kimi   │
│                       or second model for cross-check)  │
│  4. Human review   — periodic sample audit in dashboard │
└─────────────────────────────────────────────────────────┘
         │
         ▼
   eval_results table  →  dashboard / alerts on regression
```

**Golden set (retrieval).** Maintain ~50–100 labeled examples: URLs or query patterns with tags (`should_find`, `should_ignore`, `chatter`, `market`, `competitor`). After each scan, check recall: what fraction of `should_find` URLs appeared in the raw pool? Track recall over time per platform.

**Filter evals.** Sample mentions Kimi dropped vs kept. Human or LLM-as-judge labels a batch monthly; compare to Kimi scores. Metrics: precision/recall at threshold 3, false-negative rate on high-signal posts.

**Analysis faithfulness.** For each report bullet that cites a URL:

- **Structural:** URL present, domain matches platform badge, date not invented.
- **Quote check:** Extract quoted text; verify substring match against stored snippet or fetched page text.
- **LLM-as-judge:** Pass bullet + source snippet to a separate prompt: "Is this bullet supported by the source? (yes/no/partial)." Flag partial/no for human review.

**End-to-end rubric.** Monthly, reviewers score 10 random bullets on 1–5 for relevance, accuracy, and actionability. Store scores in `eval_results`; alert if rolling average drops >0.5 vs prior month.

**Storage (extends §4.1):**

```
eval_runs
  └── id, scan_id, ran_at, suite_version, overall_score

eval_results
  └── eval_run_id, check_type, target_id (source_id or bullet_id),
      passed (bool), score, details_json, reviewer (auto | human)
```

**Operational hooks:**

- Run lightweight auto-checks after every scan (URL validity, bullet-has-source).
- Run full golden-set + LLM-judge suite weekly.
- Block prompt/model deploys if regression exceeds threshold (e.g. faithfulness < 90%).
- Include eval summary line in email footer: `Quality checks: 47/50 passed · last full eval 12 Jun 2026`.

---

## 4. Data Storage and Integrity

### 4.1 Data store

**MVP** uses file-based persistence: `biweekly_memory.json`, `source_ledger.xlsx`, `biweekly_reports.xlsx`, and optional S3 uploads for email footer links. This is sufficient for launch but is lost or fragmented on redeploy without a mounted volume.

**Ideal state** moves all durable data into a proper store so the system survives redeploys, supports multiple instances, and answers "where did this bullet come from?" months later.

```
scans
  └── id, type (biweekly | quarterly), ran_at, report_text, themes,
      prompt_version, model_version

sources
  └── scan_id, url, platform, title, snippet, published_at,
      included_in_chatter, included_in_market,
      chatter_bullet_text, market_bullet_text,
      kimi_value_score

conversations
  └── user_id, scan_id, question, answer, created_at

exports
  └── scan_id, file_url, generated_at

eval_runs / eval_results
  └── see §3.5
```

**Why this matters:**

- **Traceability** — Every email bullet traces to a stored URL and snippet.
- **Follow-up context** — Teams Q&A loads from the DB, not ephemeral runtime state.
- **Trend detection** — "Trend vs Last Scan" compares theme labels across scan records.
- **Integrity** — Evals and footers both read the same source-of-truth tables.

### 4.2 Email footers

Every biweekly and quarterly email ends with a **data footer** so recipients can verify and explore the evidence.

| Footer element | Purpose |
|---|---|
| Scan metadata | Date, report type, run ID |
| Source index link | Browsable list of every URL in this scan, with inclusion flags |
| Ledger download | Excel/CSV export of all sources |
| Mention pool download | Filtered pool sent to Kimi |
| Archive link | Historical scans |
| Methodology note | Platforms searched, prompt version, model version |
| Eval summary (ideal) | Latest quality-check pass rate |

Example (conceptual):

```
─────────────────────────────────────────
Data & sources for this scan (14 Jun 2026, biweekly #47)
  View all sources:      https://data.example.com/scans/47/sources
  Download ledger:       https://storage.example.com/exports/scan-47-ledger.xlsx
  Download mention pool: https://storage.example.com/exports/scan-47-pool.xlsx
  Archive:               https://data.example.com/scans
  Searched: Reddit, DDG (web/news/X) · Prompts v2026-06 · Kimi k2.5
  Quality checks:        47/50 passed (full eval 12 Jun 2026)
─────────────────────────────────────────
```

Body bullets link to source domains where possible; the footer is the **index to everything**.

### 4.3 Object storage and exports

**MVP:** Exports are the primary audit trail — generated after each scan and uploaded to S3-compatible storage when configured.

**Ideal state:** Exports become **artifacts of** the database — generated from DB queries, uploaded with stable URLs in email footers. Spreadsheets supplement the DB; they do not replace it.
