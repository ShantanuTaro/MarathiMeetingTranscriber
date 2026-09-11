# Render, Railway, Fly, a VM: anywhere that runs a container and keeps the process
# alive. Not a serverless platform, and see DEPLOY in the README for why.
FROM python:3.12-slim

# The one system dependency. ffmpeg reads how long an upload is, what container it
# really is, and cuts anything over a backend's per-file cap into parts.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Requirements first so a code change does not reinstall the world.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render and most others hand the port over in $PORT. 8000 is for `docker run` here.
ENV PORT=8000
EXPOSE 8000

# One worker, deliberately. Jobs and sessions live in this process's memory, so a
# second worker would be a second server that has never heard of your upload.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT} --workers 1"]
