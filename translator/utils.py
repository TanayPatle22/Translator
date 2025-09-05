from typing import List
from deep_translator import GoogleTranslator
import google.generativeai as genai
import fitz  # PyMuPDF
import io, os

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from django.conf import settings

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
    prompt = f"Translate the following text from {source_lang} to {target_lang}, IMPORTANT INSTRUCTIONS:- Output ONLY the translated text.- Do NOT include explanations, notes, romanizations, or comments.- Do NOT add headers like translation. - Keep the same formatting, line breaks, and numbering as the input. - If the text cannot be translated (e.g., proper nouns), leave it unchanged.:\n{text}"

    model = genai.GenerativeModel("gemini-1.5-flash")  # you can use gemini-pro / gemini-1.5-pro too
    response = model.generate_content(prompt)

    return response.text.strip() if response.text else ""

def gemini_translate_chunks(chunks: List[str], source_lang: str, target_lang: str) -> str:
    """
    Translate large text in chunks using Gemini.
    """
    translated_chunks = []
    for chunk in chunks:
        translated = gemini_translate_text(chunk, source_lang, target_lang)
        translated_chunks.append(translated)
    return "\n\n".join(translated_chunks)

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
        else:
            translated_blocks.append(block)

    return translated_blocks

def rebuild_pdf(translated_pages, target_lang="default"):
    """
    Create a PDF with translated text + images + tables, keeping block positions & order.
    """
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

    c.setFont(font_name, 12)

    for page in translated_pages:
        for block in page["blocks"]:
            btype = block["type"]

            if btype == "text":
                x0, y0, x1, y1 = block["bbox"]
                w, h = (x1 - x0), (y1 - y0)
                y = height - y1  # flip coords

                text = block.get("text", "")
                if text:
                    text_obj = c.beginText(x0, y + h - 12)  # start from top of box
                    text_obj.setFont(font_name, 12)

                from reportlab.pdfbase.pdfmetrics import stringWidth
                max_width = w
                for line in text.split("\n"):
                    words = line.split(" ")
                    current_line = ""
                    for word in words:
                        trial_line = (current_line + " " + word).strip()
                        if stringWidth(trial_line, font_name, 12) <= max_width:
                            current_line = trial_line
                        else:
                            text_obj.textLine(current_line)
                            current_line = word
                    if current_line:
                        text_obj.textLine(current_line)

                c.drawText(text_obj)

            elif btype == "image":
                # Assuming block["image"] holds raw image bytes (from extract)
                if "image" in block:
                    x0, y0, x1, y1 = block["bbox"]
                    w, h = (x1 - x0), (y1 - y0)
                    y = height - y1
                    img_data = io.BytesIO(block["image"])
                    c.drawImage(img_data, x0, y, width=w, height=h, preserveAspectRatio=True, mask="auto")


            # elif btype == "table":
            #     # For now, render table as text (CSV-like). 
            #     # Later, can use reportlab.platypus Table for full grid rendering.
            #     rows = block.get("rows", [])
            #     if rows:
            #         x0, y0, x1, y1 = block["bbox"]
            #         y = height - y1
            #         for ridx, row in enumerate(rows):
            #             line = " | ".join(row)
            #             c.drawString(x0, y - (ridx * 14), line)


            elif btype == "other":
                # For now just preserve bounding box placeholder (optional)
                x0, y0, x1, y1 = block["bbox"]
                y = height - y1
                c.rect(x0, y, (x1 - x0), (y1 - y0), stroke=1, fill=0)  # draw placeholder box

        c.showPage()  # next page

    c.save()
    buffer.seek(0)
    return buffer.getvalue()