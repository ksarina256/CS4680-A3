"""Read uploaded student files and bundle them into one text block for the grader.

Supported: any plain-text file (code, Markdown, CSV, ...), Jupyter notebooks, PDF (with
a text layer), Word .docx, and .zip archives (expanded in memory, never written to disk).
Anything else is still listed by name, so file-presence and file-naming rules can be
checked, but its content is reported as unreadable.
"""

from __future__ import annotations

import codecs
import html
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Sequence

from .prompts import neutralize

MAX_FILE_BYTES = 25 * 1024 * 1024  # one file, or one file inside a zip
MAX_ZIP_UNCOMPRESSED = 50 * 1024 * 1024
MAX_ZIP_MEMBERS = 500
MAX_FILES = 60  # files sent to the grader
MAX_FILE_CHARS = 100_000  # longer files are cut, with a visible marker
MAX_TOTAL_CHARS = 400_000  # the whole bundle; beyond this we refuse rather than cut silently
MAX_PDF_PAGES = 100
MAX_NOTEBOOK_OUTPUT_CHARS = 2_000

_SKIP_DIRS = {"__macosx", ".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode", ".ipynb_checkpoints"}
_SKIP_FILES = {".ds_store", "thumbs.db"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".heic", ".ico"}
_OFFICE_EXTS = {".xlsx", ".xls", ".pptx", ".ppt", ".doc", ".odt", ".ods", ".odp", ".pages", ".numbers", ".key"}

KIND_LABELS = {
    "text": "Text",
    "notebook": "Notebook",
    "pdf": "PDF",
    "docx": "Word",
    "pasted": "Pasted text",
    "image": "Image",
    "archive": "Archive",
    "binary": "Binary",
}


@dataclass
class LoadedFile:
    name: str
    kind: str
    text: str | None  # None means the content could not be read
    size: int  # bytes as uploaded
    chars: int = 0  # characters before any truncation
    truncated: bool = False
    note: str = ""

    @property
    def readable(self) -> bool:
        return self.text is not None


@dataclass
class LoadResult:
    files: list[LoadedFile] = field(default_factory=list)
    archives: list[str] = field(default_factory=list)  # names of uploaded .zip files
    warnings: list[str] = field(default_factory=list)


@dataclass
class Submission:
    text: str  # goes inside <submission>...</submission>
    files: list[LoadedFile]
    archives: list[str]
    names: list[str]
    warnings: list[str]

    @property
    def manifest(self) -> str:
        """File names for the format audit, one per line."""
        lines = list(self.names)
        lines += [f"{a} (zip archive; the files above were inside it)" for a in self.archives]
        return "\n".join(lines)

    @property
    def total_chars(self) -> int:
        return len(self.text)


# ------------------------------------------------------------------ decoding


def decode_text(data: bytes) -> str | None:
    """Decode bytes as text, or return None if they look binary."""
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = data.decode("utf-16", errors="replace")
    elif b"\x00" in data[:8192]:
        return None
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")  # typical for older Windows files
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _notebook_text(text: str) -> str:
    nb = json.loads(text)
    cells = nb["cells"]
    parts = []
    for i, cell in enumerate(cells, start=1):
        source = cell.get("source", "")
        source = "".join(source) if isinstance(source, list) else str(source)
        kind = cell.get("cell_type", "code")
        if kind == "code":
            parts.append(f"# [code cell {i}]\n{source}")
            for out in cell.get("outputs", []) or []:
                piece = ""
                if out.get("output_type") == "stream":
                    piece = "".join(out["text"]) if isinstance(out.get("text"), list) else str(out.get("text", ""))
                elif out.get("output_type") == "error":
                    piece = f"{out.get('ename', 'Error')}: {out.get('evalue', '')}"
                else:
                    plain = (out.get("data") or {}).get("text/plain", "")
                    piece = "".join(plain) if isinstance(plain, list) else str(plain)
                piece = piece.strip()
                if piece:
                    if len(piece) > MAX_NOTEBOOK_OUTPUT_CHARS:
                        piece = piece[:MAX_NOTEBOOK_OUTPUT_CHARS] + " [... output cut ...]"
                    parts.append(f"# [output of cell {i}]\n{piece}")
        else:
            parts.append(f"# [{kind} cell {i}]\n{source}")
    return "\n\n".join(parts)


