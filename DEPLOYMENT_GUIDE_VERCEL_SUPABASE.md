# 🚀 Drishti — 15-Minute Cloud Deployment Guide (Vercel + Supabase)

Deploy the **Drishti AI CCTV Intelligence & Surveillance Operating Platform** to production in under **15 minutes** with **zero server maintenance costs**.

| Component | Cloud Provider | Tier | Role |
| :--- | :--- | :--- | :--- |
| **Database** | [Supabase](https://supabase.com) | Free Tier (PostgreSQL + PostGIS) | Persistent Relational Store (Cameras, Alerts, Analytics, Users, Watchlists) |
| **Frontend & API** | [Vercel](https://vercel.com) | Free Tier (Edge CDN + Python Serverless) | Static UI, HLS Proxy, REST API, Auth, Analytics Engine |

---

## ⏱️ Step-by-Step 15-Minute Timeline

```mermaid
gantt
    title 15-Minute Cloud Deployment Timeline
    dateFormat  m
    axisFormat %M min
    section Supabase Setup
    Create Project & Region          :a1, 0, 2m
    Copy Connection Pooler URL       :a2, after a1, 1m
    Run SQL Schema (Optional)        :a3, after a2, 1m
    section GitHub Sync
    Commit & Push Cloud Config       :b1, after a3, 2m
    section Vercel Deployment
    Import GitHub Repository         :c1, after b1, 2m
    Configure Environment Variables  :c2, after c1, 2m
    Deploy & Build                   :c3, after c2, 2m
    section Verification
    Login, Camera Grid & Analytics   :d1, after c3, 3m
```

---

## 📍 Part 1: Supabase Database Setup (3 Minutes)

### Step 1.1 — Create a New Supabase Project
1. Open [supabase.com/dashboard](https://supabase.com/dashboard) and sign in with GitHub.
2. Click **New project** (or select your organization).
3. Fill in the project details:
   - **Name**: `drishti-cctv`
   - **Database Password**: Set a strong password (e.g. `GujaratPoliceSecure2026!`) — **save this!**
   - **Region**: Choose `South Asia (Mumbai) [ap-south-1]` (or nearest to your location).
   - **Pricing Plan**: Free.
4. Click **Create new project**. Supabase provisions the database in ~60-90 seconds.

### Step 1.2 — Copy the Connection Pooler URI
> [!IMPORTANT]
> Because Vercel uses serverless functions, you must use **Supabase Connection Pooling (port 6543)** in **Transaction Mode** to prevent exhausting PostgreSQL connection limits.

1. In the Supabase sidebar, click **Project Settings** (gear icon at the bottom) ➔ **Database**.
2. Scroll down to the **Connection string** card.
3. Select the **URI** tab.
4. Set the dropdown to:
   - **Mode**: `Transaction` (or `Session`)
   - **Port**: `6543`
5. Copy the connection URI. It will look like:
   ```text
   postgresql://postgres.[PROJECT-REF]:[YOUR-PASSWORD]@aws-0-ap-south-1.pooler.supabase.com:6543/postgres
   ```
6. Replace `[YOUR-PASSWORD]` with your actual database password.

### Step 1.3 — (Optional) Pre-create Database Tables
Drishti auto-creates all tables on first startup via SQLAlchemy `Base.metadata.create_all()`.
If you want to manually run the schema immediately:
1. In Supabase sidebar, click **SQL Editor**.
2. Click **New query**.
3. Open [`docs/supabase_schema.sql`](file:///c:/Users/tirth/Desktop/Sentinel_Gujarat_CCTV_Platform-main/Sentinel_Gujarat_CCTV_Platform-main/docs/supabase_schema.sql) in this repository, copy the entire SQL script, paste it into the editor, and click **Run**.
4. Verify that tables (`users`, `cameras`, `alerts`, `detection_events`, `watchlists`) appear in the **Table Editor**.

---

## 📍 Part 2: Prepare Codebase & GitHub (2 Minutes)

All necessary Vercel files are already configured in this repository:
- [`vercel.json`](file:///c:/Users/tirth/Desktop/Sentinel_Gujarat_CCTV_Platform-main/Sentinel_Gujarat_CCTV_Platform-main/vercel.json): Dynamic rewrites routing `/ui/*` to static frontend and `/api/*`, `/cameras/*`, `/auth/*`, etc. to the serverless function.
- [`api/index.py`](file:///c:/Users/tirth/Desktop/Sentinel_Gujarat_CCTV_Platform-main/Sentinel_Gujarat_CCTV_Platform-main/api/index.py): Vercel Python entrypoint for FastAPI.
- [`api/requirements.txt`](file:///c:/Users/tirth/Desktop/Sentinel_Gujarat_CCTV_Platform-main/Sentinel_Gujarat_CCTV_Platform-main/api/requirements.txt): Lightweight serverless package list (< 40MB, well below Vercel's 250MB lambda size limit).

Commit and push your changes to GitHub:
```bash
git add vercel.json api/ backend/app/db/session.py frontend/app.js docs/
git commit -m "feat: configure Vercel serverless deployment and Supabase connection pooler"
git push origin feat/ai-incident-and-stream-isolation
# (Or push to your main branch / MazedOut upstream)
```

---

## 📍 Part 3: Deploy on Vercel (5 Minutes)

### Step 3.1 — Import Project in Vercel
1. Go to [vercel.com](https://vercel.com) and sign in with GitHub.
2. From the Vercel dashboard, click **Add New...** ➔ **Project**.
3. Select your repository: `MazedOut/Sentinel_Gujarat_CCTV_Platform` (or your fork).
4. Click **Import**.

### Step 3.2 — Configure Project & Environment Variables
On the configuration screen:
1. **Project Name**: `drishti-cctv` (or your preferred name).
2. **Framework Preset**: Leave as **Other**.
3. **Root Directory**: `./` (leave default).
4. Expand **Environment Variables** and add the following 4 keys:

| Environment Variable | Value | Notes |
| :--- | :--- | :--- |
| `DATABASE_URL` | `postgresql://postgres.[REF]:[PASSWORD]@aws-0-ap-south-1.pooler.supabase.com:6543/postgres` | Your Supabase connection string from Step 1.2 |
| `SENTINEL_CATALOGUE_COOKIE` | `session_cookie_value_from_your_env` | Required for fetching live 30-camera catalogue |
| `SENTINEL_HLS_COOKIE` | `session_cookie_value_from_your_env` | Required for authenticated HLS streaming proxy |
| `SECRET_KEY` | `drishti_gujarat_police_secure_jwt_secret_key_2026_prod` | 32+ character string for JWT signing |

> [!TIP]
> If you have existing cookies in your local `.env`, copy the values of `SENTINEL_CATALOGUE_COOKIE` and `SENTINEL_HLS_COOKIE` directly into Vercel.

### Step 3.3 — Click Deploy
1. Click **Deploy**.
2. Vercel will install the serverless dependencies and build your project in ~60-90 seconds.
3. Once complete, you will see the confetti screen and your live URL:
   `https://drishti-cctv.vercel.app`

---

## 📍 Part 4: Post-Deployment Verification (3 Minutes)

### Step 4.1 — Open the Live Application
Navigate to your Vercel URL (e.g., `https://drishti-cctv.vercel.app/` or `/ui/`).

### Step 4.2 — Sign in to the Gujarat Police Operations Portal
Use the pre-seeded credentials:

| Role | Username | Password | Access Level |
| :--- | :--- | :--- | :--- |
| **System Administrator** | `admin` | `sentinel_admin` | Full Command Access (All Tabs, Config, System Audit) |
| **Police Officer** | `officer1` | `sentinel_officer` | Operational Access (Live Feeds, GIS Map, Analytics, Alerts) |

### Step 4.3 — Verification Checklist for Jury Evaluation
- [x] **Ice-White Auth Portal**: Custom Drishti SVG optical emblem, secure password validation.
- [x] **Overview Screen**: Total cameras (30 registered), stream health badges, system metrics.
- [x] **Live CCTV Grid**: Click on cameras `CAM-01` through `CAM-30` to verify live video streams and HLS proxying.
- [x] **GIS Geospatial Map**: Interactive Leaflet map displaying 30 cameras with coordinate clustering across Ahmedabad and Gandhinagar.
- [x] **Video & Operational Analytics**:
  - 8 KPI cards: Total Detections, Vehicles, Persons, ANPR Reads, Unique Plates, Watchlist Matches, Alerts, Active Cameras.
  - 4 Dynamic Charts: Detection Distribution, Hourly Intensity, Vehicle Breakdown, Severity Split.
  - ANPR Detection Log table with OCR confidence.
  - **Export Analytics Report (CSV)** button for instant report generation.
- [x] **Supabase Verification**: Open Supabase Table Editor ➔ see live data rows in `users`, `cameras`, `detection_events`, and `alerts`.

---

## 💡 Architecture & Production Recommendations

### Fullstack Serverless vs. Hybrid Architecture

```
                               ┌─────────────────────────────┐
                               │       Vercel (Edge)         │
                               │   - Static Web Portal       │
                               │   - FastAPI Serverless API  │
                               │   - HLS Video Proxy         │
                               │   - JWT Auth & RBAC         │
                               └──────────────┬──────────────┘
                                              │
                      ┌───────────────────────┴───────────────────────┐
                      ▼                                               ▼
       ┌─────────────────────────────┐                 ┌─────────────────────────────┐
       │     Supabase Cloud DB       │                 │   Optional AI Stream Worker │
       │   - PostgreSQL Database     │                 │   (Local GPU / Render / VPS)│
       │   - PgBouncer Connection    │◄────────────────┤   - scripts/start_stream.py │
       │     Pooler (Port 6543)      │                 │   - YOLOv8 + PaddleOCR      │
       │   - PostGIS Spatial Data    │                 │   - Live Ingestion Loop     │
       └─────────────────────────────┘                 └─────────────────────────────┘
```

1. **Vercel Serverless (Ready Out of the Box)**:
   - Delivers the web app, login, cameras, GIS, HLS proxy, analytics dashboard, alerts, and Supabase persistence.
   - Zero maintenance, fast worldwide CDN response.
2. **Heavy AI Worker (`scripts/start_stream.py`)**:
   - Running real-time YOLOv8 + PaddleOCR on 30 continuous RTSP streams requires continuous GPU/CPU execution.
   - Run `python scripts/start_stream.py --all-cameras` on your local laptop, a cloud GPU VM (RunPod/AWS/GCP), or Docker container connected to the same Supabase `DATABASE_URL`!

---

## 🛠️ Troubleshooting & FAQ

### Q: Vercel displays `500: INTERNAL_SERVER_ERROR` on `/api/index.py`
- **Cause**: Incorrect database connection string or missing environment variable.
- **Fix**: Check Vercel ➔ **Settings** ➔ **Environment Variables**. Ensure `DATABASE_URL` uses `postgresql://` and port `6543`. If your password contains special characters (`#`, `@`, `!`), URL-encode them (e.g. `@` becomes `%40`).

### Q: Video streams show loading spinner or `SOURCE UNAVAILABLE`
- **Cause**: Expired Sentinel session cookies.
- **Fix**: Log into the official Gujarat CCTV portal in your browser, copy the active `Cookie` string from browser DevTools (Network tab), update `SENTINEL_CATALOGUE_COOKIE` and `SENTINEL_HLS_COOKIE` in Vercel, and click **Redeploy**.

### Q: Supabase error: `remaining connection slots are reserved for non-replication superuser connections`
- **Cause**: Using direct connection port `5432` instead of pooler port `6543`.
- **Fix**: Switch your `DATABASE_URL` port to `6543` in Supabase connection settings.

---

**Drishti CCTV Intelligence Platform is now live and cloud-deployed!**
