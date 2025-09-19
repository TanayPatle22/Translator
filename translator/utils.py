#utils.py

from typing import List
from deep_translator import GoogleTranslator
import google.generativeai as genai
import fitz  # PyMuPDF
import io, os
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import stringWidth
from django.conf import settings

import logging
logger = logging.getLogger(__name__)

FONTS = settings.FONTS
FONT_PATH = settings.FONT_PATH


LANGUAGE_SLUGS = {
    'en': 'English',
    'es': 'Spanish',
    'zh': 'Chinese',
    'hi': 'Hindi',
    'ar': 'Arabic',
    'pt': 'Portuguese',
    'bn': 'Bengali',
    'ru': 'Russian',
    'ur': 'Urdu',
    'fr': 'French',
    'id': 'Indonesian',
    'de': 'German',
    'ja': 'Japanese',
    'pa': 'Punjabi',
    'te': 'Telugu',
    'mr': 'Marathi',
    'vi': 'Vietnamese',
    'ko': 'Korean',
    'ta': 'Tamil',
    'it': 'Italian',
    'tr': 'Turkish',
    'th': 'Thai',
    'gu': 'Gujarati',
    'pl': 'Polish',
    'uk': 'Ukrainian',
    'nl': 'Dutch',
    'fa': 'Persian',
    'ms': 'Malay',
    'ja': 'Japanese',
    'sv': 'Swedish',
    'he': 'Hebrew',
    'ro': 'Romanian',
    'hu': 'Hungarian',
    'cs': 'Czech',
    'fi': 'Finnish',
    'el': 'Greek',
    'da': 'Danish',
    'bg': 'Bulgarian',
    'sk': 'Slovak',
    'hr': 'Croatian',
    'sl': 'Slovenian',
    'no': 'Norwegian',
    'sq': 'Albanian',
    'lt': 'Lithuanian',
    'lv': 'Latvian',
    'et': 'Estonian',
    'mk': 'Macedonian',
    'is': 'Icelandic',
    'ga': 'Irish',
    'kk': "Kazakh",
}



def chunk_text(text: str, max_chars: int = 4000) -> List[str]:
    """
    Split text into chunks under `max_chars`.
    Prefers paragraph boundaries, falls back to safe char slicing.
    """
    paragraphs = text.split("\n\n")
    chunks, current_chunk = [], ""

    for para in paragraphs:
        if len(current_chunk) + len(para) + 2 <= max_chars:
            current_chunk += para + "\n\n"
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            # If paragraph itself is larger than max_chars, break it further
            if len(para) > max_chars:
                start = 0
                while start < len(para):
                    end = min(start + max_chars, len(para))
                    chunks.append(para[start:end])
                    start = end
            else:
                current_chunk = para + "\n\n"

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks


def translate_chunks(chunks: List[str], source_lang: str, target_lang: str) -> str:
    """
    Translate a list of chunks and reassemble them.
    """
    translated_chunks = []
    for chunk in chunks:
        translated = GoogleTranslator(
            source=source_lang,
            target=target_lang
        ).translate(chunk)
        translated_chunks.append(translated)
    return "\n\n".join(translated_chunks)


def gemini_translate_text(text: str, source_lang: str, target_lang: str) -> str:
    """
    Translate text using Gemini model.
    """

    logger.debug("Initializing Gemini model…")
    model = genai.GenerativeModel("gemini-1.5-flash")  # you can use gemini-pro / gemini-1.5-pro too
    prompt = f"Translate the following text from {source_lang} to {target_lang}, IMPORTANT INSTRUCTIONS:- Output ONLY the translated text.- Do NOT include explanations, notes, romanizations, or comments.- Do NOT add headers like translation. - Keep the same formatting, line breaks, and numbering as the input. - If the text cannot be translated (e.g., proper nouns), leave it unchanged.:\n{text}"
    logger.debug(f"Prompt prepared (len={len(prompt)}): {prompt[:100]}...")
    
    try:
        response = model.generate_content(prompt, request_options={"timeout": 60})
        logger.debug(f"Raw Gemini response: {response}")

        # Try safest extraction
        if hasattr(response, "text") and response.text:
            return response.text.strip()
        elif hasattr(response, "candidates") and response.candidates:
            return response.candidates[0].content.parts[0].text.strip()
        else:
            logger.warning(f"Unexpected Gemini response format: {response}")
            return ""
    except Exception as e:
        logger.error(f"Gemini API call failed: {e}", exc_info=True)
        return ""

