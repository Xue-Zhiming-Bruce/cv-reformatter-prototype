class PdfIngestionNotImplementedError(NotImplementedError):
    """PDF ingestion is planned after the DOCX vertical slice."""


def read_pdf_text(_path: str) -> str:
    raise PdfIngestionNotImplementedError("PDF ingestion is not implemented in the first milestone.")
