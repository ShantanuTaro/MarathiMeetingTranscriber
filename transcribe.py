"""Marathi meeting audio -> Word doc, with low-confidence words highlighted.

Core logic lives here so it can run standalone (CLI) without the web server:
    python transcribe.py meeting.m4a

Every backend is hosted. The local Whisper path was removed along with its 3 GB of
weights, its silence trimming, its chunking and its per-word confidence: it was the
only reason this needed numpy, mlx and a GPU, and it was slower than the API it was
competing with. What is left is an uploader, a poller and a document builder.
"""
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import zlib
from datetime import datetime
from types import SimpleNamespace as NS

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

CONF_THRESHOLD = float(os.getenv("CONF_THRESHOLD", "0.60"))
# Marathi renders a few letters (ल, श) differently from Hindi, so prefer a Marathi
# face. "Kohinoor Devanagari" is the macOS default; "Nirmala UI" the Windows one.
DEVANAGARI_FONT = os.getenv("DEVANAGARI_FONT", "ITF Devanagari Marathi")
LATIN = re.compile(r"[A-Za-z]")
# SPEAKERS=n if you know how many people were in the room. Clustering guesses worse
# than you do. 0 lets the backend decide.
SPEAKERS = int(os.getenv("SPEAKERS", "0"))

# ASR backend. Both are hosted and both bill per second of audio; neither key needs
# to be in the environment, because the portal takes one for the session.
# Sarvam is India-first and the best bet on Marathi, but its batch API returns
# chunk-level timestamps and NO per-word confidence, so the yellow "unsure" flag
# cannot exist on this path, so _sarvam_segments synthesises words at probability 1.0
# purely so the Latin-script check, which is a regex over the text, still runs.
# ElevenLabs Scribe does return a per-word logprob, so yellow means something there.
BACKENDS = ("sarvam", "elevenlabs")
ASR = os.getenv("ASR", "sarvam")
SARVAM_MODEL = os.getenv("SARVAM_MODEL", "saaras:v4")
SARVAM_LANG = os.getenv("SARVAM_LANG", "mr-IN")
# Diarization is a price tier on Sarvam, not a flag: it takes the rate from Rs30/hour
# to Rs45/hour, billed per second of audio whether the transcript comes back usable or
# not. Worth it on a meeting, wasted on a single voice, and it is half the reason a
# Rs100 trial grant disappears inside two hour-long recordings. SPEAKERS only means
# anything while this is on.
SARVAM_DIARIZE = os.getenv("SARVAM_DIARIZE", "1") != "0"
SARVAM_POLL_SEC = float(os.getenv("SARVAM_POLL_SEC", "10"))
# Vocabulary the model cannot get from the audio: place names, scheme acronyms, the
# people in the room. A bias list, NOT a prompt: it nudges the decoder's scoring
# toward these spellings and costs nothing when a term never comes up.
# Comma-separated, so a term may contain spaces but not a comma.
KEYTERMS = [t.strip() for t in os.getenv("KEYTERMS", "").split(",") if t.strip()]
SARVAM_MAX_KEYTERMS = 50      # saaras:v4's documented cap; more is a 400, not a trim
SARVAM_MAX_SEC = 7200         # documented cap of one file. Checked before the upload:
                              # otherwise it is a long upload followed by a rejection
SARVAM_RATE = 45 if SARVAM_DIARIZE else 30   # Rs/hour, docs.sarvam.ai pricing
SCRIBE_MODEL = os.getenv("SCRIBE_MODEL", "scribe_v2")
# ISO-639-1 or -3; "mar" is Marathi. Empty = auto-detect, which on code-switched
# meeting audio is a coin flip that costs you a whole English transcript.
SCRIBE_LANG = os.getenv("SCRIBE_LANG", "mar")


# ASR is only the default now, because the server lets a job pick its own backend, so
# anything that varies by backend has to be asked for one rather than read off a
# constant fixed at import.
def model_name(asr=None):
    return {"elevenlabs": SCRIBE_MODEL}.get(asr or ASR, SARVAM_MODEL)


