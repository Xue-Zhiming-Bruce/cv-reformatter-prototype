# Local Resume Input Inventory

Status: `Active local input inventory`

Verified: November 11, 2026. Paths below are relative to the repository root;
this tracked page inventories local files without committing their contents.

## Dataset provenance and authorization

On November 11, 2026, the product owner reaffirmed that **all resumes currently
used for testing are fictional/manufactured, not confidential candidate
submissions**. This supersedes the earlier public-or-fictional description and
includes the existing local PDF/DOCX examples. The earlier August 12/24
clearances remain evidence of permitted local use; do not ask again merely
because the documents resemble personal resumes.

Fictional content is distinct from mocked execution: product trials use these
document bytes through the actual pipeline, not hand-written profiles or fake
provider results. Follow the [document-input rule](../../docs/testing/TEST_STRUCTURE.md#product-trials-use-document-inputs).
New datasets need their own provenance record. Existing external-provider
authorizations, opt-ins and budgets still apply; this inventory does not create
a new paid-call budget. Source files, archives and generated derivatives remain
local and Git-ignored.

## Primary PDF examples — nine files

Directory: `tests/local_datasets/resume_matrix/`.

| File | Existing identity/use |
| --- | --- |
| [resume_A.pdf](resume_matrix/resume_A.pdf) | ATS Classic example; source/target investigation input. |
| [resume_B.pdf](resume_matrix/resume_B.pdf) | Classic Professional example; established matrix source/target. |
| [resume_C.pdf](resume_matrix/resume_C.pdf) | Tech Teal example; established matrix target with badge decoration. |
| [resume_D.pdf](resume_matrix/resume_D.pdf) | Additional example used for extraction/coverage regressions. |
| [resume_E.pdf](resume_matrix/resume_E.pdf) | Distinct candidate source used in E→B reviews. |
| [resume_F.pdf](resume_matrix/resume_F.pdf) | Candidate with structured projects, used in F→C reviews. |
| [resume_G.pdf](resume_matrix/resume_G.pdf) | Additional example; historical target-structure diagnosis input. |
| [resume_H.pdf](resume_matrix/resume_H.pdf) | Additional example; historical target-structure diagnosis input. |
| [resume_I.pdf](resume_matrix/resume_I.pdf) | Additional example; historical target-structure blind-test input. |

For established transformation cases, start with the existing A→B/A→C/B→C/C→B,
E→B and F→C cases in `scripts/run_abc_live_matrix.py`. File availability does not
mean every layout is supported or accepted. G/H/I's older per-file external-call
limits are recorded in the [local matrix history](resume_matrix/README.md);
do not treat those old one-call records as fresh provider budgets. That page's
historical runners/paths are not current operating instructions.

Candidate and target roles must be recorded per run. The source supplies facts;
the target supplies presentation. Never use a target's sample facts as the
candidate. Use saved evidence only when checksum-matched and labeled as replay.

## DOCX examples — 50 files

Directory: `tests/local_datasets/cvparserpro_it_resumes/files/`.

The exact set is **`it_cv_1.docx` through `it_cv_50.docx`, inclusive, with no
number gaps**. Examples: [it_cv_1.docx](cvparserpro_it_resumes/files/it_cv_1.docx),
[it_cv_25.docx](cvparserpro_it_resumes/files/it_cv_25.docx),
[it_cv_50.docx](cvparserpro_it_resumes/files/it_cv_50.docx). The original local
archive is `tests/local_datasets/cvparserpro_it_resumes/source.zip`.

`~$t_cv_1.docx` is a Word lock/temp file, **not a resume input**; exclude `~$*`
files even though their extension is `.docx`. Existing local smoke coverage is
`tests/integration/test_dataset_smoke.py`: it reads the documents but uses
`MockLLMClient` and writes HTML only. It is a mocked smoke regression, not a
product trial or evidence that extracted candidate facts are correct.

## PDF archive — 2,484 files in 24 categories

Directory: `tests/local_datasets/resume_archive/extracted/data/data/`.
Each PDF is identified by `<Category>/<ID>.pdf`. The complete existing index is
[Resume.csv](resume_archive/extracted/Resume/Resume.csv): its `Category` and `ID`
columns identify each exact file. Use those columns to locate PDFs; do not
substitute the CSV's extracted text/HTML for document ingestion.

| Category | PDFs | Category | PDFs |
| --- | ---: | --- | ---: |
| ACCOUNTANT | 118 | ADVOCATE | 118 |
| AGRICULTURE | 63 | APPAREL | 97 |
| ARTS | 103 | AUTOMOBILE | 36 |
| AVIATION | 117 | BANKING | 115 |
| BPO | 22 | BUSINESS-DEVELOPMENT | 120 |
| CHEF | 118 | CONSTRUCTION | 112 |
| CONSULTANT | 115 | DESIGNER | 107 |
| DIGITAL-MEDIA | 96 | ENGINEERING | 118 |
| FINANCE | 118 | FITNESS | 117 |
| HEALTHCARE | 115 | HR | 110 |
| INFORMATION-TECHNOLOGY | 120 | PUBLIC-RELATIONS | 111 |
| SALES | 116 | TEACHER | 102 |

Example: [ACCOUNTANT/10554236.pdf](resume_archive/extracted/data/data/ACCOUNTANT/10554236.pdf).
The original archive is `tests/local_datasets/resume_archive/source.zip`.
This is broader input coverage, not a claim that every file/layout has passed
extraction, generation or owner review.

## Minimal fixtures and historical outputs

- [data/input_samples/synthetic_resume.docx](../../data/input_samples/synthetic_resume.docx)
  is a real DOCX file with fictional content, used by `scripts/run_demo.py`.
  It is a minimal demo/smoke fixture, not a replacement for representative
  product trials with the PDF/DOCX examples above.
- `production_like/`, `provider_output_review/`, matrix `runs/`, and
  `data/generated_outputs/` contain historical inputs/copies or generated
  results. Do not count generated PDFs, proof PDFs or duplicate run copies as
  new source resumes or silently start from their mocked/intermediate profiles.
- A fresh checkout may lack these ignored files. Report the exact missing path;
  do not silently generate a substitute resume or restore a retired runner.

## Reporting a document-based trial

Record exact source/target paths, formats, checksums, stages actually executed,
provider/mock/replay status, and links to resulting HTML/PDF and independent
checks. Inspect actual outputs against the original documents. Follow
[Test Result Artifact Format](../../docs/testing/TEST_RESULT_FORMAT.md) for matrix
runs; current editable output is HTML and the deliverable is PDF, not DOCX.
