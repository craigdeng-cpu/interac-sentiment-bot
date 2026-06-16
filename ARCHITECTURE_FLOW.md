# Interac Intelligence Bot — Architecture Flow

Visual reference for how the agentic workflow system is wired. For configuration details and command lists, see [PROJECT_SYSTEM_OVERVIEW.md](PROJECT_SYSTEM_OVERVIEW.md) and [README.md](README.md).

---

## 1. System Context

Who talks to what, and where intelligence is produced.

```mermaid
flowchart TB
    subgraph Users["Users & Stakeholders"]
        TG_USER["Telegram users<br/>(commands, follow-up Q&A)"]
        EMAIL_RCPT["Email recipients<br/>(HTML digest)"]
    end

    subgraph Runtime["app.py — Python 3.12 Runtime"]
        BOT["Telegram Application<br/>handlers + job queue"]
        PIPE["Scan pipelines<br/>fetch → filter → analyze → deliver"]
        STATE["State layer<br/>memory + Excel ledgers"]
    end

    subgraph Config["Configuration"]
        ENV["Environment variables<br/>TELEGRAM_TOKEN, KIMI_API_KEY, EMAIL_*"]
        PROMPTS_JSON["prompts.json<br/>queries, source toggles, prompt paths"]
        PROMPT_MD["prompts/*.md<br/>LLM system prompts"]
    end

    subgraph External["External Services"]
        REDDIT["Reddit JSON API"]
        DDG["DuckDuckGo<br/>(web, news, X/Twitter)"]
        TWITTER["twitterapi.io<br/>(optional X search)"]
        KIMI["Moonshot Kimi API<br/>kimi-k2.5-preview"]
        EMAIL["SMTP or Resend"]
        S3["S3-compatible storage<br/>(optional workbook links)"]
    end

    TG_USER <-->|polling or webhook| BOT
    BOT --> PIPE
    PIPE --> STATE
    ENV --> BOT
    PROMPTS_JSON --> PIPE
    PROMPT_MD --> PIPE

    PIPE --> REDDIT
    PIPE --> DDG
    PIPE --> TWITTER
    PIPE --> KIMI
    PIPE --> EMAIL
    PIPE --> S3
    EMAIL --> EMAIL_RCPT
    BOT --> TG_USER
```

---

## 2. Runtime Boot & Triggers

How the process starts and what kicks off a scan.

```mermaid
flowchart LR
    START["main()"] --> INIT["Load env + prompts.json<br/>Register command handlers"]
    INIT --> JOBS["Job queue:<br/>• daily 9am EST biweekly guard<br/>• daily 9am EST quarterly guard"]
    INIT --> MODE{"WEBHOOK_URL set?"}
    MODE -->|yes| WH["run_webhook()"]
    MODE -->|no| POLL["run_polling()"]

    subgraph Triggers["Scan triggers"]
        SCHED_B["scheduled_biweekly_broadcast<br/>(14-day guard)"]
        SCHED_Q["scheduled_quarterly_market_trends<br/>(Nov/Feb/May/Aug 1)"]
        CMD_SCAN["/scan"]
        CMD_EMAIL["/email (admin)"]
        CMD_Q["/quarterly (admin)"]
    end

    JOBS --> SCHED_B
    JOBS --> SCHED_Q
    POLL --> Triggers
    WH --> Triggers

    SCHED_B --> BW["Biweekly pipeline"]
    CMD_SCAN --> BW
    CMD_EMAIL --> BW
    SCHED_Q --> QTR["Quarterly pipeline"]
    CMD_Q --> QTR
```

| Trigger | Entry function | Delivery |
|---|---|---|
| Scheduled biweekly (daily check, runs if ≥14 days) | `scheduled_biweekly_broadcast` | Telegram subscribers + optional email |
| `/scan` | `run_biweekly_scan` | Requesting Telegram chat |
| `/email` | `cmd_email` | Email (+ Telegram if configured) |
| Scheduled quarterly | `scheduled_quarterly_market_trends` | Email |
| `/quarterly` | `cmd_quarterly` | Requesting Telegram chat |

---

## 3. Biweekly Agentic Pipeline (Primary Workflow)

End-to-end flow from public web to two-column intelligence digest. This is the core agentic loop.

