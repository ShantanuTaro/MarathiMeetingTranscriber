"""Web portal: upload Marathi meeting audio -> download reviewed Word doc.

Every backend is hosted, so this server holds no weights and downloads nothing at
startup. What it does hold is a key, in memory, for as long as a tab keeps its
session. See SESSIONS.
"""
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

import requests
from fastapi import (BackgroundTasks, FastAPI, File, Form, HTTPException,
                     UploadFile)
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

import transcribe as core

HERE = Path(__file__).parent
UPLOADS = HERE / "uploads"
OUT = HERE / "out"
UPLOADS.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)

# What the page lists, in the order it lists them. `rate` is rupees per hour of
# audio and is what the meter counts in; ElevenLabs prices in characters against a
# plan quota, which is not a number this can turn into money, so it has none and
# the meter there counts jobs instead of inventing a figure.
BACKENDS = {
    "sarvam": {
        "name": "Sarvam", "env": "SARVAM_API_KEY", "rate": core.SARVAM_RATE,
        "note": "India-first, best on Marathi. No per-word confidence, so nothing "
                "is highlighted as unsure.",
        # a guide this server serves, not the provider's dashboard. The dashboard is
        # one click further in, from a page that also says which permission the key
        # needs, what an hour costs, and how to not spend the free credit on nothing.
        "keys": "/help/sarvam-api-key",
    },
    "elevenlabs": {
        "name": "ElevenLabs Scribe", "env": "ELEVENLABS_API_KEY", "rate": None,
        "note": "Per-word confidence, so uncertain words are highlighted. Billed "
                "against your ElevenLabs plan.",
        "keys": "/help/elevenlabs-api-key",
    },
}

# A key the page handed over, good for as long as the tab keeps its session. Memory
# only: never written to disk, never sent back to the browser, never included in
# /jobs. One token is one backend: a Sarvam key must never ride to ElevenLabs.
# "spent" is what this session has told that backend to bill.
SESSIONS = {}


def session_key(token, asr):
    s = SESSIONS.get(token) or {}
    return s["key"] if s.get("asr") == asr else None


def ready(asr, token=None):
    return bool(session_key(token, asr) or os.getenv(BACKENDS[asr]["env"]))


def options(token=None):
    """The backends the page lists, and which of them can run right now.

    Everything is listed whether it has a key or not: the page can take one for the
    session, so an unconfigured backend is a prompt rather than an absence. `ready`
    is what /upload enforces; listing a backend is not the same as accepting a job.
    """
    return [{"id": a, "name": b["name"], "repo": core.model_name(a),
             "ready": ready(a, token), "confidence": core.has_confidence(a),
             "rate": b["rate"], "note": b["note"], "keys": b["keys"],
             "session": (SESSIONS.get(token) or {}).get("asr") == a}
            for a, b in BACKENDS.items()]


def verify(asr, key):
    """Prove a key works before any audio moves, and bill nothing doing it.

    Sarvam: create a job and never start it, since billing begins at /start, so this is
    free. ElevenLabs: read the account. A bad key fails here rather than after an
    hour-long upload.
    """
    try:
        if asr == "sarvam":
            r = requests.post(
                "https://api.sarvam.ai/speech-to-text/job/v1", timeout=30,
                headers={"api-subscription-key": key},
                json={"job_parameters": {"model": core.SARVAM_MODEL,
                                         "with_diarization": core.SARVAM_DIARIZE,
                                         "with_timestamps": True}})
        else:
            r = requests.get("https://api.elevenlabs.io/v1/user", timeout=30,
                             headers={"xi-api-key": key})
    except requests.RequestException as e:
        raise HTTPException(502, f"Could not reach {BACKENDS[asr]['name']}: {e}")
    if r.status_code in (401, 403):
        raise HTTPException(401, f"{BACKENDS[asr]['name']} rejected that key")
    if not r.ok:
        raise HTTPException(502, f"{BACKENDS[asr]['name']} {r.status_code}: {r.text[:200]}")


app = FastAPI()
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
JOBS = {}

# ponytail: one job at a time. The backends bill per second and rate-limit per key,
# so the lock is the queue and a second concurrent upload buys nothing.
_lock = threading.Lock()

DONE = ("done", "error", "cancelled")