def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        info = zf.getinfo("word/document.xml")
        if info.file_size > MAX_FILE_BYTES:
            raise ValueError("document.xml is too large")
        xml = zf.read(info).decode("utf-8", errors="replace")
    xml = re.sub(r"<w:instrText[^>]*>.*?</w:instrText>", "", xml, flags=re.DOTALL)
    xml = re.sub(r"<w:tab\s*/>", "\t", xml)
    xml = re.sub(r"<w:(?:br|cr)\b[^>]*/>", "\n", xml)
    xml = xml.replace("</w:p>", "\n")
    text = html.unescape(re.sub(r"<[^>]+>", "", xml))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader  # imported here so a missing install only affects PDFs

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("the PDF is password protected")
    pages = []
    for number, page in enumerate(reader.pages[:MAX_PDF_PAGES], start=1):
        pages.append(f"--- page {number} ---\n{(page.extract_text() or '').strip()}")
    if len(reader.pages) > MAX_PDF_PAGES:
        pages.append(f"[... only the first {MAX_PDF_PAGES} of {len(reader.pages)} pages were read ...]")
    text = "\n\n".join(pages)
    if not re.sub(r"--- page \d+ ---|\s", "", text):
        raise ValueError("no text could be extracted (a scanned PDF?)")
    return text


# ------------------------------------------------------------------ loading


def _clean_name(raw: str) -> str:
    """Make an archive member name safe to display: no drive, '..' or control characters."""
    raw = re.sub(r"[\x00-\x1f\x7f]", "", raw.replace("\\", "/"))
    parts = [p for p in PurePosixPath(raw).parts if p not in ("", "/", ".", "..")]
    return "/".join(parts) or "unnamed"


def _finish(name: str, kind: str, text: str | None, size: int, note: str = "") -> LoadedFile:
    """Build a LoadedFile, applying the per-file length cap."""
    if text is None:
        return LoadedFile(name=name, kind=kind, text=None, size=size, note=note)
    chars = len(text)
    truncated = chars > MAX_FILE_CHARS
    if truncated:
        text = text[:MAX_FILE_CHARS] + f"\n[... truncated: {chars - MAX_FILE_CHARS:,} more characters not shown ...]"
        note = note or f"only the first {MAX_FILE_CHARS:,} of {chars:,} characters were sent"
    return LoadedFile(name=name, kind=kind, text=text, size=size, chars=chars, truncated=truncated, note=note)


def _read_one(name: str, data: bytes) -> LoadedFile:
    size = len(data)
    ext = PurePosixPath(name).suffix.lower()
    kind = "text"
    try:
        if size > MAX_FILE_BYTES:
            return _finish(name, "binary", None, size, f"larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
        if ext == ".pdf":
            kind = "pdf"
            return _finish(name, kind, _pdf_text(data), size)
        if ext == ".docx":
            kind = "docx"
            return _finish(name, kind, _docx_text(data), size)
        if ext in _IMAGE_EXTS:
            return _finish(name, "image", None, size, "images cannot be read by the grader")
        if ext in _OFFICE_EXTS:
            return _finish(name, "binary", None, size, f"{ext} files are not supported; export it as PDF or text")
        text = decode_text(data)
        if text is None:
            return _finish(name, "binary", None, size, "binary file, not text")
        if ext == ".ipynb":
            kind = "notebook"
            try:
                text = _notebook_text(text)
            except (ValueError, KeyError, TypeError, AttributeError):
                kind = "text"  # not valid notebook JSON; grade the raw text instead
        return _finish(name, kind, text, size)
    except ImportError:
        return _finish(name, kind, None, size, "PDF support is not installed (pip install pypdf)")
    except Exception as exc:  # noqa: BLE001 - one bad file must not stop the rest
        reason = str(exc) if isinstance(exc, ValueError) and str(exc) else type(exc).__name__
        return _finish(name, kind, None, size, f"could not be read: {reason}")


def _expand_zip(name: str, data: bytes, result: LoadResult) -> list[LoadedFile]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return [_finish(name, "archive", None, len(data), "not a valid zip file")]

    kept: list[tuple[zipfile.ZipInfo, str]] = []
    skipped = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        clean = _clean_name(info.filename)
        parts = [p.lower() for p in clean.split("/")]
        if any(p in _SKIP_DIRS for p in parts[:-1]) or parts[-1] in _SKIP_FILES:
            skipped += 1
            continue
        kept.append((info, clean))

    if len(kept) > MAX_ZIP_MEMBERS:
        return [_finish(name, "archive", None, len(data), f"contains {len(kept):,} files; the limit is {MAX_ZIP_MEMBERS}")]
    if sum(i.file_size for i, _ in kept) > MAX_ZIP_UNCOMPRESSED:
        return [_finish(name, "archive", None, len(data), "unpacks to more than 50 MB")]

    result.archives.append(name)
    if skipped:
        result.warnings.append(f"{name}: ignored {skipped} system or hidden file(s) such as __MACOSX or .DS_Store.")
    if not kept:
        result.warnings.append(f"{name} contains no files.")

    out = []
    for info, clean in kept:
        if info.flag_bits & 0x1:
            out.append(_finish(clean, "binary", None, info.file_size, "password-protected"))
        elif clean.lower().endswith(".zip"):
            out.append(_finish(clean, "archive", None, info.file_size, "nested archives are not opened"))
        elif info.file_size > MAX_FILE_BYTES:
            out.append(_finish(clean, "binary", None, info.file_size, f"larger than {MAX_FILE_BYTES // (1024 * 1024)} MB"))
        else:
            try:
                with zf.open(info) as handle:
                    member = handle.read(MAX_FILE_BYTES + 1)
            except Exception as exc:  # noqa: BLE001 - corrupt member, unsupported compression, ...
                out.append(_finish(clean, "binary", None, info.file_size, f"could not be unpacked: {type(exc).__name__}"))
                continue
            out.append(_read_one(clean, member))
    return out


def _natural_key(name: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name.lower())]