```mermaid
flowchart TB
    subgraph Collect["1. COLLECT — fetch_biweekly_mentions()"]
        direction TB
        R["Reddit API<br/>subreddit search + /new browse"]
        D["DuckDuckGo<br/>text + news + X per query"]
        T["twitterapi.io<br/>(when configured)"]
        R --> MERGE
        D --> MERGE
        T --> MERGE
        MERGE["De-dupe + classify<br/>_classify_channel_and_source()"]
        MERGE --> BUCKETS["Three labelled buckets:<br/>• e-TRANSFER COMMUNITY<br/>• e-TRANSFER NEWS<br/>• COMPETITOR INTELLIGENCE"]
    end

    subgraph Gate["2. QUALITY GATE — still inside fetch"]
        direction TB
        REC["Recency filter<br/>MAX_MENTION_AGE_DAYS"]
        VF["Kimi value filter (parallel ×4)<br/>kimi_filter_by_value()<br/>score 1–5, drop &lt; threshold"]
        DIV["Diversity + market floors<br/>ensure Reddit/X/competitor mix"]
        REC --> VF --> DIV
    end

    subgraph Analyze["3. ANALYZE — analyze_biweekly()"]
        direction TB
        SPLIT["_split_mentions_sections()"]
        SPLIT --> LEFT["community_text"]
        SPLIT --> RIGHT["market_text<br/>(news + competitor)"]
        LEFT --> KA["Kimi call A<br/>etransfer_chatter_prompt.md"]
        RIGHT --> KB["Kimi call B<br/>market_pulse_prompt.md"]
        KA --> ASSEMBLE
        KB --> ASSEMBLE
        ASSEMBLE["Assemble report:<br/>SCAN DATE · e-Transfer Chatter ·<br/>Market Pulse · Trend vs Last Scan"]
    end

    subgraph Persist["4. PERSIST"]
        MEM["biweekly_memory.json<br/>themes, last scan, sent_urls dedupe"]
        POOL["biweekly_reports.xlsx<br/>one row per mention in pool"]
        LEDGER["source_ledger.xlsx<br/>per-source inclusion trace"]
    end

    subgraph Deliver["5. DELIVER"]
        TG_OUT["Telegram<br/>send_chunked_message()"]
        EMAIL_OUT["Email<br/>build_email_bodies() → HTML table"]
        S3_OUT["Optional S3 upload<br/>workbook download links in footer"]
    end

    BUCKETS --> Gate
    Gate --> Analyze
    ASSEMBLE --> Persist
    ASSEMBLE --> Deliver
```

### Parallel Kimi calls in the biweekly analyze step

```mermaid
flowchart LR
    RAW["Raw mentions text<br/>(3 sections)"] --> SPLIT["_split_mentions_sections()"]

  SPLIT --> C["community_text<br/>e-TRANSFER COMMUNITY only"]
  SPLIT --> M["market_text<br/>NEWS + COMPETITOR"]

  C --> P1["etransfer_chatter_prompt.md"]
  M --> P2["market_pulse_prompt.md"]

  P1 --> K1["call_kimi() — Track A"]
  P2 --> K2["call_kimi() — Track B"]

  K1 --> RPT["Combined biweekly report"]
  K2 --> RPT
```

Both `call_kimi()` tasks are launched with `asyncio.create_task` and awaited in parallel.

---

## 4. Kimi Agent Touchpoints

Every place the LLM acts as an agent in the system.

```mermaid
flowchart TB
    subgraph FetchAgents["During collection (fetch_biweekly_mentions)"]
        VF1["kimi_filter_by_value<br/>e-Transfer social (regular)"]
        VF2["kimi_filter_by_value<br/>e-Transfer social (DDG Reddit)"]
        VF3["kimi_filter_by_value<br/>e-Transfer press/news"]
        VF4["kimi_filter_by_value<br/>competitor mentions"]
    end

    subgraph AnalyzeAgents["During biweekly analysis"]
        BA["etransfer_chatter_prompt → call_kimi"]
        BB["market_pulse_prompt → call_kimi"]
    end

    subgraph QuarterlyAgents["Quarterly map-reduce"]
        QC["Per-chunk compress → call_kimi<br/>(up to 3 concurrent)"]
        QF["quarterly_market_trends_prompt → call_kimi<br/>(final synthesis)"]
        QC --> QF
    end

    subgraph Interactive["Interactive (on demand)"]
        FU["followup_prompt → call_kimi<br/>ask_followup()"]
    end

    subgraph Dormant["Written but not active in main path"]
        CUR["curate_with_kimi()<br/>curation_prompt.md"]
    end

    KIMI_API["Moonshot Kimi API"]

    FetchAgents --> KIMI_API
    AnalyzeAgents --> KIMI_API
    QuarterlyAgents --> KIMI_API
    Interactive --> KIMI_API
```

| Agent step | Prompt file | Purpose |
|---|---|---|
| Value filter | (inline scoring prompt) | Drop low-signal mentions before analysis |
| Chatter column | `etransfer_chatter_prompt.md` | Reddit/X/forum pain points & quotes |
| Market column | `market_pulse_prompt.md` | Competitor launches, pricing, ecosystem news |
| Quarterly compress | inline system prompt | Map: chunk raw scrape into evidence bullets |
| Quarterly report | `quarterly_market_trends_prompt.md` | Reduce: long-form trends narrative |
| Follow-up Q&A | `followup_prompt.md` | Answer questions against latest report |

---

## 5. Quarterly Pipeline (Map-Reduce)

Runs on a quarterly calendar (Nov 1, Feb 1, May 1, Aug 1) or via `/quarterly`.

