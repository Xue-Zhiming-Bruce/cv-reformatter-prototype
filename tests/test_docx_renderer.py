import pytest

from app.generation.docx_renderer import DocxRenderingNotImplementedError, render_docx


def test_docx_renderer_is_intentionally_not_implemented_yet() -> None:
    with pytest.raises(DocxRenderingNotImplementedError):
        render_docx()