# Sarvam reports no per-word confidence, so on that backend the yellow highlight
# can never fire. The document must say so rather than quietly print "0 words below
# 60% confidence", which reads as a clean bill of health for a check that never ran.
def has_confidence(asr=None):
    return (asr or ASR) != "sarvam"


MODEL_NAME = model_name()          # the default backend, for the CLI and the banner
HAS_CONFIDENCE = has_confidence()

MAX_REVIEW_ROWS = 200
GAP_SEC = 1.5        # a pause this long starts a new paragraph
PARA_SEC = 30        # ... and so does this much unbroken speech

GREY = RGBColor(0x7F, 0x7F, 0x7F)


class Cancelled(Exception):
    """The caller asked to stop. Raised rather than returned, so it unwinds out of
    the poll loop instead of waiting for the current request to come back."""


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def ts(seconds):
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def reason(w, threshold):
    """Why this word needs a human, or None. The single source of truth for both
    the highlight in the body and the row in the review table."""
    bare = w.word.strip()
    if not bare:
        return None
    if LATIN.search(bare):          # English that didn't come out in Devanagari
        return "Latin script"
    if w.probability < threshold:
        return f"{w.probability:.0%} sure"
    return None


# ---------------------------------------------------------------- document ---

def _page_setup(doc):
    """A4 with room in the margin for a pen, and a Devanagari face throughout."""
    for s in doc.sections:
        s.page_height, s.page_width = Cm(29.7), Cm(21.0)
        s.top_margin = s.bottom_margin = Cm(2.0)
        s.left_margin, s.right_margin = Cm(2.4), Cm(2.4)

    style = doc.styles["Normal"]
    style.font.size = Pt(11)
    # python-docx only sets the latin font; Devanagari needs w:cs too.
    rfonts = style.element.get_or_add_rPr().get_or_add_rFonts()
    for attr in ("w:ascii", "w:hAnsi", "w:cs"):
        rfonts.set(qn(attr), DEVANAGARI_FONT)
    pf = style.paragraph_format
    pf.space_after, pf.line_spacing = Pt(10), 1.3   # Devanagari needs the leading

    # Word's stock headings are a blue Calibri Light that fights the body face.
    for name, size in (("Heading 1", 20), ("Heading 2", 12)):
        h = doc.styles[name]
        h.font.name, h.font.size = DEVANAGARI_FONT, Pt(size)
        h.font.color.rgb, h.font.bold = RGBColor(0, 0, 0), True
        h.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:cs"), DEVANAGARI_FONT)
    doc.styles["Heading 2"].paragraph_format.space_before = Pt(18)


def _footer(doc, note):
    """Field codes, because Word computes page numbers, not us."""
    p = doc.sections[0].footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(note + "   ·   ")
    run.font.size, run.font.color.rgb = Pt(8), GREY
    num = p.add_run()
    num.font.size, num.font.color.rgb = Pt(8), GREY
    begin, instr, end = (OxmlElement("w:fldChar"), OxmlElement("w:instrText"),
                         OxmlElement("w:fldChar"))
    begin.set(qn("w:fldCharType"), "begin")
    instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    end.set(qn("w:fldCharType"), "end")
    for el in (begin, instr, end):
        num._r.append(el)


def _meta_block(doc, rows):
    """Borderless label/value pairs. A table, so the values line up."""
    t = doc.add_table(rows=0, cols=2)
    t.autofit = False
    for label, value in rows:
        cells = t.add_row().cells
        cells[0].width, cells[1].width = Cm(3.6), Cm(12.4)
        r = cells[0].paragraphs[0].add_run(label.upper())
        r.font.size, r.font.color.rgb, r.bold = Pt(8), GREY, True
        v = cells[1].paragraphs[0].add_run(value)
        v.font.size = Pt(9)
    for row in t.rows:                      # tight rows; this is a header, not content
        for cell in row.cells:
            cell.paragraphs[0].paragraph_format.space_after = Pt(1)
    return t


