# CV Reformatter MVP

Prototype for a CV reformat SaaS product. This local MVP turns a candidate's DOCX or text-based PDF resume into validated candidate profile JSON, flags missing recruiter-critical information, and saves debug artifacts.

This first milestone intentionally implements only:

- DOCX and PDF resume ingestion
- Plain text, paragraph, and table extraction
- LLM-driven `CandidateProfile` JSON validation
- Missing-field detection
- A CLI demo using either a mock extractor or a configured LLM provider

PDF export, template rendering, and Streamlit review UI are scaffolded for later milestones.

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

Still scaffolded:

- Client-ready DOCX template rendering
- DOCX-to-PDF export
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

Optional dataset integration tests run when `tests/archive.zip` is present
locally. They extract sample resume PDFs from the zip and verify PDF text
extraction. Do not commit real resume datasets or generated candidate outputs.

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