def stopping(job_id):
    """A job the user cancelled, or deleted outright while it was running.

    Missing job -> stop (Clear removed it). Present job with no flag -> keep going;
    the default must live on the lookup of the job, never on the lookup of the flag.
    """
    job = JOBS.get(job_id)
    return job is None or bool(job.get("cancel"))


def run_job(job_id, path, name, asr, key=None, token=None):
    job = JOBS.get(job_id)
    if job is None:               # cleared between upload and the worker starting
        path.unlink(missing_ok=True)
        return
    try:
        # `with _lock` would park a queued job here until the running one finished,
        # and a queued job is the one you most expect Stop to kill instantly.
        while not _lock.acquire(timeout=1):
            if stopping(job_id):
                raise core.Cancelled
        try:
            if stopping(job_id):
                raise core.Cancelled
            # `began` and `audio` are what the page estimates progress from: a
            # hosted job reports which step it is on and nothing else, so the bar
            # is drawn from elapsed time against the length of the recording.
            began = time.monotonic()
            job.update(status="preparing", progress=0.0, began=time.time(),
                       audio=core.duration(str(path)))

            def billed(secs):
                """The backend has started charging. Not the same as succeeding: a run
                that comes back empty, errors or is stopped is billed all the same, so
                the meter moves here and never moves back."""
                s = SESSIONS.get(token)
                if s and s["asr"] == asr:
                    s["spent"] += secs / 3600 * (s["rate"] or 0)
                    s["jobs"] += 1

            doc, flagged = core.transcribe(
                str(path), title=name, api_key=key, on_billed=billed,
                on_progress=lambda f: job.update(progress=f),
                should_stop=lambda: stopping(job_id), asr=asr,
                on_stage=lambda stage: job.update(status=stage),
            )
            dst = OUT / f"{job_id}.docx"
            doc.save(dst)
            latin = sum(1 for *_, reason in flagged if reason == "Latin script")
            job.update(status="done", progress=1.0, filename=Path(name).stem + ".docx",
                       flagged=len(flagged), latin_script=latin,
                       elapsed=round(time.monotonic() - began, 1),
                       low_confidence=len(flagged) - latin)
        finally:
            _lock.release()
    except core.Cancelled:
        job.update(status="cancelled", progress=0.0)
    except Exception as e:
        job.update(status="error", error=f"{type(e).__name__}: {e}")
    finally:
        path.unlink(missing_ok=True)


@app.get("/", response_class=HTMLResponse)
def index():
    return (HERE / "static" / "index.html").read_text()


# Getting a key is the one step this app cannot do for you, and both providers hide
# it behind a dashboard tour. These are the tours, written down. Real URLs rather
# than a query string, because they are meant to be linked to and indexed.
@app.get("/help/{slug}", response_class=HTMLResponse)
def help_page(slug: str):
    page = HERE / "static" / f"help-{slug}.html"
    # resolve() before the check: "/help/../../etc/passwd" is a path this would
    # otherwise happily read, and a 404 is the only correct answer to it.
    if not page.resolve().is_file() or page.resolve().parent != (HERE / "static").resolve():
        raise HTTPException(404, "no such guide")
    return page.read_text()


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    """Only does anything once this is served from a real domain, but it costs two
    lines and its absence is the kind of thing nobody notices until much later."""
    return "User-agent: *\nAllow: /\nDisallow: /download/\nDisallow: /status/\n"


@app.post("/upload")
def upload(bg: BackgroundTasks, file: UploadFile = File(...),
           asr: str = Form(None), session: str = Form(None)):
    asr = asr or core.ASR
    # before the file is written, not after: a backend this server has no key for is
    # a 400 the page can show, not an hour-long upload that dies in the worker.
    if asr not in BACKENDS:
        raise HTTPException(400, f"unknown backend {asr!r}")
    if not ready(asr, session):
        raise HTTPException(400, f"no {BACKENDS[asr]['name']} key for this session")
    # the key rides along as a worker argument, never in JOBS, which /jobs serves to
    # the page. A queued job keeps the copy it was handed if the session then ends.
    key = session_key(session, asr)
    job_id = uuid.uuid4().hex
    dst = UPLOADS / f"{job_id}{Path(file.filename).suffix}"
    with dst.open("wb") as f:
        shutil.copyfileobj(file.file, f)  # stream: hour-long files don't fit in RAM
    # everything not finished is ahead of this one, whatever step it is on. Listing
    # the stages by name here means every stage added later silently undercounts.
    queued = sum(1 for j in JOBS.values() if j["status"] not in DONE)
    JOBS[job_id] = {"status": "queued", "progress": 0.0, "name": file.filename,
                    "ahead": queued, "size": dst.stat().st_size, "asr": asr,
                    "created": time.time()}
    bg.add_task(run_job, job_id, dst, file.filename, asr, key, session)
    return {"job_id": job_id}


