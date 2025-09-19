#PDF2text.py
import fitz  # PyMuPDF
from typing import Union, IO
import pytesseract
from PIL import Image  
import io   
import logging
logger = logging.getLogger(__name__)

def extract_blocks_from_pdf(pdf_file: Union[str, IO]):
    """
    Extracts structured blocks (text, images, tables) from PDF.
    Returns: { "pages": [ { "number": int, "blocks": [...] } ] }
    """
    if isinstance(pdf_file, str):
        doc = fitz.open(pdf_file)

    elif isinstance(pdf_file, bytes):
        doc = fitz.open(stream=pdf_file, filetype="pdf")

    else:
        pdf_file.seek(0)
        doc = fitz.open(stream=pdf_file.read(), filetype="pdf")

    results = {"pages": []}
    for page_num, page in enumerate(doc, start=1):
        blocks = page.get_text("dict")["blocks"]  # structured content
        page_data = {"number": page_num, "blocks": []}

        for b in blocks:
            if "lines" in b:  
                # Text block
                text = " ".join(
                    span["text"] for line in b["lines"] for span in line["spans"]
                )
                page_data["blocks"].append({
                    "type": "text",
                    "bbox": b["bbox"],  # (x0, y0, x1, y1)
                    "text": text.strip()
                })
            elif "image" in b:
                pix = page.get_pixmap(clip=b["bbox"])
                img_bytes = pix.tobytes("png")

                # OCR here 👇
                image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                ocr_text = pytesseract.image_to_string(image).strip()
                logging.debug(f"[OCR] Page {page_num} image bbox={b['bbox']} -> '{ocr_text[:80]}'")

                page_data["blocks"].append({
                    "type": "image",
                    "bbox": b["bbox"],
                    "image_text": ocr_text if ocr_text else None  # store OCR text
                })
            else:
                # Could be table borders, drawings, etc.
                page_data["blocks"].append({
                    "type": "other",
                    "bbox": b["bbox"]
                })

        results["pages"].append(page_data)

    return results

def extract_text_for_validation(pdf_file: Union[str, IO]) -> str:
    """
    Extracts plain text (like old PyPDF2 method) just for validation.
    """
    data = extract_blocks_from_pdf(pdf_file)
    text_content = []
    for page in data["pages"]:
        page_text = " ".join([b["text"] for b in page["blocks"] if b["type"] == "text"])
        text_content.append(f"--- Page {page['number']} ---\n\n{page_text}")
    return "\n\n".join(text_content)

