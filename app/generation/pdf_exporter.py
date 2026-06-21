class PdfExportNotImplementedError(NotImplementedError):
    """PDF export is planned after DOCX rendering."""


def export_docx_to_pdf(*_args: object, **_kwargs: object) -> None:
    raise PdfExportNotImplementedError("PDF export is not implemented in the first milestone.")