def gemini_translate_chunks(chunks: List[str], source_lang: str, target_lang: str) -> str:
    """
    Translate large text in chunks using Gemini.
    """
    translated_chunks = []
    for chunk in chunks:
        translated = gemini_translate_text(chunk, source_lang, target_lang)
        translated_chunks.append(translated)
    return "\n\n".join(translated_chunks)

def _run_translation(text, source_lang, target_lang, engine):
    """Helper: translate text with Google/Gemini depending on length."""
    if engine == "google":
        if len(text) <= 4000:
            return GoogleTranslator(source=source_lang, target=target_lang).translate(text)
        else:
            chunks = chunk_text(text)
            return translate_chunks(chunks, source_lang, target_lang)
    elif engine == "gemini":
        if len(text) <= 4000:
            return gemini_translate_text(text, source_lang, target_lang)
        else:
            chunks = chunk_text(text)
            return gemini_translate_chunks(chunks, source_lang, target_lang)
    return text

def translate_blocks(blocks, source_lang, target_lang, engine="google"):
    """
    Translate only text blocks, keep images/others unchanged.
    Returns updated blocks list.
    """
    translated_blocks = []

    for block in blocks:
        if block["type"] == "text":
            text = block.get("text", "").strip()
            if not text:
                translated_blocks.append(block)
                continue
            # Pick engine
            if engine == "google":
                if len(text) <= 4000:
                    translated = GoogleTranslator(
                        source=source_lang,
                        target=target_lang
                    ).translate(text)
                else:
                    chunks = chunk_text(text)
                    translated = translate_chunks(chunks, source_lang, target_lang)

                
            elif engine == "gemini":
                if len(text) <= 4000:
                    translated = gemini_translate_text(text, source_lang, target_lang)
                else:
                    chunks = chunk_text(text)
                    translated = gemini_translate_chunks(chunks, source_lang, target_lang)

            else:
                translated = text  # fallback

            block["text"] = translated
            translated_blocks.append(block)

        elif block["type"] == "image" and block.get("image_text"):
            text = block["image_text"]
            logger.debug(f"[TRANSLATE_BLOCKS] OCR raw: '{text[:80]}'")

            translated = _run_translation(text, source_lang, target_lang, engine)
            logger.debug(f"[TRANSLATE_BLOCKS] OCR translated: '{translated[:80]}'")

            # keep original image block
            translated_blocks.append(block)

            # insert a new text block (just below the image)
            x0, y0, x1, y1 = block["bbox"]
            caption_block = {
                "type": "text",
                "bbox": (x0, y0 - 30, x1, y0 - 10),  # small box below image
                "text": f'Translated text from image - "{translated}"'
            }
            logger.debug(f"[TRANSLATE_BLOCKS] Inserted caption block: {caption_block}")
            translated_blocks.append(caption_block)

        else:
            translated_blocks.append(block)

    return translated_blocks