def _review_table(doc, flagged, threshold, asr=None):
    doc.add_heading("Review checklist", level=2)
    p = doc.add_paragraph()
    low = sum(1 for *_, r in flagged if r != "Latin script")
    latin = len(flagged) - low
    conf = (f"{low} below {threshold:.0%} confidence (yellow), "
            if has_confidence(asr) else "")
    r = p.add_run(f"{len(flagged)} word(s) need a look: {conf}{latin} still in "
                  f"Latin script (turquoise). Each is highlighted in the transcript.")
    r.font.size, r.font.color.rgb = Pt(9), GREY
    if not has_confidence(asr):
        note = p.add_run(f"  {model_name(asr)} reports no per-word confidence, so this "
                         "checklist cannot flag words the model was unsure of. "
                         "only ones left in Latin script. Read the whole transcript.")
        note.font.size, note.font.color.rgb, note.bold = Pt(9), GREY, True

    if not flagged:
        doc.add_paragraph("Nothing flagged. Spot-check anyway, since the model is "
                          "sometimes confidently wrong.")
        return

    shown = flagged[:MAX_REVIEW_ROWS]
    table = doc.add_table(rows=1, cols=5)
    table.style = "Table Grid"
    widths = (Cm(1.0), Cm(1.9), Cm(4.6), Cm(2.6), Cm(6.1))
    for cell, text, w in zip(table.rows[0].cells,
                             ("#", "Time", "Heard as", "Why", "Correction"), widths):
        cell.width = w
        run = cell.paragraphs[0].add_run(text.upper())
        run.bold, run.font.size, run.font.color.rgb = True, Pt(8), GREY

    for i, (stamp, word, why) in enumerate(shown, 1):
        cells = table.add_row().cells
        for cell, text, w in zip(cells, (str(i), stamp, word, why, ""), widths):
            cell.width = w
            run = cell.paragraphs[0].add_run(text)
            run.font.size = Pt(10)
            if text in (str(i), stamp, why):
                run.font.size, run.font.color.rgb = Pt(9), GREY

    if len(flagged) > len(shown):
        p = doc.add_paragraph(f"... and {len(flagged) - len(shown)} more, "
                              f"highlighted in the transcript below.")
        p.runs[0].font.size, p.runs[0].font.color.rgb = Pt(9), GREY


def looping(text):
    """A window Whisper got stuck in. Same test it uses internally to trigger a
    retry (compression ratio); we re-run it because after the last temperature it
    keeps the loop anyway rather than dropping it."""
    t = text.strip()
    return len(t) > 40 and len(t) / len(zlib.compress(t.encode())) > 2.6


def _spk(seg):
    return getattr(seg, "speaker", None)


def _group(segs):
    """Whisper emits a segment every few seconds; an hour of those is 600 stubby
    paragraphs. Merge until a real pause or half a minute of speech."""
    batch = []
    for seg in segs:
        if batch and (seg.start - batch[-1].end > GAP_SEC
                      or seg.end - batch[0].start > PARA_SEC
                      or _spk(seg) != _spk(batch[-1])):   # never merge two voices
            yield batch
            batch = []
        batch.append(seg)
    if batch:
        yield batch


def _body(doc, segs, threshold):
    doc.add_heading("Transcript", level=2)
    for batch in _group(segs):
        p = doc.add_paragraph()
        stamp = p.add_run(ts(batch[0].start) + " ")  # words carry a leading space
        stamp.font.size, stamp.font.color.rgb, stamp.bold = Pt(8), GREY, True
        if _spk(batch[0]) is not None:
            who = p.add_run(f"Speaker {_spk(batch[0]) + 1}  ")
            who.font.size, who.bold = Pt(9), True

        for seg in batch:
            if not getattr(seg, "words", None):      # no word timings for this one
                p.add_run(" " + seg.text.strip())
                continue
            for w in seg.words:
                run = p.add_run(w.word)
                why = reason(w, threshold)
                if why == "Latin script":
                    run.font.highlight_color = WD_COLOR_INDEX.TURQUOISE
                elif why:
                    run.font.highlight_color = WD_COLOR_INDEX.YELLOW


