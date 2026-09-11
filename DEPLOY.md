# Deploying this

## It cannot go on Vercel

Not a configuration problem. Vercel runs Python as serverless functions, and this is
a long-lived server with a queue:

| | |
|---|---|
| **Request bodies cap at 4.5 MB** | Meeting recordings here are 74 to 269 MB. The upload is rejected before any code runs. This one alone is fatal |
| **No `ffmpeg` or `ffprobe`** | Duration, real container detection and the two-hour split all shell out to them |
| **Background work is killed when the response returns** | `run_job` starts *after* `/upload` replies, so the transcription would be terminated immediately |
| **`JOBS` and `SESSIONS` live in memory** | Separate invocations are separate instances. A status poll would usually reach one that never heard of the job |
| **Function timeout** | 60s on Hobby, 300s on Pro. An hour of audio takes about six minutes at Sarvam |

Netlify Functions, Cloudflare Workers and Lambda behind API Gateway fail for the same
reasons. What this needs is a container with a process that stays alive.

## Render, on the free plan

The free plan is real, needs no payment card, and runs Docker. A `Dockerfile` and a
`render.yaml` are in the repo.

1. Push to GitHub.
2. Render dashboard, **New > Blueprint**, point it at the repo. It reads
   `render.yaml`.
3. Set **`SITE_URL`** to the URL Render assigns (or your own domain) and redeploy.
   Leave both API key variables unset: the page takes a key for the session, and a key
   in the environment is a key every visitor gets to spend.

What the free plan gives, and what it costs you:

- **512 MB RAM, 0.1 CPU.** Enough, with one caveat below.
- **Spins down after 15 minutes with no traffic**, and takes about a minute to wake.
  The first visitor after a quiet spell waits. A job in flight is safe while the page
  is polling, which it does every two seconds; close the tab and walk away and the
  instance can spin down mid-job.
- **750 instance hours per month per workspace**, which covers one service.
- **No persistent disk.** Finished `.docx` files live in the container filesystem and
  are lost on redeploy, restart or spin-down. Jobs are in memory anyway, so this is
  consistent rather than surprising. The card counts down the thirty minutes a
  document is kept (`DOC_TTL_SEC`) and a restart takes it sooner than that, so
  **download it when it is ready.**

512 MB is enough because every upload is re-encoded to 64k mono AAC before it is sent:
the largest thing held in memory is one compressed part, about 34 MB, not the original
file. The trade on 0.1 CPU is that encoding an hour of audio takes minutes, which is
time the slower upload would have cost anyway.

### Other hosts

Fly.io removed its free tier in 2024, and Koyeb closed its free plan to new signups
after the Mistral acquisition in 2026. Hugging Face Spaces now requires PRO for Docker
Spaces on a personal account. Railway is trial credit then paid. Oracle Cloud's Always
Free ARM VM is genuinely free and genuinely more setup.

Any of them, and any VM, runs the `Dockerfile` unchanged.

## What changes when other people can reach it

**Jobs are scoped to the browser that made them.** The page mints a `client` id into
`localStorage` and sends it with every job call. `/jobs` returns only that browser's
work, and `/status`, `/download`, `/cancel` and the two delete routes answer **404**
for anybody else's job, so a stranger cannot even confirm an id exists. Without this,
a shared deployment hands every visitor the filenames of every meeting anybody has
transcribed, and the transcripts to go with them. There is a test for it in
`test_transcribe.py`.

It is isolation, not authentication. It stops visitors seeing each other. It does not
stop somebody who can read another person's `localStorage`, and it is not a login.
If you need real accounts, this is the seam to put them on.

**Do not put API keys in the environment.** On a laptop `SARVAM_API_KEY` is a
convenience. On a public URL it is your key, spendable by anyone who finds the page,
billed to you, with no cap. Leave it unset so every visitor brings their own; the page
is built for exactly that.

**Uploads pass through the box.** A recording is written to the container's disk,
sent to the provider, and deleted when the transcript is written. It is not encrypted
at rest in between. Your hosting provider is in that path.
