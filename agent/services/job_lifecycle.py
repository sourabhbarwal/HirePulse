import os
import threading
from datetime import datetime, timedelta, timezone

import pymongo


class JobLifecycle:
    def __init__(self, jobs_collection, sqs_client, queue_url: str):
        self.jobs = jobs_collection
        self.sqs = sqs_client
        self.queue_url = queue_url
        self.visibility_timeout = self._visibility_timeout()
        self.heartbeat_interval = self._heartbeat_interval()
        self.processing_stale_seconds = self._processing_stale_seconds()

    @staticmethod
    def _visibility_timeout() -> int:
        configured = int(os.environ.get("SQS_VISIBILITY_TIMEOUT", "300"))
        return max(30, min(configured, 43200))

    def _heartbeat_interval(self) -> int:
        configured = int(os.environ.get("SQS_HEARTBEAT_INTERVAL", "60"))
        return max(5, min(configured, self.visibility_timeout - 5))

    def _processing_stale_seconds(self) -> int:
        configured = int(
            os.environ.get("PROCESSING_STALE_SECONDS", str(self.visibility_timeout * 2))
        )
        return max(self.visibility_timeout, configured)

    def set_status(self, job_id: str, status: str, extra: dict | None = None):
        if extra is None:
            extra = {}

        timestamp_fields = {}
        if status == "done":
            timestamp_fields["completedAt"] = datetime.now(timezone.utc)
        elif status == "failed":
            timestamp_fields["failedAt"] = datetime.now(timezone.utc)

        self.jobs.update_one(
            {"_id": job_id},
            {"$set": {"status": status, **timestamp_fields, **extra}},
        )

    def claim_job(self, job_id: str) -> tuple[dict | None, str]:
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(seconds=self.processing_stale_seconds)
        job = self.jobs.find_one_and_update(
            {
                "_id": job_id,
                "$or": [
                    {"status": {"$in": ["queued", "failed"]}},
                    {"status": "processing", "processingHeartbeatAt": {"$lt": stale_before}},
                    {
                        "status": "processing",
                        "processingHeartbeatAt": {"$exists": False},
                        "processingStartedAt": {"$lt": stale_before},
                    },
                ],
            },
            {
                "$set": {
                    "status": "processing",
                    "processingStartedAt": now,
                    "processingHeartbeatAt": now,
                    "errorMessage": None,
                },
                "$inc": {"attemptCount": 1},
            },
            return_document=pymongo.ReturnDocument.AFTER,
        )

        if job:
            return job, "claimed"

        existing = self.jobs.find_one({"_id": job_id}, {"status": 1, "processingHeartbeatAt": 1})
        if not existing:
            print(f"[worker] Job {job_id} not found in DB - deleting orphaned queue message")
            return None, "delete"

        status = existing.get("status")
        if status == "done":
            print(f"[worker] Job {job_id} already done - deleting duplicate queue message")
            return None, "delete"

        print(f"[worker] Job {job_id} is {status}; leaving message for retry/owner")
        return None, "retry_later"

    def start_visibility_heartbeat(self, job_id: str, receipt_handle: str):
        stop_event = threading.Event()

        def heartbeat():
            while not stop_event.wait(self.heartbeat_interval):
                try:
                    self.sqs.change_message_visibility(
                        QueueUrl=self.queue_url,
                        ReceiptHandle=receipt_handle,
                        VisibilityTimeout=self.visibility_timeout,
                    )
                    self.jobs.update_one(
                        {"_id": job_id, "status": "processing"},
                        {"$set": {"processingHeartbeatAt": datetime.now(timezone.utc)}},
                    )
                    print(f"[worker] Extended visibility timeout for job {job_id}")
                except Exception as error:
                    print(f"[worker] Failed to extend visibility for {job_id}: {error}")

        thread = threading.Thread(target=heartbeat, name=f"sqs-heartbeat-{job_id}", daemon=True)
        thread.start()
        return stop_event, thread