def build_docx(segments, threshold=CONF_THRESHOLD, title="Meeting transcript",
               total_duration=None, source=None, asr=None):
    """Consume ASR segments -> (Document, flagged).

    flagged is [(timestamp, word, why)] for every word a human should check.
    """
    segs, looped = [], 0
    for seg in segments:
        if looping(seg.text):
            looped += 1              # pure decoder noise; keeping it buries the rest
        else:
            segs.append(seg)

    flagged = [(ts(w.start), w.word.strip(), why)
               for seg in segs for w in getattr(seg, "words", None) or ()
               if (why := reason(w, threshold))]

    doc = Document()
    _page_setup(doc)
    _footer(doc, "Draft: verify every highlighted word")

    doc.add_heading(title, level=1)
    spoken = sum(seg.end - seg.start for seg in segs)
    _meta_block(doc, [
        ("Source", source or title),
        ("Duration", ts(total_duration) if total_duration else ts(spoken)),
        ("Speech", f"{ts(spoken)} of audible speech"),
        ("Model", model_name(asr)),
        ("Dropped", f"{looped} garbled stretch(es)" if looped else "none"),
        ("Speakers", str(len({_spk(s) for s in segs} - {None}) or "not detected")),
        ("Generated", datetime.now().strftime("%d %b %Y, %H:%M")),
    ])
    # On page one, above everything, because this document leaves here and gets
    # forwarded, and whoever opens it third has no idea a machine wrote it.
    note = doc.add_paragraph()
    warn = note.add_run(
        "Machine-generated draft. It has not been verified by a human, it is not a "
        "record of proceedings, and speaker labels are guesses. Check every "
        "highlighted word, and spot-check the rest, before relying on any of it.")
    warn.font.size, warn.font.color.rgb, warn.italic = Pt(9), GREY, True
    doc.add_paragraph()

    _review_table(doc, flagged, threshold, asr)
    doc.add_page_break()
    _body(doc, segs, threshold)
    return doc, flagged


# --------------------------------------------------------------------- asr ---


def _mime(path):
    """Content-Type for the upload, from what the bytes actually are.

    Not from the extension: this pipeline's first real recording was raw AAC named
    .mp3, and not from mimetypes either, which calls .m4a "audio/mp4a-latm". ffprobe
    is already a hard dependency and is the only one of the three that cannot be
    lied to. Getting this wrong is silent: Sarvam decodes the blob by its mime and
    returns an empty transcript rather than an error.
    """
    fmt = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=format_name", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.strip()
    # csv quotes the whole value when it holds commas, and this one always does:
    # "mov,mp4,m4a,3gp,3g2,mj2". Strip the quote or every lookup misses.
    fmt = fmt.strip('"').split(",")[0]
    return {"mov": "audio/mp4", "mp4": "audio/mp4", "m4a": "audio/mp4",
            "aac": "audio/aac", "mp3": "audio/mpeg", "wav": "audio/wav",
            "ogg": "audio/ogg", "flac": "audio/flac", "matroska": "audio/webm",
            "webm": "audio/webm", "amr": "audio/amr"}.get(fmt, "audio/mpeg")


def duration(path):
    """Seconds of audio, per ffprobe. The hosted backends bill on this number, so it
    is worth printing before the job starts rather than working it out afterwards
    from a credit balance that has already moved."""
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0          # unknown duration is not a reason to refuse the job


