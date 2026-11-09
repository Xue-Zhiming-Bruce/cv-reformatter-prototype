# Local runtime image for the CV Reformatter backend (API, migrations, worker).
# Verified on 2026-11-09, linux/arm64, base python:3.12-slim-bookworm (tag only,
# not digest-pinned; the image reports Python 3.12.16) · Debian bookworm
# chromium · dependency versions pinned in docker/constraints.txt, frozen from
# the host virtualenv at Python 3.12.12 · product fonts are the woff2 assets
# bundled under app/pipeline/assets/fonts and embedded into the HTML by
# app/pipeline/rendering.py.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Chromium is the PDF renderer (app/generation/chrome_html_to_pdf.py). The
# fonts-* packages only supply fallback glyphs: Lato/Charter/Roboto come from
# the bundled woff2 assets, not from the system font set.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      chromium \
      curl \
      fontconfig \
      fonts-dejavu-core \
      fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/chromium

WORKDIR /app

COPY docker/constraints.txt ./docker/constraints.txt
COPY pyproject.toml README.md ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./

# Editable install keeps app.__file__ under /app so package-relative assets and
# the pipeline's working-directory paths resolve to the mounted artifact root.
RUN pip install --no-cache-dir -c docker/constraints.txt -e .

# The artifact tree is created owned by the runtime user so a fresh named
# volume inherits that ownership; API and worker therefore share one uid view.
RUN useradd --create-home --uid 10001 appuser \
 && mkdir -p /app/data/generated_outputs /app/data/local /app/data/design_cache \
 && chown -R appuser:appuser /app/data

ENV GENERATED_OUTPUTS_DIR=/app/data/generated_outputs \
    HOME=/home/appuser

USER appuser

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
