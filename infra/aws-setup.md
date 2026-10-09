# HireForge AWS Deployment

This project is ready to deploy as three containers:

- `frontend`: static React app served by Nginx
- `backend`: Express API and WebSocket server
- `agent`: Python worker polling SQS and writing results to MongoDB

## Recommended AWS shape

- Amazon ECR for container images
- Amazon ECS Fargate for `frontend`, `backend`, and `agent`
- Application Load Balancer in front of `frontend` and `backend`
- Amazon S3 for uploaded resumes
- Amazon SQS for async job dispatch
- Amazon DocumentDB or MongoDB Atlas for `MONGODB_URI`
- AWS Secrets Manager for runtime secrets

Ready-to-edit ECS deployment assets live in [`infra/ecs`](/home/shivansh/HireForge/infra/ecs/README.md:1).

## Required secrets and config

Set these in Secrets Manager or ECS task definitions:

- `MONGODB_URI`
- `JWT_SECRET`
- `AWS_REGION`
- `S3_BUCKET`
- `SQS_QUEUE_URL`
- `GROQ_API_KEY`
- `GROQ_MODEL`
- `FRONTEND_URL`
- `BACKEND_INTERNAL_URL`
- `INTERNAL_WORKER_TOKEN`
- `PORT=3001`

Notes:

- In ECS, prefer IAM task roles instead of `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`.
- The `agent` container now compiles LaTeX PDFs, so its image includes TeX Live.
- Configure the SQS jobs queue with a dead-letter queue and a redrive policy, for example `maxReceiveCount=3`.
- The worker sets per-message visibility while receiving and extends it during long jobs. Defaults are `SQS_VISIBILITY_TIMEOUT=300`, `SQS_HEARTBEAT_INTERVAL=60`, and `PROCESSING_STALE_SECONDS=600`.
- Set the same `INTERNAL_WORKER_TOKEN` secret on the backend and agent. The agent uses `BACKEND_INTERNAL_URL` to POST progress updates, and the backend broadcasts those updates to authenticated WebSocket clients.

For the frontend container build:

- `VITE_API_URL=https://<your-api-domain>/api`

## Build images

```bash
docker build -t hireforge-frontend ./frontend
docker build -t hireforge-backend ./backend
docker build -t hireforge-agent ./agent
```

## Push to ECR

```bash
aws ecr create-repository --repository-name hireforge-frontend
aws ecr create-repository --repository-name hireforge-backend
aws ecr create-repository --repository-name hireforge-agent
```

Authenticate Docker, tag each image, and push to the matching ECR repository.

Or use:

```bash
AWS_REGION=ap-south-1 AWS_ACCOUNT_ID=123456789012 IMAGE_TAG=prod-001 \
VITE_API_URL=https://api.example.com/api \
bash infra/ecs/build-and-push.sh
```

## ECS services

Create three task definitions:

- `hireforge-frontend`
  - container port `80`
  - public via ALB
- `hireforge-backend`
  - container port `3001`
  - public via ALB
  - health check path `/api/health`
- `hireforge-agent`
  - no public listener
  - desired count `1`

Recommended desired counts:

- `frontend`: `1`
- `backend`: `1` to start, then scale out
- `agent`: `1` to start, then scale based on queue depth

The repository includes starter task definition templates:

- `infra/ecs/taskdef-frontend.json`
- `infra/ecs/taskdef-backend.json`
- `infra/ecs/taskdef-agent.json`

## Networking notes

- Put ECS services in private subnets where possible.
- Allow the backend and agent to reach S3, SQS, Groq, and Mongo.
- Set `FRONTEND_URL` on the backend to the frontend domain.
- Set `BACKEND_INTERNAL_URL` on the agent to the backend URL reachable from the agent task.
- Set `VITE_API_URL` on the frontend build to the backend public URL.

## First deployment checklist

1. Create the S3 bucket, SQS jobs queue, and SQS dead-letter queue.
2. Provision DocumentDB or another Mongo-compatible database and capture the connection string.
3. Store secrets in Secrets Manager.
4. Build and push the three images to ECR.
5. Create ECS task definitions with the correct environment variables.
6. Create ECS services and attach ALB listeners for frontend and backend.
7. Verify `/api/health`, registration/login, PDF upload, and SQS worker processing.

## Operational note

Any time you change the LaTeX template or worker logic, rebuild and push the `agent` image, then redeploy the `agent` ECS service.