def _sarvam(path, on_progress=None, should_stop=None, key=None, on_billed=None,
            on_stage=None):
    """Sarvam takes 2 hours per file, so a longer recording is cut into equal parts,
    sent one at a time, and stitched back onto a single clock.

    Speaker ids are clustered per job, so "speaker 0" in part two is not the person
    it was in part one. They are renumbered across parts rather than merged: a
    document where one label means two people is worse than one with too many.
    """
    secs = duration(path)
    with tempfile.TemporaryDirectory() as tmp:
        n = math.ceil(secs / SARVAM_MAX_SEC) or 1
        if on_stage:
            on_stage("splitting" if n > 1 else "compressing")
        parts = _split_audio(path, secs, tmp)
        if n > 1:
            _log(f"sarvam: {ts(secs)} is over the {ts(SARVAM_MAX_SEC)} limit for one "
                 f"file, so sending it as {len(parts)} parts")
        segs, speakers = [], 0
        for i, (part, offset) in enumerate(parts):
            # the stage still names the step; the part rides along after a separator
            # so the page can show both without a second field to thread through.
            # One part is not "part 1 of 1", it is just the file.
            tail = f" \u00b7 part {i + 1} of {len(parts)}" if len(parts) > 1 else ""
            stage = (lambda step, t=tail: on_stage(step + t)) if on_stage else None
            here = _sarvam_one(part, None, should_stop, key, on_billed, stage)
            for seg in here:
                seg.start += offset
                seg.end += offset
                if seg.speaker is not None:
                    seg.speaker += speakers
                for w in seg.words:
                    w.start += offset
                    w.end += offset
                segs.append(seg)
            speakers += len({s.speaker for s in here if s.speaker is not None})
            if on_progress:
                on_progress((i + 1) / len(parts))
        return segs


def _encode(path, out, at=0.0, span=None):
    """One span of `path` as 64k mono AAC. Returns how long the result really is.

    Every upload goes through this, not only the ones over a length cap. Speech at
    64k mono is indistinguishable to a recogniser from the 256k stereo it usually
    arrives as, and the size difference is the whole job: a 269 MB source leaves as
    about 35 MB, which is less to upload, less to hold in memory while uploading,
    and one container for every codec that came in.

    ponytail: costs a decode pass, which on a throttled host is minutes for an
    hour of audio. Worth it there precisely because the upload is the slow part.
    Stream-copy instead if you ever run this next to the API.
    """
    # -ss goes before -i so ffmpeg seeks by index instead of decoding up to the
    # mark, which on a two-hour file is the difference between instant and minutes.
    cut = ["-t", f"{span:.3f}"] if span else []
    subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{at:.3f}", *cut,
                    "-i", path, "-vn", "-ac", "1", "-c:a", "aac", "-b:a", "64k",
                    out], check=True)
    return duration(out)


def _split_audio(path, secs, into):
    """Cut into as few equal parts as the cap allows, one part if it fits. Equal,
    not cap-sized: a 2h18m recording splits into two of 1h09m rather than a 2h part
    and an 18m stub.

    ponytail: cuts on the clock, so a word at each seam may be damaged. Cutting on
    silence means decoding the whole file first, and there is nothing downstream
    that would use the decoded audio.
    """
    n = math.ceil(secs / SARVAM_MAX_SEC) or 1
    span = secs / n
    parts, at = [], 0.0
    for i in range(n):
        out = os.path.join(into, f"part{i:03d}.m4a")
        # the last part takes everything left rather than a measured span, so
        # nothing falls off the end; a single part is all "last".
        got = _encode(path, out, at, span if i < n - 1 else None)
        parts.append((out, at))
        # measured, not assumed: the seek lands on a frame rather than the mark, and
        # starting the next part where this one really ended cannot drift.
        at += got
    return parts


