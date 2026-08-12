import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import uuid
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
UI_DIR = ROOT / "maps_export_ui"
EXPORT_SCRIPT = ROOT / "extract_maps_lists.py"
DATA_DIR = Path(os.environ.get("MAPS_EXPORT_DATA_DIR", ROOT.parent)).expanduser()
PROFILE_DIR = Path(os.environ.get("MAPS_PROFILE_DIR", DATA_DIR / "maps_profile")).expanduser()
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8765"))
ALLOWED_ORIGINS = {
    origin.strip().rstrip("/")
    for origin in os.environ.get("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
}

jobs = {}
jobs_lock = threading.Lock()


def public_job(job):
    return {
        "id": job["id"],
        "status": job["status"],
        "stage": job["stage"],
        "message": job["message"],
        "current": job["current"],
        "total": job["total"],
        "list_name": job["list_name"],
        "places": list(job["places"]),
        "current_place": dict(job["current_place"]) if job["current_place"] else None,
        "files": list(job["files"]),
        "error": job["error"],
        "started_at": job["started_at"],
    }


def update_job(job_id, **changes):
    with jobs_lock:
        jobs[job_id].update(changes)


def parse_progress(job_id, line):
    line = line.strip()
    if not line:
        return

    if line.startswith("MAPS_EXPORT_PLACE "):
        try:
            place = json.loads(line.removeprefix("MAPS_EXPORT_PLACE "))
        except json.JSONDecodeError:
            return
        with jobs_lock:
            jobs[job_id]["places"].append(place)
            jobs[job_id]["current_place"] = place
        return

    list_match = re.match(r"List:\s*(.+)", line)
    located_match = re.search(r"Located place\s+(\d+)/(\d+)", line)
    found_match = re.match(r"Found\s+(\d+)\s+unique place", line)
    exported_match = re.match(r"\[(\d+)/(\d+)\]\s+(Exported|Failed):\s*(.+)", line)

    if list_match:
        update_job(
            job_id,
            list_name=list_match.group(1),
            stage="discovering",
            message="Reading places from the list",
        )
    elif located_match:
        update_job(
            job_id,
            stage="discovering",
            current=int(located_match.group(1)),
            total=int(located_match.group(2)),
            message="Opening list entries",
        )
    elif found_match:
        total = int(found_match.group(1))
        update_job(
            job_id,
            stage="extracting",
            current=0,
            total=total,
            message=f"Extracting details from {total} places",
        )
    elif exported_match:
        current = int(exported_match.group(1))
        total = int(exported_match.group(2))
        verb = exported_match.group(3)
        place = exported_match.group(4)
        update_job(
            job_id,
            stage="extracting",
            current=current,
            total=total,
            message=(
                f"Exported {place}"
                if verb == "Exported"
                else f"Skipped {place}"
            ),
        )
    elif line.startswith("Finding all places"):
        update_job(
            job_id,
            stage="discovering",
            message="Finding places in the list",
        )


def run_export(job_id, list_url, output_prefix, formats):
    job = jobs[job_id]
    output_dir = DATA_DIR / "exports" / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    update_job(job_id, output_dir=output_dir)
    safe_prefix = re.sub(r"[^A-Za-z0-9_.-]+", "_", output_prefix).strip("._")
    safe_prefix = safe_prefix or "google_maps_list_export"
    base_prefix = output_dir / safe_prefix

    command = [
        sys.executable,
        "-u",
        str(EXPORT_SCRIPT),
        list_url,
        "--output",
        str(base_prefix),
        "--progress-json",
    ]
    if "json" in formats:
        command.append("--json")
    if "csv" in formats:
        command.append("--csv")

    try:
        environment = os.environ.copy()
        environment["MAPS_PROFILE_DIR"] = str(PROFILE_DIR)
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        update_job(job_id, process=process)

        for line in process.stdout:
            parse_progress(job_id, line)

        return_code = process.wait()
        if return_code != 0:
            raise RuntimeError(
                f"The exporter stopped with exit code {return_code}."
            )

        generated = []
        for path in sorted(output_dir.glob(f"{safe_prefix}_*")):
            if path.suffix.lower().lstrip(".") not in formats:
                continue
            generated.append(
                {
                    "name": path.name,
                    "format": path.suffix.lower().lstrip("."),
                    "size": path.stat().st_size,
                    "url": f"/api/jobs/{job_id}/files/{path.name}",
                }
            )

        if not generated:
            raise RuntimeError("The export finished but no output files were found.")

        update_job(
            job_id,
            status="completed",
            stage="completed",
            current=jobs[job_id]["total"],
            files=generated,
            message=f"Created {len(generated)} export file(s)",
        )
    except Exception as error:
        update_job(
            job_id,
            status="failed",
            stage="failed",
            error=str(error),
            message="Export failed",
        )
    finally:
        update_job(job_id, process=None)


class MapsExportHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(UI_DIR), **kwargs)

    def log_message(self, format_string, *args):
        print(f"[{self.log_date_time_string()}] {format_string % args}")

    def end_headers(self):
        origin = self.headers.get("Origin", "").rstrip("/")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        super().end_headers()

    def do_OPTIONS(self):
        origin = self.headers.get("Origin", "").rstrip("/")
        if origin not in ALLOWED_ORIGINS:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return None

    def do_POST(self):
        if self.path != "/api/jobs":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        payload = self.read_json()
        if payload is None:
            self.send_json(
                {"error": "Request body must be valid JSON."},
                HTTPStatus.BAD_REQUEST,
            )
            return

        list_url = str(payload.get("list_url", "")).strip()
        parsed_url = urlparse(list_url)
        allowed_hosts = {"maps.app.goo.gl", "www.google.com", "google.com"}
        if parsed_url.scheme != "https" or parsed_url.hostname not in allowed_hosts:
            self.send_json(
                {"error": "Enter a valid Google Maps share URL."},
                HTTPStatus.BAD_REQUEST,
            )
            return

        requested_formats = payload.get("formats", ["txt"])
        formats = {str(value).lower() for value in requested_formats}
        formats.add("txt")
        if not formats.issubset({"txt", "json", "csv"}):
            self.send_json(
                {"error": "Only TXT, JSON, and CSV formats are supported."},
                HTTPStatus.BAD_REQUEST,
            )
            return

        with jobs_lock:
            if any(job["status"] == "running" for job in jobs.values()):
                self.send_json(
                    {"error": "An export is already running."},
                    HTTPStatus.CONFLICT,
                )
                return

            job_id = uuid.uuid4().hex
            job = {
                "id": job_id,
                "status": "running",
                "stage": "starting",
                "message": "Starting Chromium",
                "current": 0,
                "total": 0,
                "list_name": "",
                "places": [],
                "current_place": None,
                "files": [],
                "error": "",
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "process": None,
                "output_dir": None,
            }
            jobs[job_id] = job

        output_prefix = str(
            payload.get("output_prefix", "google_maps_list_export")
        )
        thread = threading.Thread(
            target=run_export,
            args=(job_id, list_url, output_prefix, formats),
            daemon=True,
        )
        thread.start()
        self.send_json(public_job(job), HTTPStatus.ACCEPTED)

    def do_GET(self):
        job_match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})", self.path)
        file_match = re.fullmatch(
            r"/api/jobs/([a-f0-9]{32})/files/([^/]+)", self.path
        )

        if job_match:
            job_id = job_match.group(1)
            with jobs_lock:
                job = jobs.get(job_id)
                if not job:
                    self.send_json(
                        {"error": "Export job not found."},
                        HTTPStatus.NOT_FOUND,
                    )
                    return
                payload = public_job(job)
            self.send_json(payload)
            return

        if file_match:
            job_id, encoded_name = file_match.groups()
            filename = Path(unquote(encoded_name)).name
            with jobs_lock:
                job = jobs.get(job_id)
                allowed = job and any(
                    item["name"] == filename for item in job["files"]
                )
            if not allowed:
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            path = job["output_dir"] / filename
            if not path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            content = path.read_bytes()
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header(
                "Content-Disposition", f'attachment; filename="{path.name}"'
            )
            self.end_headers()
            self.wfile.write(content)
            return

        super().do_GET()


def main():
    if not UI_DIR.is_dir():
        raise SystemExit(f"UI directory not found: {UI_DIR}")

    server = ThreadingHTTPServer((HOST, PORT), MapsExportHandler)
    url = f"http://{HOST}:{PORT}"
    print(f"Google Maps List Exporter is running at {url}")
    print("Press Control-C to stop it.")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
