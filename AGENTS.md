CV Reformatter — Project Instructions

Product contract and roadmap requirement

Before making product, UI, API, workflow, extraction, validation, or generation changes, read:

* docs/MVP_PRODUCT_SPEC.md
* docs/MVP_BUILD_ROADMAP.md

Use these as the product contract and build checklist. Identify the current stage before coding. If implementation details conflict with the spec or roadmap, pause and clarify before coding.

Ownership boundary

The frontend is owned and supported by the user's co-founder. The user is responsible for the backend.

Default agent responsibility is backend implementation and backend quality:

* FastAPI API behavior;
* ingestion and extraction;
* CandidateProfile validation;
* missing-field detection;
* client-facing render context;
* DOCX/PDF generation;
* backend tests and local artifacts.

Do not change frontend code unless the user explicitly asks for frontend edits. Frontend alignment issues may be reviewed and described, but implementation should stay on the backend side by default.

Product purpose

Build an MVP for boutique headhunters and recruitment agencies.

The product turns a candidate’s raw CV/resume into:

* structured candidate data;
* a recruiter-branded, client-ready CV;
* a missing-information checklist;
* a draft follow-up email/message to the candidate.

The product is not merely a file converter.

Core workflow

Candidate CV in DOCX or PDF
→ extract text/content
→ convert to structured candidate data
→ detect missing information
→ recruiter reviews and edits extracted data
→ populate recruiter/client template
→ generate DOCX and PDF outputs

Architecture rule

Never build this as:

PDF → LLM → PDF

Always build it as:

Resume file
→ extracted text
→ validated CandidateProfile JSON
→ recruiter review/edit
→ template rendering
→ DOCX/PDF output

CandidateProfile is the core internal data model.

MVP scope

Support first:

* DOCX resumes;
* text-based PDFs;
* one recruiter/agency DOCX template;
* recruiter-provided PDF sample resumes as visual/reference formats when needed;
* optional job-description text;
* local-only usage.

Do not build yet:

* scanned-PDF OCR;
* multi-user authentication;
* billing;
* ATS/CRM integrations;
* cloud storage;
* complex dashboards;
* multiple templates;
* automatic client sending.

Template/sample format rule

There are two different document inputs:

* candidate CV/resume: the source document extracted into CandidateProfile;
* recruiter sample/template: the target client-facing format the recruiter wants to match.

Recruiters may sometimes upload a PDF as a sample resume or preferred output format. Treat that PDF as a visual/reference format, not as the core data model and not as a directly editable template.

For MVP, use a controlled DOCX renderer for generated output. If a recruiter uploads a DOCX template, it can be used as the first true template source. If a recruiter uploads a PDF sample, use it only to understand layout/style expectations, while still rendering from reviewed CandidateProfile data into the app’s DOCX/PDF output pipeline.

Do not build automatic PDF-template reverse engineering in the MVP.

Do not implement:

sample PDF → LLM → final PDF

Required MVP outputs

1. Client-ready formatted CV as DOCX.
2. Client-ready formatted CV as PDF.
3. Missing-information checklist.
4. Draft follow-up email/message for the recruiter to send to the candidate.
5. Saved intermediate artifacts for debugging:
    * extracted raw text;
    * CandidateProfile JSON;
    * missing-fields JSON;
    * generated DOCX;
    * generated PDF.

CandidateProfile rules

Use strict Pydantic models.

Candidate fields should include:

* full name;
* email;
* phone;
* location;
* LinkedIn URL;
* portfolio/GitHub URL;
* professional summary;
* skills;
* languages;
* work experience;
* education;
* certifications;
* salary expectation;
* notice period;
* work authorization;
* interview availability.

Never invent facts.

If a field is not explicitly supported by the source CV, use null. Do not infer salaries, visa status, employment dates, achievements, qualifications, or employer names.

Missing-information rules

The system should detect, at minimum:

* salary expectation;
* notice period;
* current location;
* work authorization / visa status;
* interview availability;
* language proficiency;
* LinkedIn or portfolio link when relevant.

There are two views:

Internal recruiter view

