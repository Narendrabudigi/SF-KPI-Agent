# 🏗️ SAP SF KPI Agentic AI — Comprehensive Technical Architecture & Flowchart

This document details the end-to-end system architecture, multi-agent orchestration, Databricks ML integration, MCP context memory, GenAI prescriptive reasoning, notification dispatch, and real-time dashboard visualization.

---

## 1. High-Level System Architecture Diagram

```mermaid
graph TB
    subgraph INGESTION ["1. Ingestion & Trigger Layer"]
        SAP["SAP Datasphere / SF OData API"] -->|OAuth2 Bearer Token| ODATA["odata_client.py<br/>(Polls KPI Metrics)"]
        UI_TRIG["Web UI Trigger<br/>(/api/trigger-scan)"] --> ORCH
        ODATA --> ORCH["agents/orchestrator.py<br/>(Master Orchestrator Daemon)"]
    end

    subgraph ORCHESTRATION ["2. Master Orchestrator & Breach Tracking"]
        ORCH --> BT["breach_tracker.py<br/>• Normalizes KPI Aliases<br/>• Extracts Dimensions (Dept / KPI_ID)<br/>• Cooldown & active_breaches.json"]
        ORCH --> MATRIX["config.py<br/>(Interdependency Matrix)"]
        ORCH --> MCP_MEM[("mcp_memory.json<br/>(Shared Context Memory)")]
    end

    subgraph DOMAIN_AGENTS ["3. Domain-Specific ML Agents (Batch Dispatch)"]
        ORCH -->|Domain Routing & Lock| AGENTS{Domain Dispatcher}
        AGENTS -->|"Time-to-Hire"| REC["recruitment_agent.py"]
        AGENTS -->|"Onboarding"| ONB["onboarding_agent.py"]
        AGENTS -->|"Offboarding"| OFB["offboarding_agent.py"]
        AGENTS -->|"Attrition"| EC["employee_central_agent.py"]
        AGENTS -->|"Payroll"| EP["employee_payment_agent.py"]
    end

    subgraph DATABRICKS ["4. Databricks ML / Delta Lake Layer"]
        REC & ONB & OFB & EC & EP -->|"REST API (BREACH_PAYLOAD)"| DB_RUN["databricks_client.py<br/>(Notebook Job Execution)"]
        DB_RUN --> NOTEBOOKS["Databricks ML Notebooks<br/>(01_TTH, 02_ONB, 03_OFB, 04_EAR, 05_PER)"]
        NOTEBOOKS --> DELTA[("Databricks SQL / Delta Lake<br/>Results Tables")]
        DELTA -->|"SQL Client Query"| SQL_CLIENT["databricks_sql_client.py"]
    end

    subgraph PRESCRIPTIVE ["5. GenAI Prescriptive & Anomaly Engine"]
        SQL_CLIENT -->|"Feature Importance & RCA Output"| PRESC["agents/prescriptive_agent.py"]
        MCP_MEM -->|"Raw SAP OData Context"| PRESC
        PRESC -->|"Structured Prompting"| LLM["clients/llm_client.py<br/>(Azure OpenAI GPT-4o)"]
        LLM -->|"JSON Insights (Summary, Actions, Risk)"| PRESC
    end

    subgraph DISPATCH ["6. Multi-Channel Notification Layer"]
        PRESC --> NOTIF["agents/notification_agent.py"]
        NOTIF -->|"Cooldown Check"| NOTIF_CLIENT["clients/notification_client.py"]
        NOTIF_CLIENT --> EMAIL["📧 Rich HTML Email (SMTP)"]
        NOTIF_CLIENT --> SLACK["💬 Slack Webhook Blocks"]
    end

    subgraph PRESENTATION ["7. Web Control Center & Analytics UI"]
        PRESC --> DASH_DATA[("dashboard_data.json<br/>& runtime_state.py")]
        FASTAPI["main.py (FastAPI Backend)"] --> DASH_DATA
        FASTAPI --> DASH["dashboard.html (Live Control Center)"]
        FASTAPI --> TTH_PAGE["timetohire.html"]
        FASTAPI --> ONB_PAGE["onboarding.html"]
        FASTAPI --> EAR_PAGE["attrition.html"]
        FASTAPI --> OFB_PAGE["offboarding.html"]
        FASTAPI --> ALERTS_PAGE["alerts.html"]
    end
```

