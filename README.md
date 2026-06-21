# CV Reformatter MVP

Prototype for a CV reformat SaaS product. This local MVP turns a candidate's DOCX resume into validated candidate profile JSON, flags missing recruiter-critical information, and saves debug artifacts.

This first milestone intentionally implements only:

- DOCX resume ingestion
- Plain text, paragraph, and table extraction
- LLM-driven `CandidateProfile` JSON validation
- Missing-field detection
- A CLI demo using either a mock extractor or a configured LLM provider

PDF export, template rendering, and Streamlit review UI are scaffolded for later milestones.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

To use a real LLM, set `LLM_PROVIDER` and the matching API key in `.env`.

## Run Tests

```bash
pytest
```

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
export LLM_PROVIDER=openai
export OPENAI_API_KEY=...
python scripts/run_demo.py --input path/to/synthetic_resume.docx
```

Supported provider values are `openai` and `anthropic`. The response must be JSON that conforms to the strict Pydantic `CandidateProfile` schema.