Show all missing fields clearly.

Example:

Missing:
- Salary expectation
- Notice period
- Work authorization

Client-facing view

Never automatically show a blunt “missing information” list to a client.

For each sensitive or incomplete field, the recruiter chooses one of:

show
hide
pending_confirmation
available_upon_request

Example:

Availability: To be confirmed
Work authorization: To be confirmed
Salary expectation: Available upon request

The recruiter remains in control of client-facing disclosure.

Blind profile / anonymization rules

Anonymization is required for client-facing blind candidate profiles.

Anonymization affects only generated client-facing previews/exports. Never delete, overwrite, or anonymize the internal CandidateProfile source data.

When blind profile mode is enabled, hide or replace direct identifiers in the client-facing output:

* full name → Candidate A, initials, or another neutral label;
* email → hidden;
* phone → hidden;
* LinkedIn URL → hidden;
* portfolio/GitHub URL → hidden by default unless the recruiter explicitly chooses to show it;
* current employer → hide or generalize when recruiter marks it sensitive;
* exact location → generalize when needed.

Preservation promise rules

The product should preserve original extracted resume text for recruiter comparison, but must not claim a literal lossless conversion guarantee.

Do not promise:

Nothing gets dropped.
Lossless guarantee.

Preferred product promise:

Original preserved. Recruiter approved.

This means:

* original extracted text remains visible during review;
* structured fields are editable before export;
* missing or sensitive fields are flagged;
* the recruiter controls what appears in the client-facing output;
* final output is not treated as client-ready until reviewed.

Suggested stack

* Python 3.12+
* FastAPI for backend/API
* Streamlit for first local UI
* Pydantic for schemas and validation
* python-docx for DOCX reading/generation
* PyMuPDF for PDF text extraction
* LibreOffice headless mode for DOCX → PDF conversion
* LLM-provider abstraction compatible with OpenAI and Anthropic
* local filesystem and SQLite only if needed

Code-quality requirements

* Keep code modular.
* Add tests for all core components.
* Use type hints.
* Validate all LLM output with Pydantic.
* Provide clear errors for unsupported or corrupted files.
* Do not commit .env, API keys, real resumes, or personal data.
* Use synthetic resumes only in repository fixtures/tests.
* Preserve existing document styles where possible.
* Prefer small, reviewable changes over large rewrites.
* Explain changes and how to run tests after implementation.

Testing resources

When testing ingestion, extraction, rendering, or export behavior, prefer the existing test files and datasets in the repository, especially:

* tests/ for automated test cases;
* data/input_samples/ for synthetic sample inputs;
* data/generated_outputs/ for local debug artifacts and generated outputs;
* local dataset folders only when they are safe for local testing and not committed as real candidate data.

When running tests, save the terminal test output to `tests/test_results/` with a timestamped filename so the user can inspect the result later. Do not commit generated test result files unless the user explicitly asks for them.

Cross-format test fixture rule: when a backend test needs example resume files, DOCX resumes may be used as content examples while testing PDF-related behavior, and PDF resumes may be used as content examples while testing DOCX-related behavior. Test names and saved reports should clearly state the actual source format used and the backend behavior under test. This rule is only for backend testing convenience; it must not weaken the architecture rule, turn a PDF sample into an editable template, or create a direct `PDF -> LLM -> PDF` path.

Do not introduce or commit real resumes or personal data for tests.

Development order

Work in this order:

1. Repo scaffold and environment.
2. DOCX reader.
3. CandidateProfile schema.
4. LLM extraction into validated JSON.
5. Missing-field detection.
6. One DOCX template renderer.
7. DOCX-to-PDF export.
8. Basic Streamlit upload/review/download interface.
9. Text-based PDF support.
10. Additional templates and OCR later.

Before adding new product features, ensure the current pipeline works end-to-end with synthetic test resumes.

Definition of first successful demo

A user can:

1. Upload one DOCX CV.
2. See extracted candidate fields.
3. Correct fields manually.
4. See missing information.
5. Generate a branded DOCX.
6. Download a PDF version.
7. Copy a drafted follow-up message for missing information.
