"""PDFReaderSkill — extract text from a local PDF file."""

from __future__ import annotations

import asyncio
from pathlib import Path

from seraphim.skills.base import BaseSkill, SkillResult

_MAX_CHARS = 12_000  # ~3 k tokens — enough for the agent to summarise


class PDFReaderSkill(BaseSkill):
    name = "pdf_reader"
    description = (
        "Extract text from a local PDF file. "
        "Use when the user provides a PDF path and asks to read, summarize, or analyze it. "
        "Returns raw extracted text; the agent then summarizes or answers questions about it."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or relative path to the PDF file",
            },
            "pages": {
                "type": "string",
                "description": "Page range to extract, e.g. '1-5' or '3'. Default: all pages.",
                "default": "",
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters to return (default: 12000)",
                "default": _MAX_CHARS,
            },
        },
        "required": ["path"],
    }

    async def run(
        self,
        path: str,
        pages: str = "",
        max_chars: int = _MAX_CHARS,
        **kwargs,
    ) -> SkillResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._extract, path, pages, max_chars)

    def _extract(self, path: str, pages: str, max_chars: int) -> SkillResult:
        p = Path(path).expanduser()
        if not p.exists():
            return SkillResult(success=False, output="", error=f"Fichier introuvable : {path}")
        if p.suffix.lower() != ".pdf":
            return SkillResult(success=False, output="", error=f"Pas un PDF : {path}")

        page_set: set[int] | None = None
        if pages.strip():
            try:
                page_set = _parse_page_range(pages)
            except ValueError as e:
                return SkillResult(success=False, output="", error=str(e))

        text, lib = _try_pymupdf(p, page_set)
        if text is None:
            text, lib = _try_pypdf(p, page_set)
        if text is None:
            text, lib = _try_pdfplumber(p, page_set)
        if text is None:
            return SkillResult(
                success=False,
                output="",
                error=(
                    "Aucune bibliothèque PDF disponible. Installe l'une de :\n"
                    "  pip install pymupdf      # recommandé\n"
                    "  pip install pypdf         # léger\n"
                    "  pip install pdfplumber    # meilleur pour les tableaux"
                ),
            )

        text = text.strip()
        total = len(text)
        if total > max_chars:
            text = text[:max_chars] + f"\n\n[… tronqué — {total} caractères au total]"

        header = f"[PDF : {p.name} | extrait via {lib} | {total} caractères"
        if page_set:
            header += f" | pages {pages}"
        header += "]\n\n"

        return SkillResult(success=True, output=header + text)


# ── Page range parser ─────────────────────────────────────────────────────────

def _parse_page_range(s: str) -> set[int]:
    pages: set[int] = set()
    for part in s.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            pages.update(range(int(a), int(b) + 1))
        else:
            pages.add(int(part))
    return pages


# ── Extractors (tried in order) ───────────────────────────────────────────────

def _try_pymupdf(p: Path, pages: set[int] | None) -> tuple[str | None, str]:
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(p))
        parts = [
            page.get_text()
            for i, page in enumerate(doc, 1)
            if pages is None or i in pages
        ]
        doc.close()
        return "\n\n".join(parts), "PyMuPDF"
    except ImportError:
        return None, ""
    except Exception as e:
        return f"[PyMuPDF erreur : {e}]", "PyMuPDF"


def _try_pypdf(p: Path, pages: set[int] | None) -> tuple[str | None, str]:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(p))
        parts = [
            (page.extract_text() or "")
            for i, page in enumerate(reader.pages, 1)
            if pages is None or i in pages
        ]
        return "\n\n".join(parts), "pypdf"
    except ImportError:
        return None, ""
    except Exception as e:
        return f"[pypdf erreur : {e}]", "pypdf"


def _try_pdfplumber(p: Path, pages: set[int] | None) -> tuple[str | None, str]:
    try:
        import pdfplumber
        with pdfplumber.open(str(p)) as pdf:
            parts = [
                (page.extract_text() or "")
                for i, page in enumerate(pdf.pages, 1)
                if pages is None or i in pages
            ]
        return "\n\n".join(parts), "pdfplumber"
    except ImportError:
        return None, ""
    except Exception as e:
        return f"[pdfplumber erreur : {e}]", "pdfplumber"
