CV Reformatter — Project Instructions

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