# Document viewer security

`GET /documents/{public-document-number}/html` resolves a public legal
signature in the active PostgreSQL release. Internal dataset, document and
chunk IDs are not accepted as the public key.

The response sanitizes canonical HTML with an allowlist of text, headings,
lists and tables. Scripts, styles, frames, forms, SVG, event handlers and
active URL schemes are removed. It sends `nosniff`, `frame-ancestors 'none'`,
`base-uri 'none'`, and a restrictive CSP. Raw HTML hashes remain server-side;
the release repository verifies source content before ingest.

The frontend renders this fragment in the trusted viewer shell and shows the
public document number, effective interval and official URL from the citation
contract. `tests/test_document_viewer.py` and
`tests/test_api/test_document_viewer_endpoint.py` cover XSS removal, internal
anchors, hash mismatch, and path traversal; managed-browser smoke remains a
release gate.
