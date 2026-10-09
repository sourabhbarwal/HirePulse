import json
import os
import sys
import time
from pathlib import Path

import boto3
import fitz  # PyMuPDF
import pymongo
from dotenv import load_dotenv
from langchain_groq import ChatGroq

ROOT_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(ROOT_ENV_PATH, override=True)
load_dotenv(ENV_PATH, override=True)

from graph.graph import graph
from services.job_lifecycle import JobLifecycle
from services.progress_notifier import ProgressNotifier
from services.resume_renderer import ResumeRenderer

REQUIRED_ENV = [
    "MONGODB_URI",
    "AWS_REGION",
    "S3_BUCKET",
    "SQS_QUEUE_URL",
    "GROQ_API_KEY",
    "GROQ_MODEL",
]

missing_env = [name for name in REQUIRED_ENV if not os.environ.get(name, "").strip()]
if missing_env:
    print(f"[worker] Missing env vars: {', '.join(missing_env)}")
    sys.exit(1)


def _aws_client_kwargs():
    kwargs = {"region_name": os.environ["AWS_REGION"]}
    access_key_id = os.environ.get("AWS_ACCESS_KEY_ID", "").strip()
    secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()

    if access_key_id and secret_access_key:
        kwargs["aws_access_key_id"] = access_key_id
        kwargs["aws_secret_access_key"] = secret_access_key

    return kwargs

sqs = boto3.client(
    "sqs",
    **_aws_client_kwargs(),
)
s3 = boto3.client(
    "s3",
    **_aws_client_kwargs(),
)

DB_NAME = os.environ.get("MONGODB_DB", "resumeforge")
mongo = pymongo.MongoClient(os.environ["MONGODB_URI"])
db = mongo[DB_NAME]
jobs = db["jobs"]

QUEUE_URL = os.environ["SQS_QUEUE_URL"]
BUCKET = os.environ["S3_BUCKET"]
LATEX_TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "shivansh_one_page_resume.tex"
job_lifecycle = JobLifecycle(jobs, sqs, QUEUE_URL)
set_status = job_lifecycle.set_status
progress_notifier = ProgressNotifier()

llm = ChatGroq(
    model=os.environ["GROQ_MODEL"],
    temperature=0.2,
    max_tokens=8192,
)
resume_renderer = ResumeRenderer(llm, LATEX_TEMPLATE_PATH)


def extract_text_from_s3(s3_key: str) -> str:
    obj = s3.get_object(Bucket=BUCKET, Key=s3_key)
    pdf = fitz.open(stream=obj["Body"].read(), filetype="pdf")
    return "\n".join(page.get_text() for page in pdf)


def publish_progress(job_id: str, progress: list[str], status: str = "processing"):
    set_status(job_id, status, {"progress": progress})
    progress_notifier.publish(job_id, progress, status)