---

## 2. End-to-End Execution Sequence Diagram

```mermaid
sequenceDiagram
    autonumber
    participant SAP as SAP Datasphere / SF
    participant Orch as Master Orchestrator
    participant Tracker as Breach Tracker
    participant MCP as MCP Memory Server
    participant Domain as Domain Agent (e.g. EC Agent)
    participant DB as Databricks ML Cluster
    participant Delta as Databricks SQL / Delta
    participant Presc as Prescriptive GenAI Agent
    participant LLM as Azure OpenAI (GPT-4o)
    participant Notif as Notification Agent
    participant UI as Web Dashboard

    Note over Orch: Background scan triggers every 24h or manual UI trigger
    Orch->>SAP: fetch_kpi_data() via OAuth2 Bearer Token
    SAP-->>Orch: Returns KPI records (KPI_ID, Dept, Values, Month, Year)
    
    Orch->>Tracker: check_breach() & generate_breach_id()
    Tracker-->>Orch: Active breach list (Critical / Warning)
    
    Orch->>MCP: store_odata_row(incident_id, raw_rows)
    Note over MCP: Saves raw SAP context for downstream GenAI enrichment
    
    Orch->>Domain: execute_domain_agent(BREACH_PAYLOAD JSON)
    Note over Domain: Bundles all departmental/process breaches into 1 batch run
    Domain->>DB: submit_run(notebook_path, parameters={BREACH_PAYLOAD})
    
    loop Poll Job Execution
        Domain->>DB: get_run_status(run_id)
        DB-->>Domain: RUNNING -> TERMINATED (SUCCESS)
    end

    Domain->>Delta: Query Delta Lake Results Table
    Delta-->>Domain: ML RCA Data (Root causes, Delay factors, Analysis Type)

    Domain->>Presc: run_prescriptive_analysis(ML_Results, Domain, KPI)
    Presc->>MCP: read_memory() (Retrieve SAP raw context)
    MCP-->>Presc: Raw employee / dept metadata
    
    Presc->>LLM: Generate Prescriptive Mitigation Strategy (GPT-4o)
    LLM-->>Presc: Structured JSON (Executive Summary, Immediate Actions, Precautions)

    Presc->>Notif: dispatch_notifications(Consolidated Breach Data)
    Notif->>Notif: Check notification cooldown (prevent duplicates)
    par Multi-Channel Broadcast
        Notif->>Email: Send Styled Executive HTML Email
        Notif->>Slack: Send Interactive Slack Message Blocks
    end

    Presc->>UI: Update dashboard_data.json & runtime_state.py
    UI->>UI: Real-Time SSE/Polling renders live charts, logs, and tables
```

---

## 3. Step-by-Step Technical Layer Breakdown

