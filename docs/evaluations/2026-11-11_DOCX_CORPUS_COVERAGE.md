# DOCX Corpus Coverage Measurement (2026-11-11)

Status: `Curated measurement record` — the coverage number for the
inventoried DOCX corpus (issue #31).

Last reviewed: November 11, 2026

Measured revision: `135605990475aeb188cea3819e028f9151843156`

This note records one controlled offline run of the 50 inventoried DOCX resumes
through the real extraction/coverage and built-in generation path
(`POST /api/process` → `POST /api/generate`), and the aggregate coverage number
it produced. Before this run the corpus had only a corpus-reading check, so the
number was an assumption. It is now a recorded measurement.

The deliverable is the number, not a green result. A low coverage number would
have been a successful outcome of issue #31 and would have been reported here
unchanged; nothing was relaxed to produce the figures below.

## What Was Run

One pytest test,
`tests/integration/test_docx_corpus_coverage.py`, drives every file through
these existing production stages, in this order:

| # | Stage | Executed |
| --- | --- | --- |
| 1 | `POST /api/process` — `read_docx` ingestion of the actual corpus bytes | yes |
| 2 | `build_llm_client(provider="mock")` — the production factory's `mock` branch (`MockLLMClient`) | yes (mock extraction) |
| 3 | `segment_and_extract_resume` → `audit_segmentation_lines` → `materialize_llm_segmentation` | yes |
| 4 | `<outputs>/<artifact_id>/segmentation_audit.json` read back through `SegmentationCoverageAudit` | yes |
| 5 | `POST /api/generate` with the returned `artifact_id`/`profile_revision_id` and **no** `target_format` (built-in template path) | yes |
| 6 | `require_profile_revision` → `validate_profile_source_review` (the real coverage gate) | yes |
| 7 | `render_layout_template` → built-in `lane-a-product-subset/17` HTML | yes |
| 8 | `export_html_to_pdf` replaced by `tests/helpers/layout_proof.py::make_proof_pdf_exporter()` | yes, **stubbed** |
| 9 | `validate_generated_content` + `validate_output_structure` on the stubbed PDF | yes |

Artifacts (`segmentation_audit.json`, HTML, PDF, debug JSON) were written only
to the test's `tmp_path`; nothing was written into `tests/local_datasets/`.

## Corpus

Directory: `tests/local_datasets/cvparserpro_it_resumes/files/` — read in place,
no copy, re-create, re-zip or rename. The directory holds 51 entries;
`~$t_cv_1.docx` is a Word lock file and is excluded, leaving the inventoried 50
files `it_cv_1.docx`..`it_cv_50.docx` with no number gaps. Each file's SHA-256
below is over the file bytes as read for this run.

## Result

```text
DOCX corpus coverage: files=50 process_ok=50 audit_passed=50 generate_ok=50 source_lines=1050 matched_lines=1050 coverage=1.0000
```

- `POST /api/process`: 50 of 50 returned `200`.
- Audit: 50 of 50 `passed`; 0 `failed`; 0 files blocked from generation.
- Source lines: 1050 matched of 1050 (21 non-empty lines per file); 0 missing
  and 0 invented/altered lines.
- `POST /api/generate`: 50 of 50 returned `200` with a `pdf_sha256`; 0 blocked.
- Wall time of the run: 3.49 s (`pytest` reported `1 passed`).

## Per-File Results

| File | SHA-256 | Source lines | Missing | Invented/altered | Audit | Generate |
| --- | --- | --- | --- | --- | --- | --- |
| `it_cv_1.docx` | `6680953b79c1b553acbe9ff7df6d0098216b23184e3fe8d31a0bcd8fbae2f5d8` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_2.docx` | `e46022b48e3480cda20725dcbb0793e352e15e3deafa0f2a14003afeb0ed43a8` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_3.docx` | `dc39e1bd33dbcf9aa8556ad08d9e3937b115acb312b1e994b3c5fce464f35ef3` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_4.docx` | `1951edc2c99ba8eb99be20da51217bf16f8cd356f97f667b268eb6224cf09b2d` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_5.docx` | `7cbab1288611db416744f179f975ffc2a830053b3a471dc8b9b2a9972345b4f1` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_6.docx` | `f0ae4a7604a80a12b7d671e04c4a3b421529faa3bb40c92736c29bd8c69eebc0` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_7.docx` | `68993954e784d146cc4032b2e06ec66647faac3d810edb5da5cc23a4230a5ca0` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_8.docx` | `c564b30e435cd319eb475f6ef0f1e750873de420dba165a9a94e767276f0ff2c` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_9.docx` | `0bca5c2dc9cda7a8506ddd90955d28a598566164016cc213bd1f6b5658d819b2` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_10.docx` | `34daa4ba817c6a599d658c060db93df83399b2aadfad3be7f2c31268f69774b8` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_11.docx` | `5edba954a81f5404ccc20e3681dbb1cf2117dae2179afdbc4a91f9bff5368f63` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_12.docx` | `8dabde762b0d23a0c658309cb4685016a89f7ee78994464a8fd13d8f8d0f64e7` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_13.docx` | `60a12fa47b74f772b22e12ec81a73826443584663d28036be3021b7450fe818c` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_14.docx` | `2e4d2dc961fc7876cbb91b2fd9dc84c3034239e123f27da87e6126b7debcb595` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_15.docx` | `0e167904d77188b4855fdc76f6ae7609150de9b7b5307d7c0193cb227f52f105` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_16.docx` | `d241fad1603c2fc6c31df23af378bcd67a9ac911992aafa17c9f23b8796d3f9b` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_17.docx` | `bc70cc28bd70b9a2d069af409fce227068cfc7627669a01e6ae4864efcbf8a37` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_18.docx` | `b5fd5168ac4d054d913fca822fb1fce5d4ba6c48060f7768699d0a4d5bd924f6` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_19.docx` | `abe2814e7b763198126e30ea12b590b8e4c3e624eb905a647a598c06751cda77` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_20.docx` | `050fda8bd4516b909868efd61d44f7e35c4792ba088d0344491a9a8893248e31` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_21.docx` | `f113b6b32a96b770bc87999939943bfa3b65be538ccdcbddfd8ac0c3007ced86` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_22.docx` | `e9749c46016aa251e8778e7f9de4deab8100de1a6657fffa355a4533ce8ffc0e` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_23.docx` | `d8a651c00b347c6feb0ce5de34f21a747b90f0bd6f252a9e6ca09f46e64e7717` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_24.docx` | `e3bbfc65cdde962aab0055a38f7e325a737747112b3d129ad796520c249aae92` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_25.docx` | `cd5d049458ace79e2b92a75a20c0198aceec99b9d463aa4c4a087c5ff4918d2c` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_26.docx` | `59bef8b5dd573397e5c388d891a2cb02a50737345e8ca2aa231cfad2cfe70522` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_27.docx` | `b6e5b57d07d986e309e9d1b3d9328b5161c651e8d052dcf523ff4e0220c58a85` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_28.docx` | `6cb7eb81d5b94dca1401f52084e2d479e6cc4b552154c821f2d7c11c69339b1c` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_29.docx` | `b9c93bae731ec9491e9fdac7ad8874c55300df09eb854d1fc8945364bc1befe7` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_30.docx` | `1361a3881073c06f8000c33034a59e6b319f6e419d98747353125512c37af32c` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_31.docx` | `1e41ed3efaae0156074bda7b58c3158511faf44527f178ca67ccc485ded8be20` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_32.docx` | `60865d59061bd1dea548e504e61c8270c21bd88c3205eee6f2fe4fee7fd8fcf4` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_33.docx` | `30dd6984b6a9a35764f134e5e206254776b36f5f2e701c072ea48573dabb83d1` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_34.docx` | `1767593948b168aa325f3b997d6f7753e3e4931ad47c830d57b467d350b7494f` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_35.docx` | `9c41cacf559adf09a07e6ddf44547eded1cea856cdc94745a19234d8edfc11dd` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_36.docx` | `2ce5c1ca440210ebd98f75e4b41cff36d4cbcae92ed297523fcb46d516bc9cae` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_37.docx` | `9c1d7fae99816565bc3fd9397065a3d05150a97d4c91bca3b20e12071dc76865` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_38.docx` | `1688ab696e7299b5c018444810c50c46d5e9045a66641eed8bbea948a7c7751d` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_39.docx` | `284b8780ee89a3c9b44059f8269b01772d873c3cf1b94cf91ef19681db2ca3a3` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_40.docx` | `67f3ed5df439f3a4f07ca941d7f9ad0367c046ff86b3de0b06aba5695dc8282a` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_41.docx` | `535281b7e9c0b4824423c921602a054375816c82168b79db8b1a6853a1d547e3` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_42.docx` | `6b311a505880fb908834eadda72fb35c630f464e49185c746424755fed13c94b` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_43.docx` | `189dce07855a9f995d3b596ed1370073cb3ff34c82ec6de3fc680c4d042ecb8e` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_44.docx` | `01dd5e88a55238dff82aecda164b0cbc2c5a7394cb96876d3770cd0bf0e7fab8` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_45.docx` | `c45fd2b1dcbdea755365c7a18444601991e1db460fb7153d2463a49a6488d38d` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_46.docx` | `701d78bf3c83c86737bc9db78d6b93824159b989df7e9a880aeb2ca6807dcdc9` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_47.docx` | `fe2f399cb1946144b10cb111129a3a5cd9550890bc9cee786cd45153ac6107a6` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_48.docx` | `9cce96452e83d46e0232deabbc73c6f3e6d57149f6ab5a2d08882aa67d9f7510` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_49.docx` | `7ac2f681694da1a57afaeca04b127fa8db705f64d455e5821345d24d424312cf` | 21 | 0 | 0 | `passed` | 200 |
| `it_cv_50.docx` | `0ea984455d551d394f084768509a2924668f5e7cc0249eb553c304f5d61610d0` | 21 | 0 | 0 | `passed` | 200 |

## Stages Not Executed

- Live extraction provider. Extraction is the production factory's `mock`
  branch; no `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` is read, no network call is
  made and no paid budget is spent. This run is not evidence of provider
  extraction quality.
- Chrome/Chromium HTML→PDF export. PDF export is the shared helper stub, so no
  PDF fidelity is claimed.
- Target analysis, matrix scoring, layout proofs, page counts and visual
  diagnostics: this corpus has no target, and `POST /api/generate` was called
  without `target_format`.
- Profile approval, reviewer edits and final acceptance: profiles were generated
  and used as the saved process revision, never human-approved.
- DOCX layout diversity: see the first limit below.

## What This Run Does Not Establish

This is not a product trial under
`docs/testing/TEST_STRUCTURE.md#product-trials-use-document-inputs`: the
documents are real but extraction was mocked, so the run shows the coverage gate
and the generation path on the corpus bytes — it does not show what a live
provider extracts from them. It is not an owner acceptance of any artifact and
not a quality claim about any generated HTML/PDF: no generated output is
referenced, reviewed or retained by this note, and the owner remains the only
judge of generated files.

## Limits

1. The corpus is one DOCX template rendered for 50 different fictional
   identities — 50 distinct texts, one layout. `coverage=1.0000` therefore says
   nothing about DOCX layout diversity; it is not a layout-coverage result.
2. Extraction is mock, so this is not evidence of provider extraction quality;
   the audit matched what the mock segmenter returned against the same source
   text. A live-provider run of the same corpus is a separate, budgeted decision.
3. PDF export is the helper stub (text-bearing synthetic PDF at the template
   geometry), so no PDF fidelity, typography or page-count claim is made.
4. The figures describe one machine and revision `1356059` only; a change to the
   extractor, the audit, the renderer, the composer or the machine invalidates
   them.
5. Every per-file row is an audit and generation outcome. It is not a statement
   that the generated profile is good, complete or client-ready.

## Reproduction

Raw evidence (Git-ignored):
`tests/test_results/pytest/20261111T200926Z_docx_corpus_coverage.txt`.

```bash
set -o pipefail
.venv/bin/python -m pytest tests/integration/test_docx_corpus_coverage.py -q -s 2>&1 \
  | tee "tests/test_results/pytest/$(date -u +%Y%m%dT%H%M%SZ)_docx_corpus_coverage.txt"
```

The command exits `0` on whatever numbers it measures: the test asserts only
that 50 rows exist, that every row carries a valid audit record and a decided
generation status, and it prints the aggregate line above. With the corpus
directory absent the test skips naming
`tests/local_datasets/cvparserpro_it_resumes/files`; with a number missing from
1..50 it fails naming the missing numbers.