def _sarvam_one(path, on_progress=None, should_stop=None, key=None, on_billed=None,
                on_stage=None):
    """Sarvam batch: create job -> presigned upload -> start -> poll -> download.

    The synchronous /speech-to-text endpoint caps at 30 seconds of audio, which no
    meeting is, so the job API is the only door, and diarization and timestamps
    are batch-only anyway. Audio does not go through Sarvam on the way in: the job
    hands back a presigned Azure blob URL and the bytes go straight there.

    ponytail: reads the file into memory for the PUT, because a streamed body goes
    out chunked and Azure rejects that. Sarvam caps a file at 2 hours, so this is
    bounded at roughly a 60 MB m4a; chunk the upload if that ever stops being true.
    """
    import requests

    key = key or os.getenv("SARVAM_API_KEY")
    if not key:
        raise RuntimeError("ASR=sarvam needs SARVAM_API_KEY in the environment")
    head = {"api-subscription-key": key}
    base = "https://api.sarvam.ai/speech-to-text/job/v1"

    def call(method, url, **kw):
        if should_stop and should_stop():
            raise Cancelled
        r = requests.request(method, url, headers=head, timeout=(30, 300), **kw)
        if not r.ok:                 # the body names the field it disliked; keep it
            raise RuntimeError(f"Sarvam {r.status_code} on {url}: {r.text[:500]}")
        return r

    hours = duration(path) / 3600
    rate = SARVAM_RATE
    _log(f"sarvam: {ts(hours * 3600)} of audio at ~Rs{rate}/hour = ~Rs{hours * rate:.2f}"
         + ("" if SARVAM_DIARIZE else " (diarization off)")
         + ". Billed per second from the moment the job starts, transcript or not.")

    params = {"model": SARVAM_MODEL, "with_diarization": SARVAM_DIARIZE,
              "with_timestamps": True}
    if KEYTERMS:
        params["keyterms"] = KEYTERMS[:SARVAM_MAX_KEYTERMS]
        if len(KEYTERMS) > SARVAM_MAX_KEYTERMS:
            _log(f"KEYTERMS: sending the first {SARVAM_MAX_KEYTERMS} of "
                 f"{len(KEYTERMS)}. Put the ones the model gets wrong first")
    if SARVAM_LANG:
        params["language_code"] = SARVAM_LANG
    if SPEAKERS and SARVAM_DIARIZE:   # num_speakers is a diarization parameter, and
        params["num_speakers"] = SPEAKERS   # without one it is a 400, not a hint
    job = call("POST", base, json={"job_parameters": params}).json()["job_id"]

    name = os.path.basename(path)
    links = call("POST", f"{base}/upload-files",
                 json={"job_id": job, "files": [name]}).json()
    _log(f"sarvam job {job}: uploading {name} ...")
    if on_stage:
        on_stage("uploading")
    with open(path, "rb") as f:
        r = requests.put(links["upload_urls"][name]["file_url"], data=f.read(),
                         # Azure's own API, not Sarvam's: no key, and x-ms-blob-type
                         # is mandatory. Content-Type matters just as much: Sarvam
                         # decodes by the blob's mime, and with none set it assumes
                         # WAV, hands an hour of AAC to a WAV parser, and returns an
                         # empty transcript with the job still marked Success.
                         headers={"x-ms-blob-type": "BlockBlob",
                                  "Content-Type": _mime(path)}, timeout=(30, 300))
    if not r.ok:
        raise RuntimeError(f"upload to blob storage failed: {r.status_code} {r.text[:300]}")
    call("POST", f"{base}/{job}/start")
    if on_stage:
        on_stage("waiting")
    if on_billed:
        # the exact moment the meter starts: past here the audio is paid for whatever
        # happens next: an empty transcript, an error, or Stop.
        on_billed(hours * 3600)

    while True:
        if should_stop and should_stop():
            # Sarvam exposes no way to cancel a started job: it runs to completion on
            # their side and the audio is billed whichever way this process exits. Say
            # so, or the next run looks free and the balance disagrees.
            _log(f"sarvam job {job} stopped here, but it is already running and will "
                 f"be billed in full, ~Rs{hours * rate:.2f}")
            raise Cancelled
        time.sleep(SARVAM_POLL_SEC)   # nothing is ever done on the first check
        st = call("GET", f"{base}/{job}/status").json()
        if st.get("job_state") in ("Completed", "Failed"):
            break
    details = st.get("job_details") or ()
    bad = [d for d in details if d.get("state") != "Success"]
    if st.get("job_state") != "Completed" or bad or not details:
        # a per-file error lives in job_details even when the job says Completed
        why = (bad[0].get("error_message") if bad else None) or st.get("error_message")
        raise RuntimeError(f"Sarvam job {job} {st.get('job_state')}: {why or st}")

    out = details[0]["outputs"][0]["file_name"]
    url = call("POST", f"{base}/download-files",
               json={"job_id": job, "files": [out]}).json()["download_urls"][out]["file_url"]
    result = call("GET", url).json()
    segs = _sarvam_segments(result)
    if not segs:
        # Success with nothing in it is the shape a mis-detected container takes.
        # Never let that reach build_docx: a blank document reads like a silent
        # recording, and the difference matters more than an hour of compute.
        raise RuntimeError(
            f"Sarvam job {job} returned an empty transcript for {name} "
            f"(it decoded the upload as {result.get('audio_mime')}). If that is not "
            f"the real format, remux the file and try again. Sarvam billed this "
            f"run in full (~Rs{hours * rate:.2f}); it counts as a Success upstream.")
    if on_progress:
        on_progress(1.0)
    return segs