### Layer 1: Ingestion & Trigger System
- **OData OAuth2 Authentication**: [`clients/odata_client.py`](file:///d:/sf_kpi_agents/SAP-SF-KPI-AGENTS/clients/odata_client.py) generates client-credential access tokens from SAP Datasphere and fetches live employee lifecycle KPI data.
- **Autonomous & Manual Triggers**:
  - **Background Daemon**: Runs 24/7 on a customizable interval loop.
  - **FastAPI Endpoints**: `/api/trigger-scan` allows on-demand KPI evaluation directly from the UI.

### Layer 2: Master Orchestrator & Breach Tracking
- **Canonical Normalization**: Maps shorthand aliases (`tth`, `oct`, `ear`, `per`, `oft`) to canonical KPI names.
- **Dimension Resolution**:
  - **Individual Incidents** (`Time-to-Hire`, `Onboarding`, `Offboarding`): Tracked per `KPI_ID` or process identifier.
  - **Departmental KPIs** (`Employee Attrition Rate`): Tracked per `DEPARTMENT_ID` / `Department`.
- **Interdependency Matrix**: Resolves domino effects (e.g. Onboarding delays escalating into high early-stage Attrition).
- **Concurrency Locking**: Domain locks (`DOMAIN_LOCKS`) prevent overlapping Databricks runs for the same domain.

### Layer 3: Model Context Protocol (MCP) Memory
- **State Preservation**: [`agents/mcp_server.py`](file:///d:/sf_kpi_agents/SAP-SF-KPI-AGENTS/agents/mcp_server.py) acts as a high-speed memory server caching raw OData row context (`store_odata_row`).
- **Context Injection**: Injects raw employee attributes, department tags, and manager details into the downstream LLM prompts.

### Layer 4: Databricks ML Root Cause Analysis (RCA)
- **Batch Processing**: Groups all active breaches for a domain into a single `BREACH_PAYLOAD` JSON string:
  ```json
  [
    {
      "DEPARTMENT_ID": "D01",
      "YEAR": "2026",
      "MONTH": "7",
      "Severity": "CRITICAL"
    }
  ]
  ```
- **ML Notebooks**: Executes domain-specific ML notebooks in Databricks (`01_TTH_RCA`, `02_ONB_RCA`, `03_OFB_RCA`, `04_EAR_RCA`, `05_PER_RCA`) calculating anomaly scores, delay factors, and affected record lists.
- **Delta Lake Integration**: [`clients/databricks_sql_client.py`](file:///d:/sf_kpi_agents/SAP-SF-KPI-AGENTS/clients/databricks_sql_client.py) queries the generated Delta Lake tables.

### Layer 5: GenAI Prescriptive Analysis Engine
- **Data Partitioning**: Segregates findings into `ACTUAL_BREACH` (immediate remediation) and `PREDICTIVE_RISK` (early warning alerts).
- **LLM Reasoning**: [`agents/prescriptive_agent.py`](file:///d:/sf_kpi_agents/SAP-SF-KPI-AGENTS/agents/prescriptive_agent.py) calls Azure OpenAI GPT-4o to formulate:
  1. **Executive Summary**: Why it is late, by how much, and downstream impacts.
  2. **Prescriptive Actions**: Concrete action items, responsible role owners, and expected timeline.
  3. **Strategic Phasing**: Immediate containment, medium-term workflows, and long-term policies.
  4. **Risk Assessment & Precautions**: Operational, financial, and legal risk safeguards.

### Layer 6: Multi-Channel Notifications
- **Smart Cooldowns**: Enforces deduplication timers in `breach_tracker.py` so stakeholders are not spammed for persistent breaches.
- **HTML Email**: Dispatches responsive tables, KPI badges, root causes, and direct CTA buttons to relevant detail pages.
- **Slack Blocks**: Sends structured webhook messages with action buttons.

### Layer 7: Interactive Control Center UI
- **FastAPI Layer**: Exposes `/api/system-state`, `/api/results`, `/api/alerts`, `/api/trigger-scan`, and `/api/reset`.
- **Live Terminal & Swarm Status**: Visualizes active background agent states, memory footprint, and high-fidelity streaming logs.
- **KPI Deep-Dive Pages**: Specialized analysis views for [Time-to-Hire](file:///d:/sf_kpi_agents/SAP-SF-KPI-AGENTS/timetohire.html), [Onboarding](file:///d:/sf_kpi_agents/SAP-SF-KPI-AGENTS/onboarding.html), [Attrition](file:///d:/sf_kpi_agents/SAP-SF-KPI-AGENTS/attrition.html), [Offboarding](file:///d:/sf_kpi_agents/SAP-SF-KPI-AGENTS/offboarding.html), and [Alerts](file:///d:/sf_kpi_agents/SAP-SF-KPI-AGENTS/alerts.html).
- **Global Theme Engine**: Dark / Light theme toggle with `localStorage` persistence and anti-flicker pre-render scripts.
