from __future__ import annotations

import argparse
from pathlib import Path

import pymupdf


PAGE_WIDTH = 612
PAGE_HEIGHT = 792


def _insert_textbox(
    page: pymupdf.Page,
    rect: tuple[float, float, float, float],
    text: str,
    *,
    fontsize: float,
    align: int = pymupdf.TEXT_ALIGN_LEFT,
) -> None:
    remaining = page.insert_textbox(
        pymupdf.Rect(rect), text, fontname="helv", fontsize=fontsize, align=align
    )
    if remaining < 0:
        raise RuntimeError(f"Demo text did not fit in {rect}: {text[:40]}")


def generate_layout_demo_pdf(output_path: str | Path, page_count: int = 3) -> Path:
    """Create a deterministic, copyright-free two-column parsing fixture."""
    if page_count < 3:
        raise ValueError("page_count must be at least 3 to demonstrate repeated margins")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    pdf = pymupdf.open()
    pdf.set_metadata({
        "title": "Layout-Aware Scientific Paper Parsing",
        "author": "Ada Example; Lin Demo",
    })
    for page_number in range(1, page_count + 1):
        page = pdf.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        _insert_textbox(
            page,
            (55, 24, 557, 45),
            "Proceedings of the Deterministic Parsing Workshop",
            fontsize=8,
            align=pymupdf.TEXT_ALIGN_CENTER,
        )
        _insert_textbox(
            page,
            (55, 82, 557, 116),
            f"{page_number} A Deterministic Layout-Aware Scientific Paper Parsing System",
            fontsize=16,
            align=pymupdf.TEXT_ALIGN_CENTER,
        )
        _insert_textbox(
            page,
            (55, 132, 280, 620),
            (
                f"Left column page {page_number}. The first column must be read before the "
                "second column. A deterministic transfor-\nmation rejoins a Latin word "
                "inside one positioned block. The well-known method keeps ordinary "
                "compound hyphens intact.\n\n"
                "1 Introduction\nThe layout model uses coordinates and font sizes without "
                "calling an external service. Similar Article Notes remain body content."
            ),
            fontsize=10,
        )
        _insert_textbox(
            page,
            (332, 132, 557, 620),
            (
                f"Right column page {page_number}. This sentence follows every sentence in "
                "the left column. Search and question answering consume the corrected "
                "reading order. Formula x-y and Chinese text 中文-公式 remain unchanged.\n\n"
                "2 Results\nThe parser records page-local character ranges and stable block "
                "identifiers for later highlighting."
            ),
            fontsize=10,
        )
        _insert_textbox(
            page,
            (55, 650, 557, 684),
            (
                f"Figure {page_number}. A full-width caption follows both columns and records "
                "the deterministic final reading order for review."
            ),
            fontsize=9,
            align=pymupdf.TEXT_ALIGN_CENTER,
        )
        _insert_textbox(
            page,
            (55, 738, 557, 754),
            "Layout Parsing Workshop · Demonstration Copy",
            fontsize=8,
            align=pymupdf.TEXT_ALIGN_CENTER,
        )
        _insert_textbox(
            page,
            (280, 760, 332, 776),
            str(page_number),
            fontsize=8,
            align=pymupdf.TEXT_ALIGN_CENTER,
        )
    pdf.save(output)
    pdf.close()
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output",
        nargs="?",
        default="backend/data/raw/layout_demo.pdf",
        help="Output PDF path",
    )
    args = parser.parse_args()
    path = generate_layout_demo_pdf(args.output)
    print(path)


if __name__ == "__main__":
    main()