def process_job(job_id: str, receipt_handle: str) -> bool:
    print(f"[worker] Processing job {job_id}")
    job, claim_action = job_lifecycle.claim_job(job_id)
    if not job:
        return claim_action == "delete"

    stop_heartbeat, heartbeat_thread = job_lifecycle.start_visibility_heartbeat(job_id, receipt_handle)
    progress = ["Worker claimed the job and started processing."]
    publish_progress(job_id, progress)

    try:
        try:
            progress.append("Extracting resume text from the uploaded PDF.")
            publish_progress(job_id, progress)
            resume_text = extract_text_from_s3(job["resumeS3Key"])
        except Exception as error:
            progress.append(f"PDF extraction failed: {error}")
            publish_progress(job_id, progress, "failed")
            set_status(job_id, "failed", {"errorMessage": f"PDF extraction failed: {error}"})
            raise RuntimeError(f"PDF extraction failed: {error}") from error

        initial_state = {
            "job_id": job_id,
            "resume_text": resume_text,
            "jd_text": job["jdText"],
            "template_kind": job.get("templateKind", "sde"),
            "target_score": job.get("targetScore", 90),
            "parsed_resume": {},
            "parsed_jd": {},
            "ats_score": 0,
            "keyword_map": {},
            "gaps": [],
            "rewritten_resume": None,
            "rendered_resume_text": None,
            "suggestions": [],
            "iteration": 0,
            "final_score": None,
            "progress": [],
        }

        try:
            progress.append("Running the LangGraph resume optimization pipeline.")
            publish_progress(job_id, progress)
            result = graph.invoke(initial_state)
        except Exception as error:
            progress.append(f"Agent pipeline failed: {error}")
            publish_progress(job_id, progress, "failed")
            set_status(job_id, "failed", {"errorMessage": f"Agent error: {error}"})
            print(f"[worker] Agent failed for {job_id}: {error}")
            raise RuntimeError(f"Agent error: {error}") from error

        result_progress = result.get("progress", [])
        if result_progress:
            progress = progress + [step for step in result_progress if step not in progress]
            publish_progress(job_id, progress)

        final_resume_text = result.get("rewritten_resume") or resume_text
        generated_resume_key = None
        generated_resume_tex = None

        try:
            progress.append("Building structured resume content for the LaTeX template.")
            publish_progress(job_id, progress)
            resume_document = resume_renderer.build_resume_document(
                final_resume_text,
                resume_text,
                job.get("templateKind", "sde"),
            )
            progress.append("Compiling the final resume PDF.")
            publish_progress(job_id, progress)
            generated_resume_tex = resume_renderer.build_resume_latex(resume_document)
            pdf_bytes = resume_renderer.compile_latex_to_pdf(generated_resume_tex)
            generated_resume_key = f"generated-resumes/{job['userId']}/{job_id}.pdf"
            s3.put_object(
                Bucket=BUCKET,
                Key=generated_resume_key,
                Body=pdf_bytes,
                ContentType="application/pdf",
            )
            result["rendered_resume_text"] = json.dumps(resume_document)
            progress.append("Generated final resume PDF from the LaTeX template.")
            result["progress"] = progress

            # ── Post-PDF scoring (Bug #4 fix): score the ACTUAL document that
            #    became the PDF, not the fabricated hybrid from the graph.  This
            #    ensures the number shown to the user matches the real file.
            from tools.ats_scorer import compute_ats_score
            scorer_resume = resume_renderer.document_to_parsed_resume(resume_document)
            pdf_score, pdf_keyword_map, pdf_gaps = compute_ats_score(
                scorer_resume, result.get("parsed_jd", {})
            )
            result["final_score"] = pdf_score
            result["keyword_map"] = pdf_keyword_map
            result["gaps"] = pdf_gaps
            progress.append(f"Re-scored from actual PDF content: {pdf_score}/100.")
            result["progress"] = progress
        except Exception as error:
            progress.append(f"LaTeX PDF generation failed: {error}")
            result["progress"] = progress

        progress = result.get("progress", progress)

        set_status(
            job_id,
            "done",
            {
                "originalScore": result["ats_score"],
                "finalScore": result["final_score"],
                "rewrittenResume": result.get("rewritten_resume"),
                "renderedResumeText": result.get("rendered_resume_text"),
                "generatedResumeTex": generated_resume_tex,
                "generatedResumeS3Key": generated_resume_key,
                "gaps": result["gaps"],
                "suggestions": result["suggestions"],
                "keywordMap": result["keyword_map"],
                "iterations": result["iteration"],
                "templateKind": job.get("templateKind", "sde"),
                "targetScore": job.get("targetScore", 90),
                "progress": result.get("progress", []),
            },
        )
        progress_notifier.publish(job_id, progress, "done")

        print(f"[worker] Job {job_id} done. Score: {result['ats_score']} → {result['final_score']}")
        return True
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=1)


def poll():
    print(f"[worker] Loaded env from {ENV_PATH}")
    print(f"[worker] Mongo database: {DB_NAME}")
    print(f"[worker] Started. Polling SQS for jobs on {QUEUE_URL}")
    while True:
        try:
            response = sqs.receive_message(
                QueueUrl=QUEUE_URL,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=20,
                VisibilityTimeout=job_lifecycle.visibility_timeout,
            )
            messages = response.get("Messages", [])

            if not messages:
                continue

            print(f"[worker] Received {len(messages)} message(s)")
            for message in messages:
                should_delete = False

                try:
                    body = json.loads(message["Body"])
                    job_id = body["jobId"]
                except Exception as error:
                    print(f"[worker] Malformed message deleted: {error}")
                    should_delete = True

                try:
                    if not should_delete:
                        should_delete = process_job(job_id, message["ReceiptHandle"])
                except Exception as error:
                    print(f"[worker] Unhandled error for {job_id}: {error}")
                    set_status(job_id, "failed", {"errorMessage": str(error)})

                if should_delete:
                    sqs.delete_message(
                        QueueUrl=QUEUE_URL,
                        ReceiptHandle=message["ReceiptHandle"],
                    )
                else:
                    print(f"[worker] Leaving message for SQS retry: {job_id}")

        except KeyboardInterrupt:
            print("\n[worker] Shutting down.")
            sys.exit(0)
        except Exception as error:
            print(f"[worker] SQS error: {error} — retrying in 5s")
            time.sleep(5)


if __name__ == "__main__":
    poll()
