# CV Reformatter MVP

Prototype for a CV reformat SaaS product. This local MVP turns a candidate's DOCX or text-based PDF resume into validated candidate profile JSON, flags missing recruiter-critical information, and saves debug artifacts.

## Database And Hosting Direction

- PostgreSQL is the target MVP database and should run locally during MVP development and testing.
- The existing SQLite artifact index is transitional and should not be expanded as the primary persistence model.
- Local document and debug artifacts remain on the local filesystem during the MVP.
- After the local MVP is complete, the selected initial hosting provider is DigitalOcean:
  - App Platform for the containerized FastAPI backend and frontend hosting;
  - Managed PostgreSQL for separate staging and production databases;
  - Spaces for private resume, extracted-text, DOCX, PDF, and debug artifacts.

DigitalOcean deployment begins only after the end-to-end synthetic demo and local PostgreSQL persistence path work successfully. Real candidate data must not be hosted before authentication, authorization, private object access, retention/deletion behavior, and backup/restore procedures are in place.

This first milestone intentionally implements only:

- DOCX and PDF resume ingestion
- Plain text, paragraph, and table extraction
- LLM-driven `CandidateProfile` JSON validation
- Missing-field detection
- Backend generation from reviewed `CandidateProfile` JSON to DOCX/PDF
- A CLI demo using either a mock extractor or a configured LLM provider

The frontend review/export flow is owned separately and should call the backend generation API.

## Current Status

Implemented:

- DOCX text extraction
- Text-based PDF text extraction
- Strict `CandidateProfile` Pydantic validation
- OpenAI / Anthropic LLM client abstraction
- Stage-specific OpenAI / Anthropic API key and model settings
- Offline mock extractor for tests and demos
- Missing-field detection
- Draft follow-up message generation
- Client-facing render context with disclosure and blind-profile behavior
- Controlled MVP DOCX rendering
- LibreOffice/`soffice` DOCX-to-PDF export
- FastAPI generation and download endpoints

Still scaffolded:

- Streamlit upload/review/download UI

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

To use a real LLM, set `LLM_PROVIDER` and the matching API key/model in `.env`.
For candidate extraction, stage-specific settings override the generic fallback:

```env
LLM_PROVIDER=openai

OPENAI_EXTRACT_API_KEY=
OPENAI_EXTRACT_MODEL=gpt-4.1-mini

# Optional generic fallback used when a stage-specific value is absent.
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini
```

For Anthropic, use the matching `ANTHROPIC_EXTRACT_API_KEY` and
`ANTHROPIC_EXTRACT_MODEL` variables.

## Run Tests

```bash
pytest
```

Optional local dataset tests run when `tests/local_datasets/` contains local
resume datasets. They verify DOCX dataset extraction/rendering and PDF text
extraction. Do not commit real resume datasets, API keys, generated candidate
outputs, or generated test reports.

## Run The Local Demo

The demo creates a synthetic DOCX resume if you do not pass one:

```bash
python scripts/run_demo.py --mock
```

To process your own synthetic DOCX:

```bash
python scripts/run_demo.py --input data/input_samples/sample_resume.docx --mock
```

Artifacts are written to `data/generated_outputs/`:

- `raw_extracted_text.txt`
- `candidate_profile.json`
- `missing_fields.json`
- `followup_email.txt`

Generated outputs and local candidate files are gitignored by default. Use synthetic resumes only.

## Real LLM Mode

```bash
python scripts/run_demo.py --input path/to/synthetic_resume.docx
```

Omit `--mock` to use the configured real LLM provider. Supported provider values
are `openai` and `anthropic`. The response must be JSON that conforms to the
strict Pydantic `CandidateProfile` schema.
