# HirePulse

AI resume optimization app. Users upload a resume and job description, the backend queues the work, and a Python LangGraph agent rewrites, scores, and generates a final LaTeX PDF.

## Core Flow

```text
Frontend -> Backend -> S3 original PDF -> Mongo queued Job -> SQS
SQS -> Agent -> LangGraph parse/score/rewrite/verify -> LaTeX PDF
Agent -> S3 generated PDF -> Mongo done Job -> Frontend presigned URL
```

Live progress:

```text
Agent -> Backend internal callback -> WebSocket -> Frontend
```

## Services

```text
frontend/   React + Vite dashboard, served by Nginx
backend/    Express API, auth, S3/SQS, job APIs, WebSocket
agent/      Python worker, LangGraph pipeline, ATS scoring, LaTeX renderer
infra/      AWS and ECS deployment notes
```

## Requirements

- Docker and Docker Compose
- AWS S3 bucket
- AWS SQS queue
- Groq API key

Use a private S3 bucket. For reliable retries, configure an SQS dead-letter queue.

## Run With Docker

Create env file:

```bash
cp .env.example .env
```

Fill these values in `.env`:

```text
JWT_SECRET=
INTERNAL_WORKER_TOKEN=
AWS_REGION=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
S3_BUCKET=
SQS_QUEUE_URL=
GROQ_API_KEY=
GROQ_MODEL=qwen/qwen3.8-27b
FRONTEND_URL=http://localhost
VITE_API_URL=/api
```

Start:

```bash
docker compose up --build -d
```

Check:

```bash
docker compose ps
curl -fsS http://localhost/api/health
```

Open:

```text
http://localhost
```

Logs:

```bash
docker compose logs -f backend agent frontend
```

Stop:

```bash
docker compose down
```

Reset local Mongo data:

```bash
docker compose down -v
```

If port 80 is busy:

```bash
APP_PORT=8080 docker compose up --build -d
```

Then open `http://localhost:8080`.

## Local Dev

Backend and agent load root `.env` first. `backend/.env` and `agent/.env` can override local values.

```bash
cp .env.example .env
cp backend/.env.example backend/.env
cp agent/.env.example agent/.env
```

Run each service:

```bash
docker run -d -p 27017:27017 --name hirepulse-mongo mongo:7
cd backend && npm ci && npm run dev
cd agent && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && python worker.py
cd frontend && npm ci && npm run dev
```

## Useful Checks

```bash
python agent/test_resume_renderer.py
python -m py_compile agent/worker.py agent/services/*.py
node --check backend/src/app.js
npm --prefix frontend run lint
npm --prefix frontend run build
docker compose config --services
```

## Key Endpoints

```text
POST /api/auth/register
POST /api/auth/login
POST /api/upload
GET  /api/jobs
GET  /api/jobs/:jobId
POST /api/internal/jobs/:jobId/progress
WS   /ws?jobId=<id>&token=<jwt>
```

## Notes

- Do not commit `.env` files or cloud credentials.
- Docker Compose uses local Mongo through `mongodb://mongo:27017/resumeforge`.
- `INTERNAL_WORKER_TOKEN` must match between backend and agent.
- Deep AWS setup lives in `infra/aws-setup.md`.
- ECS deployment notes live in `infra/ecs/README.md`.
