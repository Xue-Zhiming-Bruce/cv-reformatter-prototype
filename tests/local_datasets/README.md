# Local Test Datasets

This directory is for local-only resume datasets used to smoke test ingestion,
extraction, target-layout analysis, rendering, and export behavior.

## Dataset provenance and authorization

On August 12, 2026, the product owner confirmed that **all resume and target
documents currently stored under `tests/local_datasets/` are fake example
resumes downloaded from public internet sources**. They are not real candidate
submissions. The current corpus is authorized for local evaluation and for the
explicitly requested live Adobe, Azure, OpenAI, and similar target-layout API
experiments. Agents should not ask for this authorization again for the current
corpus.

This attestation applies only to files present in the current local corpus.
Newly added datasets must record their provenance and authorization before live
provider use. Public availability also does not establish redistribution or
licensing rights, so dataset contents, source archives, extracted text, and
generated derivatives remain local and ignored by git.

Expected local layout:

```text
tests/local_datasets/
  cvparserpro_it_resumes/
    source.zip
    files/
      *.docx
  resume_archive/
    source.zip
    extracted/
      Resume/Resume.csv
      data/data/**/*.pdf
```

Current automated smoke coverage uses the DOCX files under
`cvparserpro_it_resumes/files/`. PDF archive coverage should stay focused and
local-only until API-level text-based PDF support is wired into `/api/process`.

Cross-format fixture rule: DOCX resume examples may be used while testing
PDF-related backend behavior, and PDF resume examples may be used while testing
DOCX-related backend behavior. Saved test reports should state the actual source
format used, the dataset path, and the behavior being validated. This is only a
testing convenience; it does not turn a PDF sample into an editable template and
does not change the required resume -> extracted text -> `CandidateProfile` JSON
-> reviewed rendering -> DOCX/PDF output pipeline.