```mermaid
flowchart TB
    FQ["fetch_biweekly_mentions(quarterly=True)<br/>~90-day window, no sent_urls dedupe"]
    FQ --> FIT{"Raw text fits<br/>single Kimi context?"}

    FIT -->|yes| SINGLE["Single call_kimi()<br/>quarterly_market_trends_prompt.md"]
    FIT -->|no| MAP["Split into ~3000-char chunks"]
    MAP --> COMPRESS["Parallel compress calls<br/>_build_quarterly_evidence_digest()<br/>(semaphore: 3)"]
    COMPRESS --> DIGEST["Merged evidence digest"]
    DIGEST --> REDUCE["Final call_kimi()<br/>quarterly_market_trends_prompt.md"]
    REDUCE --> OUT

    SINGLE --> OUT["Quarterly report + source_ledger rows"]
    OUT --> EMAIL_Q["Email delivery<br/>(quarterly trigger)"]
```

---

## 6. Interactive Follow-Up Flow

Plain-text Telegram messages (not commands) trigger a separate agent path.

```mermaid
sequenceDiagram
    participant User as Telegram user
    participant Bot as handle_message()
    participant Mem as In-memory state
    participant Kimi as call_kimi()
    participant API as Moonshot API

    User->>Bot: Plain text question
    Bot->>Bot: check_rate_limit(user_id)
    Bot->>Mem: Read last_report + last_mentions_raw
    Bot->>Kimi: ask_followup(question, report)
    Note over Kimi: followup_prompt.md +<br/>report excerpt + raw mentions (3k chars)
    Kimi->>API: chat/completions
    API-->>Kimi: Answer
    Kimi-->>Bot: Response text
    Bot-->>User: Markdown reply + daily limit note
```

Requires a prior successful `/scan`, scheduled run, or `/email` so `last_report` is populated.

---

## 7. State & Persistence

```mermaid
flowchart LR
    subgraph Ephemeral["In-memory (lost on redeploy)"]
        SUB["subscribed_chats"]
        LR["last_report"]
        LMR["last_mentions_raw"]
        RL["per-user rate limits"]
        ED["email dedup / cooldown"]
    end

    subgraph Disk["STATE_DIR (persisted)"]
        MEM["biweekly_memory.json<br/>last_scan_date, themes, sent_urls"]
        BWX["biweekly_reports.xlsx<br/>rolling mention pool log"]
        SLX["source_ledger.xlsx<br/>per-source traceability"]
        QMEM["quarterly memory<br/>(if quarterly ran)"]
    end

    SCAN["Biweekly / quarterly run"] --> Ephemeral
    SCAN --> Disk
```

---

## 8. Email Rendering Path

How the biweekly report becomes the HTML digest.

```mermaid
flowchart LR
    REPORT["Plain-text report core"] --> BUILD["build_email_bodies()"]
    BUILD --> HTML["_build_biweekly_html()<br/>1200px table layout"]
    HTML --> SEND{"EMAIL_PROVIDER?"}
    SEND -->|smtp| SMTP["smtplib"]
    SEND -->|resend| RESEND["Resend API"]
    BUILD --> S3U["Optional: _upload_workbooks_for_email_links()"]
    S3U --> FOOT["Footer download links<br/>biweekly_reports + source_ledger"]
```

Left column = e-Transfer Chatter (pain points). Right column = Payments Landscape / Market Pulse.

---

## 9. Component Map

| Layer | Key module / file | Responsibility |
|---|---|---|
| Entry | `main()` in `app.py` | Boot Telegram app, register handlers & jobs |
| Config | `prompts.json`, env vars | Search queries, prompt paths, API keys |
| Collection | `fetch_biweekly_mentions()` | Reddit + DDG + Twitter, classify, filter |
| LLM gateway | `call_kimi()` | All Moonshot API calls (shared HTTP client) |
| Value filter | `kimi_filter_by_value()` | Pre-analysis mention scoring |
| Biweekly analysis | `analyze_biweekly()` | Dual-track parallel synthesis |
| Quarterly analysis | `analyze_quarterly()` | Map-reduce for long context |
| Delivery | `send_email()`, `send_chunked_message()` | Email HTML + Telegram chunks |
| Scheduling | `scheduled_biweekly_broadcast`, `scheduled_quarterly_market_trends` | Autonomous cadence |
| Audit trail | `_append_source_ledger()`, `_append_biweekly_pool_excel()` | Excel evidence logs |

---

## 10. Deployment Topology

```mermaid
flowchart TB
    DEV["Local dev<br/>python app.py + polling"]
    RAIL["Railway / Docker<br/>Procfile: python app.py"]
    VOL["Optional STATE_DIR volume<br/>/data on Railway"]

    RAIL --> VOL
    RAIL --> ENV_R["Railway env vars"]
    ENV_R --> APP["Container runs app.py"]
    APP --> TG["Telegram Bot API"]
    APP --> KIMI["Moonshot API"]
    APP --> RESEND["Resend / SMTP"]
```

---

*Generated to complement the prose docs. Diagrams reflect `app.py` as of the current repository state.*
