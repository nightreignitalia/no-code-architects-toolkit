from flask import Blueprint, request, jsonify
import os, subprocess, uuid, tempfile
from google.cloud import storage
from app_utils import (
    validate_payload,
    queue_task_wrapper,
)
from services.authentication import authenticate  # already in the repo
from services.cloud_storage import upload_file    # convenience helper
import logging

# ------------------------------------------------------------------
# 1)  Blueprint declaration: Flask auto-discovers this object
# ------------------------------------------------------------------
v1_video_merge_audio_bp = Blueprint("v1_video_merge_audio", __name__)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 2)  JSON schema – makes payload validation identical to other routes
# ------------------------------------------------------------------
PAYLOAD_SCHEMA = {
    "type": "object",
    "properties": {
        "video_url": {"type": "string", "format": "uri"},
        "audio_url": {"type": "string", "format": "uri"},
        "webhook_url": {"type": "string", "format": "uri"},
        "id": {"type": "string"},
    },
    "required": ["video_url", "audio_url"],
    "additionalProperties": False,
}

# ------------------------------------------------------------------
# 3)  Helper functions (download ↔️  GCS, run FFmpeg, upload result)
# ------------------------------------------------------------------
API_KEY     = os.getenv("API_UNCORE_KEY")     # already set in Cloud Run
BUCKET_NAME = os.getenv("GCP_BUCKET_NAME")
gcs_client  = storage.Client()

def _download(gcs_url: str, dest: str) -> None:
    """
    gcs_url must be of the form
    https://storage.googleapis.com/<bucket>/<blob>
    """
    if not gcs_url.startswith("https://storage.googleapis.com/"):
        raise ValueError("URL must be a public GCS link")
    bucket_name, blob_name = gcs_url.replace(
        "https://storage.googleapis.com/", ""
    ).split("/", 1)
    gcs_client.bucket(bucket_name).blob(blob_name).download_to_filename(dest)

def _upload(local_path: str) -> str:
    dst_blob = f"merged/{uuid.uuid4()}.mp4"
    blob = gcs_client.bucket(BUCKET_NAME).blob(dst_blob)
    blob.upload_from_filename(local_path)
    blob.make_public()
    return f"https://storage.googleapis.com/{BUCKET_NAME}/{dst_blob}"

# ------------------------------------------------------------------
# 4)  Route handler
# ------------------------------------------------------------------
@v1_video_merge_audio_bp.route("/v1/video/merge-audio", methods=["POST"])
@authenticate                                           # ⬅︎ API-key check
@validate_payload(PAYLOAD_SCHEMA)                       # ⬅︎ JSON schema
@queue_task_wrapper(bypass_queue=False)                 # ⬅︎ job queue
def merge_audio(job_id, data):
    """
    POST body:
      { "video_url": "...", "audio_url": "...", ... }
    Returns:
      { "merged_url": "https://storage.googleapis.com/..." }
    """
    logger.info(f"Job {job_id}: merging audio into video")

    with tempfile.TemporaryDirectory() as tmp:
        vid  = os.path.join(tmp, "video.mp4")
        aud  = os.path.join(tmp, "track")
        outp = os.path.join(tmp, "merged.mp4")

        # 1. Download assets
        _download(data["video_url"], vid)
        _download(data["audio_url"], aud)

        # 2. FFmpeg – copy video, encode audio to AAC, stop at the shorter
        cmd = [
            "ffmpeg", "-y",
            "-i", vid, "-i", aud,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac",
            "-shortest",
            outp,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            logger.error(proc.stderr)
            return proc.stderr, "/v1/video/merge-audio", 500

        # 3. Upload result to the same bucket
        public_url = _upload(outp)
        logger.info(f"Job {job_id}: finished – {public_url}")
        return public_url, "/v1/video/merge-audio", 200
