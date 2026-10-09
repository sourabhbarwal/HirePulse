import json
import os
import urllib.error
import urllib.request


class ProgressNotifier:
    def __init__(self):
        self.backend_url = os.environ.get("BACKEND_INTERNAL_URL", "").rstrip("/")
        self.token = os.environ.get("INTERNAL_WORKER_TOKEN", "")
        self.enabled = bool(self.backend_url and self.token)

    def publish(self, job_id: str, progress: list[str], status: str | None = None):
        if not self.enabled:
            return

        payload = {"progress": progress}
        if status:
            payload["status"] = status

        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.backend_url}/api/internal/jobs/{job_id}/progress",
            data=data,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            print(f"[worker] Progress callback failed for {job_id}: {error}")
