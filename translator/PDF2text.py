
import fitz  # PyMuPDF
from typing import Union, IO

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
                page_data["blocks"].append({
                    "type": "image",
                    "bbox": b["bbox"],
                    "image": img_bytes   # store bytes now for future OCR
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

# import PyPDF2
# from typing import Union, IO

# def extract_text_from_pdf(pdf_file: Union[str, IO]) -> str:

#     import logging
#     logger = logging.getLogger(__name__)
#     logger.info("Starting PDF text extraction...")

#      # Handle both path string and file-like object (e.g. Django UploadedFile)
#     if isinstance(pdf_file, str):
#         logger.info(f"Opening PDF from path: {pdf_file}")
#         file_obj = open(pdf_file, "rb")
#         close_after = True
#     else:
#         logger.info("Opening PDF from file-like object")
#         file_obj = pdf_file
#         close_after = False

#     try:
#         reader = PyPDF2.PdfReader(file_obj, strict=False)
#         pdf_text = []
#         logger.info(f"PDF loaded. Total pages: {len(reader.pages)}")

#         for i, page in enumerate(reader.pages, start=1):
#             logger.debug(f"Extracting text from page {i}")
#             content = page.extract_text() or ""  # fallback if empty
#             pdf_text.append(f"--- Page {i} ---\n\n{content.strip()}")

#         logger.info("✅ PDF text extraction complete")
#         return "\n\n".join(pdf_text)

#     finally:
#         if close_after:
#             file_obj.close()


# if __name__ == "__main__":
#     # Test with a local file
#     extracted_text = extract_text_from_pdf("/Users/tanay/Desktop/core/translator/lecs102.pdf")
#     print(extracted_text[:1000])  # print first 1000 chars

#     with open(pdf_file, 'rb') as file:
#         reader = PyPDF2.PdfReader(file, strict=False)
#         pdf_text= []

#         for page in reader.pages:
#             content = page.extract_text()
#             pdf_text.append(content)
#     return pdf_text


# if __name__ == "__main__":
#     extracted_text = extract_text_from_pdf("/Users/tanay/Desktop/core/translator/lecs102.pdf")
#     for text in extracted_text:
#         print(text)