# 🇳🇱 NS Train Disruptions Data Pipeline & Live Dashboard

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://ns-disruption-dashboard-70283043290.streamlit.app/)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Supabase-green.svg)](https://supabase.com/)
[![GitHub Actions](https://img.shields.io/badge/orchestration-GitHub%20Actions-orange.svg)](https://github.com/features/actions)

An end-to-end data engineering and analytics project that automatically ingests real-time train disruption data across the Netherlands via the **NS (Nederlandse Spoorwegen) API**, processes geospatial and operational metrics, stores them in a **Supabase PostgreSQL** database, and visualizes live trends using an interactive **Streamlit** dashboard.

---

## 🏗️ Architecture & Data Flow

```mermaid
flowchart LR
    subgraph Data Extraction & Orchestration
        GH[GitHub Actions\nCron: Every 20 mins] -->|Triggers| ETL[src/ingestion.py\nExtract & Transform]
        NS[NS API Gateway\n/disruptions/v3] -->|HTTP GET / Retry Logic| ETL
    end

    subgraph Data Processing
        ETL -->|Haversine Formula| GEO[Affected Track Distance in km]
        ETL -->|Status Reconciliation| REC[Detect Resolved Disruptions]
    end

    subgraph Database Layer
        GEO -->|Upsert SCD1 Current State| T1[(disruptions table)]
        GEO -->|Append Event History| T2[(disruption_snapshots table)]
        REC -->|Mark is_active = FALSE| T1
    end

    subgraph Analytics & Presentation
        T1 -->|Read Table| ST[src/app.py\nStreamlit Dashboard]
        ST --> UI[Interactive Visualizations\nKPIs, Plotly Charts & Trends]
    end
```

---

## ⚙️ Key Engineering Features

- **Automated Cloud Orchestration**: GitHub Actions cron runs the ETL workflow automatically every 20 minutes (`7,27,47 * * * *`).
- **Resilient API Ingestion**: Built with `requests.Session` and `urllib3.util.retry.Retry` to handle transient network errors, rate-limiting (`429`), and server timeouts with exponential backoff.
- **Geospatial Distance Calculation**: Computes the physical track length (in kilometers) affected by each incident using the **Haversine formula** across consecutive station coordinates (`lat`, `lng`).
- **State Reconciliation**: Automatically tracks when disruptions finish by comparing the live feed with the database, updating active records to `is_active = FALSE`.
- **Dual Table Schema Strategy**:
  - `disruptions`: Stores the single current state of every incident (`ON CONFLICT (id) DO UPDATE`).
  - `disruption_snapshots`: Point-in-time event log for historical progression analysis.

---

## 🛠️ Tech Stack

- **Language & Pipeline**: Python 3.11, `psycopg2`, `requests`, `urllib3`
- **Database**: Supabase (Cloud PostgreSQL)
- **CI/CD & Orchestration**: GitHub Actions
- **Dashboard & Analytics**: Streamlit, Plotly, Pandas

---

## 📊 Dashboard Features

- **Real-Time KPIs**: Total disruptions, average duration, active incidents count, and cumulative weighted impact (`duration × affected_km`).
- **Interactive Time Series Trends**: Bar chart switchable between **Monthly** and **Weekly** views using custom Plotly interactive controls.
- **Root Cause & Disruption Type Breakdown**: Donut chart and horizontal ranking charts highlighting frequent causes (e.g., maintenance, weather, signal faults).
- **Live Search & Filter**: Filter by Year, Month, and Disruption Type, with an expandable raw record viewer.

---

## 💻 Local Development Setup

If you want to run this project locally on your machine:

### 1. Clone the repository
```bash
git clone https://github.com/sea-bug0310/ns-disruption-dashboard.git
cd ns-disruption-dashboard
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure Secrets
Create a `.env` file in the root directory for ingestion:
```env
DATABASE_URL=postgresql://<user>:<password>@<host>:<port>/postgres
NS_API_KEY=your_ns_api_portal_key
```

Create `.streamlit/secrets.toml` for the dashboard:
```toml
SUPABASE_URL = "https://your-project.supabase.co"
SUPABASE_KEY = "your-supabase-anon-key"
```

### 4. Run the Pipeline & App
```bash
# Run ingestion manually
python src/ingestion.py

# Launch the Streamlit dashboard
streamlit run src/app.py
```
---

## 📚 Data sources & attribution

- **NS Disruptions API** (live feed, via the NS API Portal).
- **Rijden de Treinen open data** (`data/disruptions-2025.csv`): disruption data for 2025, used for
  year-over-year comparison. Source: [Rijden de Treinen](https://www.rijdendetreinen.nl/en/open-data/disruptions),
  licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
