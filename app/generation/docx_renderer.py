class DocxRenderingNotImplementedError(NotImplementedError):
    """DOCX template rendering is planned after the extraction milestone."""


def render_docx(*_args: object, **_kwargs: object) -> None:
    raise DocxRenderingNotImplementedError("DOCX rendering is not implemented in the first milestone.")
