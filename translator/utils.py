#utils.py

from typing import List, Optional, Dict, Any
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
from dataclasses import dataclass

import logging
logger = logging.getLogger(__name__)

FONTS = settings.FONTS
FONT_PATH = settings.FONT_PATH


LANGUAGE_SLUGS = {
    'en': 'English', 'es': 'Spanish', 'zh': 'Chinese', 'hi': 'Hindi',
    'ar': 'Arabic', 'pt': 'Portuguese', 'bn': 'Bengali', 'ru': 'Russian',
    'ur': 'Urdu', 'fr': 'French', 'id': 'Indonesian', 'de': 'German',
    'ja': 'Japanese', 'pa': 'Punjabi', 'te': 'Telugu', 'mr': 'Marathi',
    'vi': 'Vietnamese', 'ko': 'Korean', 'ta': 'Tamil', 'it': 'Italian',
    'tr': 'Turkish', 'th': 'Thai', 'gu': 'Gujarati', 'pl': 'Polish',
    'uk': 'Ukrainian', 'nl': 'Dutch', 'fa': 'Persian', 'ms': 'Malay',
    'sv': 'Swedish', 'he': 'Hebrew', 'ro': 'Romanian', 'hu': 'Hungarian',
    'cs': 'Czech', 'fi': 'Finnish', 'el': 'Greek', 'da': 'Danish',
    'bg': 'Bulgarian', 'sk': 'Slovak', 'hr': 'Croatian', 'sl': 'Slovenian',
    'no': 'Norwegian', 'sq': 'Albanian', 'lt': 'Lithuanian', 'lv': 'Latvian',
    'et': 'Estonian', 'mk': 'Macedonian', 'is': 'Icelandic', 'ga': 'Irish',
    'kk': 'Kazakh',
}

@dataclass
class TranslationResult:
    """Simple result class for translation operations."""
    original_text: str
    translated_text: str
    source_lang: str
    target_lang: str
    engine: str
    success: bool
    error_message: Optional[str] = None