def _sarvam_segments(data):
    """Sarvam batch output -> the same segment shape the document is built on.

    Two shapes come back: diarized_transcript.entries when with_diarization was
    accepted, else timestamps.{chunks,start_time_seconds,end_time_seconds} as three
    parallel lists. Prefer the diarized one: it is the same chunking plus a voice.

    There is no per-word anything here, so each chunk's words are split out and
    their timings linearly interpolated across the chunk. They are approximations
    and only ever used to put a timestamp on a review row; probability is 1.0
    because Sarvam does not report one, which makes CONF_THRESHOLD a no-op and
    leaves the Latin-script check as the only thing that can flag a word.
    """
    entries = (data.get("diarized_transcript") or {}).get("entries")
    if entries:
        rows = [(e.get("transcript") or "", e.get("start_time_seconds") or 0.0,
                 e.get("end_time_seconds") or 0.0, str(e.get("speaker_id") or "0"))
                for e in entries]
    else:
        t = data.get("timestamps") or {}
        # the key is "words" in the live response and "chunks" in the docs, and
        # either way each element is a whole sentence. Accept both.
        rows = [(c, a, b, None) for c, a, b in
                zip(t.get("words") or t.get("chunks") or (),
                    t.get("start_time_seconds") or (),
                    t.get("end_time_seconds") or ())]
        if not rows and (data.get("transcript") or "").strip():
            rows = [(data["transcript"], 0.0, 0.0, None)]   # with_timestamps refused

    ids, segs = {}, []
    for text, start, end, spk in rows:
        if not text.strip():
            continue
        words, at = [], start
        parts = text.split()
        span = max(end - start, 0.0) / max(len(parts), 1)
        for part in parts:
            words.append(NS(word=" " + part, probability=1.0,   # unreported, not sure
                            start=at, end=at + span))
            at += span
        segs.append(NS(start=start, end=max(end, start), text=" " + " ".join(parts),
                       speaker=None if spk is None else ids.setdefault(spk, len(ids)),
                       words=words))
    return segs


def _scribe_segments(data):
    """ElevenLabs word list -> the same segment shape the document is built on.

    Scribe emits one flat stream of tokens, no segments, so cut it where _group
    would anyway: a real pause, or a change of voice. Spacing is its own token
    type; fold it into the next word because _body prints runs back to back and
    relies on each one carrying its own leading space, as Whisper's do.
    """
    ids, words, pending = {}, [], ""
    for w in data.get("words", ()):
        if w["type"] == "spacing":
            pending += w.get("text") or " "
            continue
        if w["type"] != "word":       # audio_event: laughter, footsteps. Not speech.
            continue
        spk = w.get("speaker_id")
        words.append(NS(
            word=(pending or " ") + w["text"],
            # logprob, not a probability, so exp() puts it back on CONF_THRESHOLD's scale
            probability=float(math.exp(w.get("logprob", 0.0))),
            start=w["start"], end=w["end"],
            speaker=None if spk is None else ids.setdefault(spk, len(ids))))
        pending = ""

    segs, batch = [], []

    def flush():
        if batch:
            segs.append(NS(start=batch[0].start, end=batch[-1].end,
                           text="".join(x.word for x in batch),
                           speaker=batch[0].speaker, words=list(batch)))

    for w in words:
        if batch and (w.start - batch[-1].end > GAP_SEC
                      or w.speaker != batch[-1].speaker):
            flush()
            batch.clear()
        batch.append(w)
    flush()
    return segs