def rebuild_pdf(translated_pages, original_pdf_path, target_lang="default"):
    """
    Create a PDF with translated text + images + tables, keeping block positions & order.
    """

    doc = fitz.open(original_pdf_path)

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # Pick correct font for target_lang
    try:
        font_name, font_file = FONTS.get(target_lang, FONTS["default"])
    except:
        font_name, font_file = FONTS["default"]  # always fallback to NotoSans

    font_path = os.path.join(FONT_PATH, font_file)
    try:
        pdfmetrics.registerFont(TTFont(font_name, font_path))
    except:
        # fallback → NotoSans only (not Helvetica)
        default_font, default_file = FONTS["default"]
        pdfmetrics.registerFont(TTFont(default_font, os.path.join(FONT_PATH, default_file)))
        font_name = default_font

    font_size = 9
    c.setFont(font_name, font_size)

    def bbox_equal(a, b, tol=1.0):
        """Compare two bboxes with small tolerance (points)."""
        if not a or not b:
            return False
        a_t = tuple(float(x) for x in a)
        b_t = tuple(float(x) for x in b)
        return all(abs(a_t[i] - b_t[i]) <= tol for i in range(4))
    
    for page_index, page in enumerate(doc):
        blocks = page.get_text("rawdict")["blocks"]
        translated_blocks = translated_pages[page_index]["blocks"]

        # for block in blocks:
        for i, block in enumerate(blocks):
            translated_text = None
            tb = translated_blocks[i] if i < len(translated_blocks) else {}

            # CHANGE: try index-based match first, then bbox-based match
            if tb.get("type") == "text":
                translated_text = tb.get("text", "").strip()
            if not translated_text:
                for tb2 in translated_blocks:
                    if tb2.get("type") == "text" and bbox_equal(tb2.get("bbox"), block.get("bbox")):
                        translated_text = tb2.get("text", "").strip()
                        break

            # CHANGE: improved text drawing with line spacing and better y-coordinates
            if "lines" in block and translated_text:
                
                x0, y0, x1, y1 = block["bbox"]
                w, h = (x1 - x0), (y1 - y0)
                y_top = height - y1  # convert coords

                text_obj = c.beginText(x0, y_top + h - font_size)
                text_obj.setFont(font_name, font_size)
                text_obj.setLeading(font_size + 2)  # line spacing

                max_width = w  # block width
                for line in translated_text.split("\n"):
                    words = line.split(" ")
                    current_line = ""
                    for word in words:
                        trial_line = (current_line + " " + word).strip()
                        if stringWidth(trial_line, font_name, font_size) <= max_width:
                            current_line = trial_line
                        else:
                            text_obj.textLine(current_line)
                            current_line = word
                    if current_line:
                        text_obj.textLine(current_line)
                c.drawText(text_obj)

            elif "image" in block:
                image_list = page.get_images(full=True)
                for img_index, img in enumerate(image_list):
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    img_bytes = base_image["image"]

                    x0, y0, x1, y1 = block["bbox"]
                    w, h = (x1 - x0), (y1 - y0)

                    # 👇 Fix here
                    c.drawImage(ImageReader(io.BytesIO(img_bytes)), x0, height - y1,
                                width=w, height=h, preserveAspectRatio=True, mask="auto")
                    
                    if "ocr_text" in block and block["ocr_text"].strip():
                        ocr_text = block["ocr_text"]
                        logger.debug(f"[PDF REBUILD] Drawing OCR text at {block['bbox']} -> '{ocr_text[:80]}'")

                        text_obj = c.beginText(x0, (height - y1) - font_size - 5)
                        text_obj.setFont(font_name, font_size)
                        text_obj.setLeading(font_size + 2)
                        for line in ocr_text.split("\n"):
                            text_obj.textLine(line)
                        c.drawText(text_obj)
                    break


            elif tb.get("type") == "table":
                rows = tb.get("rows", [])
                if rows:
                    x0, y0, x1, y1 = block["bbox"]
                    y = height - y1
                    for ridx, row in enumerate(rows):
                        line = " | ".join(row)
                        c.drawString(x0, y - (ridx * (font_size + 2)), line)


            else:
                # For now just preserve bounding box placeholder (optional)
                x0, y0, x1, y1 = block["bbox"]
                y = height - y1
                c.rect(x0, y, (x1 - x0), (y1 - y0), stroke=1, fill=0)  # draw placeholder box

        c.showPage()  # next page

        # for tb in translated_blocks:
        #     if tb.get("type") == "text" and tb.get("text") and tb["bbox"]:
        #         x0, y0, x1, y1 = tb["bbox"]
        #         y_top = height - y1
        #         caption_text = tb["text"]

        #         logger.debug(f"[PDF REBUILD] Drawing extra text at {tb['bbox']} -> '{caption_text[:80]}'")

        #         text_obj = c.beginText(x0, y_top + (y1 - y0) - font_size)
        #         text_obj.setFont(font_name, font_size)
        #         text_obj.setLeading(font_size + 2)
        #         for line in caption_text.split("\n"):
        #             text_obj.textLine(line)
        #         c.drawText(text_obj)
        
        # logger.debug(f"[PDF REBUILD] Completed Page {page_index+1}, drew {len(translated_blocks)} translated blocks")


    c.save()
    buffer.seek(0)
    return buffer.getvalue()