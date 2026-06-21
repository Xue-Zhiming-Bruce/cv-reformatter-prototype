from pathlib import Path
from zipfile import BadZipFile, ZipFile


class UnsupportedFileTypeError(ValueError):
    """Raised when the uploaded file type is outside the MVP scope."""


class CorruptedFileError(ValueError):
    """Raised when a DOCX cannot be opened as a valid package."""


def validate_docx_file(path: str | Path) -> Path:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    if not file_path.is_file():
        raise UnsupportedFileTypeError(f"Expected a file, got: {file_path}")
    if file_path.suffix.lower() != ".docx":
        raise UnsupportedFileTypeError("Only .docx files are supported in this milestone.")

    try:
        with ZipFile(file_path) as archive:
            if "[Content_Types].xml" not in archive.namelist():
                raise CorruptedFileError("The DOCX package is missing required metadata.")
    except BadZipFile as exc:
        raise CorruptedFileError("The DOCX file appears to be corrupted or invalid.") from exc

    return file_path
