# CrownOps Railway staging setup

This source package is prepared for Railway staging deployment.

Important:
- Do not commit any `.env` file, database, uploads, passwords, OAuth secrets, or customer data.
- For the first staging deploy keep `CROWNOPS_ALLOW_EXTERNAL=0`.
- Use a Railway Volume mounted at `/data` before importing real data.
- Set `CROWNOPS_DB=/data/crownops.sqlite3` and `CROWNOPS_UPLOADS=/data/uploads` after the volume is attached.
- Railway injects `PORT`; the Dockerfile listens on it.
- Health endpoint: `/api/health`.