def _scribe(path, on_progress=None, should_stop=None, key=None, on_billed=None,
            on_stage=None):
    """One POST for the whole file, so no chunking and no remapping: the timings come
    back in the original recording's own clock.

    ponytail: blocking request, so progress is 0 then 1. The API exposes no
    intermediate state; switch to its webhook flow if a bar matters on long files.
    """
    import requests

    key = key or os.getenv("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("ASR=elevenlabs needs ELEVENLABS_API_KEY in the environment")
    if should_stop and should_stop():
        raise Cancelled
    form = {"model_id": SCRIBE_MODEL, "diarize": "true",
            "timestamps_granularity": "word"}
    if KEYTERMS:
        # multipart has no list type; ElevenLabs reads this field as JSON. Billed
        # at +20% when present, so it is off unless KEYTERMS is actually set.
        form["keyterms"] = json.dumps(KEYTERMS, ensure_ascii=False)
    if SCRIBE_LANG:
        form["language_code"] = SCRIBE_LANG
    if SPEAKERS:
        form["num_speakers"] = str(SPEAKERS)
    # The charge lands with the request, not with a usable transcript: an empty
    # result costs the same. Say so before the bytes go out, not after.
    if on_billed:
        on_billed(duration(path))
    with tempfile.TemporaryDirectory() as tmp:
        # Scribe's cap is 10 hours, so nothing here needs splitting. It is still
        # re-encoded: requests assembles a multipart body in memory, so the size of
        # the file is the size of the upload's memory footprint.
        if on_stage:
            on_stage("compressing")
        small = os.path.join(tmp, "audio.m4a")
        _encode(path, small)
        if should_stop and should_stop():
            raise Cancelled
        if on_stage:
            on_stage("uploading")
        _log(f"uploading {os.path.basename(path)} to {SCRIBE_MODEL} ...")
        with open(small, "rb") as f:
            r = requests.post(
                "https://api.elevenlabs.io/v1/speech-to-text",
                headers={"xi-api-key": key}, data=form,
                files={"file": (os.path.basename(path), f)},
                # One POST covers upload AND transcription, so the read timeout has
                # to outlast the job, but not by an hour. An hour-long ceiling is
                # how a dead socket became a worker blocked until a server restart.
                timeout=(30, 1800))
    if not r.ok:                     # the body names the field it disliked; keep it
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:500]}")
    segs = _scribe_segments(r.json())
    if on_progress:
        on_progress(1.0)
    return segs


def transcribe(path, on_progress=None, should_stop=None, asr=None,
               api_key=None, on_billed=None, on_stage=None, **kw):
    """api_key from the caller wins over the environment: the portal takes one for the
    session, so trying a hosted backend does not mean restarting the server. on_billed
    fires when a paid backend starts charging, which is not when the job succeeds, and
    on_stage names the step a hosted job is on, since it has no progress bar to show."""
    asr = asr or ASR
    if asr == "sarvam":
        segs = _sarvam(path, on_progress, should_stop, api_key, on_billed, on_stage)
    elif asr == "elevenlabs":
        segs = _scribe(path, on_progress, should_stop, api_key, on_billed, on_stage)
    else:
        raise ValueError(f"unknown ASR backend {asr!r}. Try {', '.join(BACKENDS)}")
    return build_docx(segs, total_duration=segs[-1].end if segs else None,
                      asr=asr, **kw)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python transcribe.py <audio-file> [output.docx]")
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(src)[0] + ".docx"
    print(f"sending to {ASR} / {MODEL_NAME} ...")
    doc, flagged = transcribe(
        src,
        title=os.path.basename(src),
        on_stage=lambda step: print(f"\r{step:<28}", end="", flush=True),
        on_progress=lambda f: print(f"\r{f:.0%}", end="", flush=True),
    )
    doc.save(dst)
    print(f"\n{dst}  ({len(flagged)} words flagged for review)")
