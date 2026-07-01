# Local Test Datasets

This directory is for local-only resume datasets used to smoke test ingestion,
extraction, rendering, and export behavior.

These files may contain real candidate data, so dataset contents are ignored by
git. Commit only this README and synthetic fixtures. Do not commit extracted
resume text, generated candidate profiles, generated outputs, or source archives.

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