def _dedupe(files: list[LoadedFile]) -> None:
    """Give repeated names a numeric suffix so the grader can tell them apart."""
    seen: dict[str, int] = {}
    for f in files:
        key = f.name.lower()
        count = seen.get(key, 0) + 1
        seen[key] = count
        if count > 1:
            path = PurePosixPath(f.name)
            f.name = str(path.with_name(f"{path.stem} ({count}){path.suffix}"))


def load_uploads(uploads: Sequence[tuple[str, bytes]]) -> LoadResult:
    """Read uploaded files. Never raises for a bad file; problems become notes and warnings."""
    result = LoadResult()
    for raw_name, data in uploads:
        name = _clean_name(PurePosixPath(raw_name.replace("\\", "/")).name or raw_name)
        if name.lower().endswith(".zip"):
            result.files.extend(_expand_zip(name, data, result))
        else:
            result.files.append(_read_one(name, data))

    _dedupe(result.files)
    result.files.sort(key=lambda f: _natural_key(f.name))

    unreadable = [f for f in result.files if not f.readable]
    if unreadable:
        result.warnings.append(
            f"{len(unreadable)} file(s) could not be read and will count as missing evidence: "
            + ", ".join(f.name for f in unreadable)
            + ". Tasks that depend on them will lose points."
        )
    cut = [f.name for f in result.files if f.truncated]
    if cut:
        result.warnings.append(f"Long file(s) were cut to {MAX_FILE_CHARS:,} characters: " + ", ".join(cut) + ".")
    return result


# ------------------------------------------------------------------ bundling


_MARKER_LINE = re.compile(r"(?im)^([ \t]*)={5,}([ \t]*(?:END[ \t]+)?FILE[ \t]*:)")


def _defang(text: str) -> str:
    """Stop a student's file from containing lines that look like our own file markers."""
    return _MARKER_LINE.sub(r"\1= = = = =\2", text)


def _bundle(files: list[LoadedFile]) -> str:
    """The layout described by prompts.LAYOUT_NOTE: a file list, then each file between markers."""
    listing = "\n".join(f"- {_marker_name(f.name)}" for f in files)
    parts = [f"Files in this submission ({len(files)}):\n{listing}"]
    for f in files:
        name = _marker_name(f.name)
        if f.text is None:
            body = f"[content not provided: {f.note}]"
        elif not f.text.strip():
            body = "[content not provided: the file is empty]"
        else:
            body = _defang(neutralize(f.text, "submission"))
        parts.append(f"===== FILE: {name} =====\n{body}\n===== END FILE: {name} =====")
    return "\n\n".join(parts)


def _marker_name(name: str) -> str:
    return re.sub(r"[\r\n]+", " ", name).strip()


def build_submission(loaded: LoadResult, pasted: str = "", pasted_name: str = "") -> Submission:
    """Combine uploaded files and any pasted text into the text the grader will read.

    Raises ValueError, with a message fit to show the user, when there is nothing to grade
    or the submission is too large to send in full.
    """
    pasted, pasted_name = pasted.strip(), pasted_name.strip()
    warnings = list(loaded.warnings)

    if not loaded.files:
        if not pasted:
            raise ValueError("Upload at least one file, or paste the submission text.")
        if len(pasted) > MAX_TOTAL_CHARS:
            raise ValueError(f"The pasted submission is {len(pasted):,} characters; the limit is {MAX_TOTAL_CHARS:,}.")
        return Submission(text=pasted, files=[], archives=[], names=[pasted_name] if pasted_name else [], warnings=warnings)

    files = [LoadedFile(**vars(f)) for f in loaded.files]  # copies, so cached results stay untouched
    if pasted:
        files.append(_finish(pasted_name or "(pasted text)", "pasted", pasted, len(pasted.encode("utf-8"))))
        _dedupe(files)
    if len(files) > MAX_FILES:
        raise ValueError(f"{len(files)} files were provided; the limit is {MAX_FILES}. Remove some files and try again.")

    if all(not f.readable for f in files):
        raise ValueError("None of the uploaded files could be read, so there is nothing to grade. See the file list for why.")

    text = _bundle(files)
    if len(text) > MAX_TOTAL_CHARS:
        raise ValueError(
            f"The submission is {len(text):,} characters in total; the limit is {MAX_TOTAL_CHARS:,}. "
            "Remove large files that are not part of the grade (data files, build output) and try again."
        )
    return Submission(
        text=text,
        files=files,
        archives=list(loaded.archives),
        names=[f.name for f in files],
        warnings=warnings,
    )