@app.post("/cancel/{job_id}")
def cancel(job_id: str):
    """Ask a job to stop. It unwinds at the next poll window, so this returns before
    the worker has actually noticed; the status poll is what confirms it."""
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job")
    if job["status"] not in DONE:
        job["cancel"] = True
    return {"ok": True}


@app.delete("/jobs/{job_id}")
def forget(job_id: str):
    """Clear one card. Cancels first if it is still running, so Clear never orphans
    a worker that goes on paying for a transcript nobody is waiting for."""
    job = JOBS.pop(job_id, None)
    if not job:
        raise HTTPException(404, "unknown job")
    job["cancel"] = True              # run_job reads JOBS.get(...) -> gone -> stop
    (OUT / f"{job_id}.docx").unlink(missing_ok=True)
    return {"ok": True}


@app.post("/jobs/clear")
def clear():
    """Clear every finished card. Leaves running jobs alone, since stopping work is Stop's
    job, and a Clear that silently killed a running transcription would be a trap."""
    gone = [k for k, v in JOBS.items() if v["status"] in DONE]
    for k in gone:
        JOBS.pop(k, None)
        (OUT / f"{k}.docx").unlink(missing_ok=True)
    return {"cleared": len(gone)}


@app.get("/jobs")
def jobs():
    """So a page that just loaded, or reloaded, can find work already running."""
    return [{"id": k, **v} for k, v in
            sorted(JOBS.items(), key=lambda kv: kv[1].get("created", 0))]


@app.get("/backends")
def backends(session: str = ""):
    return {"options": options(session), "wallet": _wallet(session)}


def _wallet(token):
    """What this session has spent. Neither backend publishes a balance endpoint, so
    "remaining" only exists if the user typed what their dashboard showed them."""
    s = SESSIONS.get(token)
    if not s:
        return None
    spent = round(s["spent"], 2)
    bal = s["balance"]
    return {"asr": s["asr"], "name": BACKENDS[s["asr"]]["name"], "spent": spent,
            "rate": s["rate"], "jobs": s["jobs"], "balance": bal,
            "remaining": None if bal is None else round(bal - spent, 2)}


@app.post("/session")
def open_session(key: str = Form(...), asr: str = Form(...),
                 balance: float = Form(None)):
    """Take a key for this session, after proving it works and before any audio moves.

    `balance` is optional and is only what the user says their dashboard shows;
    there is no API to read it.
    """
    if asr not in BACKENDS:
        raise HTTPException(400, f"unknown backend {asr!r}")
    key = key.strip()
    verify(asr, key)
    token = uuid.uuid4().hex
    SESSIONS[token] = {"asr": asr, "key": key, "spent": 0.0, "balance": balance,
                       "jobs": 0, "rate": BACKENDS[asr]["rate"]}
    return {"token": token, "wallet": _wallet(token), "options": options(token)}


@app.get("/session/{token}")
def session_state(token: str):
    if token not in SESSIONS:
        raise HTTPException(404, "no such session")
    return _wallet(token)


@app.delete("/session/{token}")
def end_session(token: str):
    """End it: the key leaves memory. Jobs already running keep the copy they were
    handed; killing those is Stop's job, and they are billed either way."""
    return {"ok": SESSIONS.pop(token, None) is not None}


@app.get("/status/{job_id}")
def status(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    # How long this job has actually been working, measured here. The page draws its
    # estimated progress from this, and a clock it computed itself would be wrong by
    # however far the browser's clock has drifted from the server's.
    began = job.get("began")
    return {**job, "running": round(time.time() - began, 1) if began else 0.0}


@app.get("/download/{job_id}")
def download(job_id: str):
    job = JOBS.get(job_id)
    if not job or job["status"] != "done":
        raise HTTPException(404, "not ready")
    return FileResponse(
        OUT / f"{job_id}.docx", filename=job["filename"],
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