class SimpleTranslationManager:
    """Basic translation manager compatible with your existing code."""
    
    def translate_text(self, text: str, source_lang: str, target_lang: str, 
                      engine: str = "google") -> TranslationResult:
        """Translate text using specified engine."""
        
        if not text or not text.strip():
            return TranslationResult(text, "", source_lang, target_lang, engine, False, "Empty text")
        
        try:
            if engine == "google":
                if len(text) <= 4000:
                    translated = GoogleTranslator(
                        source=source_lang, target=target_lang
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
                return TranslationResult(text, text, source_lang, target_lang, engine, False, "Unknown engine")
            
            return TranslationResult(text, translated, source_lang, target_lang, engine, True)
            
        except Exception as e:
            logger.error(f"Translation failed: {e}")
            return TranslationResult(text, text, source_lang, target_lang, engine, False, str(e))

# Create global translation manager instance
translation_manager = SimpleTranslationManager()

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
    model = genai.GenerativeModel("gemini-1.5")  # you can use gemini-pro / gemini-1.5-pro too
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


def rebuild_pdf(translated_pages, original_pdf_path, target_lang="default"):
    """
    Enhanced PDF rebuilding compatible with new span-based structure.
    """
    doc = fitz.open(original_pdf_path)
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # Pick correct font for target_lang
    try:
        font_name, font_file = FONTS.get(target_lang, FONTS["default"])
    except:
        font_name, font_file = FONTS["default"]

    font_path = os.path.join(FONT_PATH, font_file)
    try:
        pdfmetrics.registerFont(TTFont(font_name, font_path))
    except:
        default_font, default_file = FONTS["default"]
        pdfmetrics.registerFont(TTFont(default_font, os.path.join(FONT_PATH, default_file)))
        font_name = default_font

    font_size = 11
    c.setFont(font_name, font_size)

    def bbox_equal(a, b, tol=1.0):
        """Compare two bboxes with small tolerance (points)."""
        if not a or not b:
            return False
        a_t = tuple(float(x) for x in a)
        b_t = tuple(float(x) for x in b)
        return all(abs(a_t[i] - b_t[i]) <= tol for i in range(4))

    for page_index, page in enumerate(doc):
        if page_index >= len(translated_pages):
            break
            
        original_blocks = page.get_text("rawdict")["blocks"]
        translated_page = translated_pages[page_index]
        translated_blocks = translated_page.get("blocks", [])

        for i, original_block in enumerate(original_blocks):
            try:
                # Find corresponding translated block
                translated_block = translated_blocks[i] if i < len(translated_blocks) else {}
                
                if "lines" in original_block:
                    # Handle text blocks - check for new span-based structure
                    if "lines" in translated_block and translated_block.get("type") == "text":
                        # New format with preserved span structure
                        render_enhanced_text_block(c, translated_block, height, font_name, font_size)
                    elif translated_block.get("text"):
                        # Fallback to simple text rendering
                        render_simple_text_block(c, original_block, translated_block.get("text"), 
                                                height, font_name, font_size)
                
                elif "image" in original_block:
                    # Handle image blocks
                    render_image_block(c, page, original_block, translated_block, height, font_name, font_size)
                    
            except Exception as e:
                logger.error(f"Error rendering block {i} on page {page_index + 1}: {e}")
                continue

        c.showPage()

    c.save()
    buffer.seek(0)
    return buffer.getvalue()


def render_enhanced_text_block(c, translated_block, height, font_name, font_size):
    """Render text block with preserved span structure."""
    try:
        for line in translated_block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if not text:
                    continue
                
                # Get span position
                origin = span.get("origin", [0, 0])
                x, y = origin[0], height - origin[1]
                
                # Set font size from span if available
                span_font = span.get("font", font_name)
                span_size = span.get("size", font_size)
                try:
                    c.setFont(span_font, span_size)
                except:
                    c.setFont(font_name, font_size)
                
                # Draw text at original position
                c.drawString(x, y, text)
                
    except Exception as e:
        logger.error(f"Enhanced text block rendering failed: {e}")

def render_simple_text_block(c, original_block, translated_text, height, font_name, font_size):
    """Fallback simple text block rendering."""
    try:
        x0, y0, x1, y1 = original_block["bbox"]
        w, h = (x1 - x0), (y1 - y0)
        y_top = height - y1

        text_obj = c.beginText(x0, y_top + h - font_size)
        text_obj.setFont(font_name, font_size)
        text_obj.setLeading(font_size + 2)

        max_width = w
        for line in translated_text.split("\n"):
            words = line.split(" ")
            current_line = ""
            for word in words:
                trial_line = (current_line + " " + word).strip()
                if stringWidth(trial_line, font_name, font_size) <= max_width:
                    current_line = trial_line
                else:
                    if current_line:
                        text_obj.textLine(current_line)
                    current_line = word
            if current_line:
                text_obj.textLine(current_line)
        
        c.drawText(text_obj)
        
    except Exception as e:
        logger.error(f"Simple text block rendering failed: {e}")

def render_image_block(c, page, original_block, translated_block, height, font_name, font_size):
    """Render image blocks with OCR text if available."""
    try:
        # Draw the image
        image_list = page.get_images(full=True)
        for img_index, img in enumerate(image_list):
            xref = img[0]
            base_image = page.parent.extract_image(xref)
            img_bytes = base_image["image"]

            x0, y0, x1, y1 = original_block["bbox"]
            w, h = (x1 - x0), (y1 - y0)

            c.drawImage(ImageReader(io.BytesIO(img_bytes)), x0, height - y1,
                       width=w, height=h, preserveAspectRatio=True, mask="auto")
            
            # Add translated OCR text if available
            ocr_text = translated_block.get("image_text") or translated_block.get("translated_ocr")
            if ocr_text and ocr_text.strip():
                # Draw OCR text below image
                text_y = height - y1 - font_size - 5
                c.setFont(font_name, font_size - 1)  # Slightly smaller for OCR text
                
                # Simple word wrapping for OCR text
                words = ocr_text.split()
                current_line = ""
                max_width = w
                
                for word in words:
                    trial_line = (current_line + " " + word).strip()
                    if stringWidth(trial_line, font_name, font_size - 1) <= max_width:
                        current_line = trial_line
                    else:
                        if current_line:
                            c.drawString(x0, text_y, current_line)
                            text_y -= (font_size - 1) + 2
                        current_line = word
                
                if current_line:
                    c.drawString(x0, text_y, current_line)
            
            break
            
    except Exception as e:
        logger.error(f"Image block rendering failed: {e}")

# For backward compatibility - keep your existing function signatures
def translate_text_simple(text: str, source_lang: str, target_lang: str, engine: str = "google") -> str:
    """Simple text translation function for backward compatibility."""
    result = translation_manager.translate_text(text, source_lang, target_lang, engine)
    return result.translated_text if result.success else text
