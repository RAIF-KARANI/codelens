# The demo API on Vercel

This folder is the **root directory of the Vercel project for the demo's API**
(`docs/demo.md`). It holds no code of its own:

| File | What it's for |
|---|---|
| `main.py` | Vercel's entrypoint: imports the FastAPI `app` from `services/api` |
| `requirements.txt` | Pinned runtime dependencies, generated from `uv.lock` by `scripts/export_demo_requirements.py` (CI checks it's current), plus the two local packages by path |
| `.python-version` | Python 3.12, as the project |
| `vercel.json` | A daily cron calling `/health`: it touches the database, which keeps a free Supabase project from pausing after a week idle |

## Vercel project settings

| Setting | Value |
|---|---|
| Root Directory | `deploy/api` |
| Include files outside the root directory in the Build Step | **on** (the default): `requirements.txt` installs `../../packages/core` and `../../services/api` |
| Framework Preset | FastAPI (detected) |
| Function region | the same as the Supabase project's (e.g. Mumbai `bom1` with Supabase `ap-south-1`): every page makes several database queries |

Environment variables:

| Name | Value |
|---|---|
| `DEMO_MODE` | `true` |
| `DATABASE_URL` | Supabase's **Session pooler** connection string, as `postgresql+asyncpg://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?ssl=require` |
| `API_JWT_SECRET` | the demo's own secret (`.env.demo`), the same value as the dashboard's |

No Redis, GitHub or model settings: a demo runs on Postgres alone.

## Checked locally

A fresh Python 3.12 environment with only `pip install -r requirements.txt`
(58 MB, against Vercel's 500 MB limit) served `main:app` with `DEMO_MODE=true` and an
unreachable Redis: `/health` ok, the repos and a review's events with a visitor token,
401 without one.
