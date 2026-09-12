# Frontend Mock API

This local API lets frontend development continue while the C1/C2 backend experiments remain unsettled. It is stateful only in memory and never calls Adobe, an LLM, a database, or the production rendering pipeline.

## Start it

From the repository root:

```bash
python -m uvicorn scripts.mock_api:app --reload --host 127.0.0.1 --port 8000
```

Then open the API documentation at <http://127.0.0.1:8000/docs>. The existing Vite configuration already proxies `/api` requests to this address.

Restarting the process clears all mock artifacts. During development, the same reset can be triggered without restarting:

```bash
curl -X POST http://127.0.0.1:8000/__mock__/reset
```

## Supported product flow

The mock implements the current same-format workflow:

1. Upload a candidate with `POST /api/process`.
2. Optionally upload a same-format target with `POST /api/target-format`.
3. Submit the reviewed profile to `POST /api/artifacts/{artifact_id}/profiles/approve`.
4. Generate with `POST /api/generate`, using the returned `approved_profile_version_id`.
5. Open the returned preview or download URL.

Both PDF-to-PDF and DOCX-to-DOCX lanes are supported. A PDF candidate paired with a DOCX target, or the reverse, returns `409` rather than silently switching lanes.

The current frontend still posts a profile directly to `/api/generate`. It should be updated to perform step 3 first; the mock intentionally follows the backend contract so the frontend does not become coupled to an obsolete shortcut.

## Minimal example

```bash
curl -F file=@candidate.pdf http://127.0.0.1:8000/api/process
```

Use the returned `artifact_id` and `profile` when approving:

```bash
curl -X POST http://127.0.0.1:8000/api/artifacts/ARTIFACT_ID/profiles/approve \
  -H 'Content-Type: application/json' \
  -d '{"profile": PROFILE_JSON, "reviewer_note": "Approved in the mock UI"}'
```

Then generate:

```bash
curl -X POST http://127.0.0.1:8000/api/generate \
  -H 'Content-Type: application/json' \
  -d '{"artifact_id":"ARTIFACT_ID","approved_profile_version_id":"PROFILE_VERSION_ID"}'
```

## Provisional chat layout editing

These routes are intentionally marked provisional. They provide a stable frontend seam for the proposed interaction—select a layout node, describe a correction, inspect the new preview, then accept or reject it—without deciding whether C1 or C2 will implement the production behavior.

List selectable nodes:

```text
GET /api/artifacts/{artifact_id}/layout-nodes
```

Create a preview edit:

```http
POST /api/artifacts/{artifact_id}/layout-edits
Content-Type: application/json

{
  "instruction": "Make the experience bullets less indented",
  "selected_node_id": "section.experience.entries",
  "scope": "node",
  "base_layout_version_id": "layout_v1"
}
```

The response includes an `edit_id`, typed mock `operations`, changed node IDs, and a `preview_url`. An applied edit is not active until the UI sends a decision:

```http
POST /api/artifacts/{artifact_id}/layout-edits/{edit_id}/decision
Content-Type: application/json

{"decision": "accept"}
```

Rejecting preserves the previous active layout version. Generation always uses the active accepted version.

## UI failure states

Send the mock-only `X-Mock-Scenario` header to exercise frontend states without changing production request bodies:

| Endpoint | Header value | Result |
| --- | --- | --- |
| `/api/process` | `processing_failed` | `422` extraction failure |
| `/layout-edits` | `needs_clarification` | Chat asks the user to select a target |
| `/layout-edits` | `unsupported` | Instruction is outside the edit vocabulary |
| `/layout-edits` | `render_failed` | Validation failure with the previous version preserved |

Omit the header, or use `happy`, for the normal path.

## Boundaries

- All candidate information is synthetic.
- Uploaded bytes are validated by extension but are not parsed or stored.
- Preview PDFs and DOCX downloads are simple fixtures, not fidelity evidence.
- No claim made by this mock should be used to judge C1, C2, Adobe, or VLM quality.
- The layout-edit JSON is a frontend collaboration contract only; it is not yet an approved production API or persistence schema.
