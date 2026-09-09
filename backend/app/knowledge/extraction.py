import json
import os
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pymupdf


def extract(path):
    extension = path.suffix.lower()
    if extension == ".pdf":
        with pymupdf.open(path) as document:
            if document.needs_pass:
                raise ValueError("encrypted_pdf")
            pages = []
            for number, page in enumerate(document, 1):
                try:
                    # Preserve the PDF's reading stream. PyMuPDF's visual line sort
                    # interleaves left/right columns in the local rulebooks.
                    text = page.get_text(sort=False)
                    pages.append(
                        dict(
                            physical_page=number,
                            page_label=page.get_label() or None,
                            page_kind="pdf",
                            text=text,
                        )
                    )
                except Exception:
                    pages.append(
                        dict(
                            physical_page=number,
                            page_label=None,
                            page_kind="pdf",
                            text="",
                            error="extraction_failed",
                        )
                    )
            return pages
    if extension == ".doc":
        if os.name != "nt":
            raise ValueError("word_required")
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(Path(__file__).with_name("word_extract.ps1")),
                "-InputDocument",
                str(path),
            ],
            capture_output=True,
            timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            raise ValueError("word_extraction_failed")
        return json.loads(result.stdout.decode("utf-8-sig"))
    if extension == ".docx":
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > 30_000_000:
                raise ValueError("document_too_large")
            root = ET.fromstring(archive.read(info))
        namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        text = "\n\n".join(
            "".join(node.text or "" for node in p.iter(namespace + "t"))
            for p in root.iter(namespace + "p")
        )
        # DOCX XML has no reliable physical pagination; never invent PDF page numbers.
        return [dict(physical_page=None, page_label=None, page_kind="text", text=text)]
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = path.read_text(encoding=encoding)
            return [dict(physical_page=None, page_label=None, page_kind="text", text=text)]
        except UnicodeDecodeError:
            continue
    raise ValueError("text_encoding_failed")
