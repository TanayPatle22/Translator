# PDF2text.py - Enhanced with Smart Span Batching

import fitz  # PyMuPDF
from typing import Union, IO, Dict, List, Any, Tuple, Optional
import pytesseract
from PIL import Image
import io
import logging
import json
import base64
import re
from dataclasses import dataclass, asdict
from collections import defaultdict

# Import translation utilities
try:
    from .utils import translation_manager, TranslationResult
except ImportError:
    # Fallback for standalone testing
    from utils import translation_manager, TranslationResult

logger = logging.getLogger(__name__)

@dataclass
class SpanStyle:
    """Represents the styling parameters of a text span."""
    size: float
    flags: int
    font: str
    color: int
    alpha: int
    ascender: float
    descender: float
    bidi: int = 0
    char_flags: int = 0
    
    def __hash__(self):
        """Make SpanStyle hashable for grouping."""
        return hash((
            round(self.size, 2),  # Round to avoid floating point precision issues
            self.flags,
            self.font,
            self.color,
            self.alpha,
            round(self.ascender, 3),
            round(self.descender, 3),
            self.bidi,
            self.char_flags
        ))
    
    def __eq__(self, other):
        """Check if two styles are equivalent."""
        if not isinstance(other, SpanStyle):
            return False
        return (
            abs(self.size - other.size) < 0.1 and  # Allow small font size variations
            self.flags == other.flags and
            self.font == other.font and
            self.color == other.color and
            self.alpha == other.alpha and
            abs(self.ascender - other.ascender) < 0.01 and
            abs(self.descender - other.descender) < 0.01 and
            self.bidi == other.bidi and
            self.char_flags == other.char_flags
        )

@dataclass
class TextRun:
    """Represents a run of text with consistent styling."""
    style: SpanStyle
    text: str
    spans: List[Dict[str, Any]]  # Original span data for reconstruction
    line_breaks: List[int]  # Positions where line breaks occurred
    
    def add_span(self, span: Dict[str, Any], text: str, is_line_break: bool = False):
        """Add a span to this text run."""
        if is_line_break and self.text and not self.text.endswith(' '):
            self.text += ' '  # Add space for line breaks
            
        start_pos = len(self.text)
        self.text += text
        
        if is_line_break:
            self.line_breaks.append(start_pos)
            
        self.spans.append(span)
    
    def clean_text(self) -> str:
        """Clean text for translation by handling hyphenation and spacing."""
        text = self.text
        
        # Handle hyphenation across lines
        text = re.sub(r'-\s+', '', text)  # Remove hyphen + whitespace
        
        # Clean up multiple spaces
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text

class SmartPDFProcessor:
    """Enhanced PDF processor with intelligent span batching."""
    
    def __init__(self, debug_mode: bool = False):
        self.debug_mode = debug_mode
        self.debug_files = {}
        
        if debug_mode:
            self.debug_files = {
                'output': open("output.txt", "w", encoding="utf-8"),
                'blocks': open("blocks.json", "w", encoding="utf-8"),
                'runs': open("text_runs.json", "w", encoding="utf-8"),
                'results': open("results.json", "w", encoding="utf-8")
            }
    
    def __del__(self):
        """Close debug files."""
        for file in self.debug_files.values():
            if file and not file.closed:
                file.close()
    
    def extract_blocks_from_pdf(self, pdf_file: Union[str, IO], 
                               source_lang: str = "en", 
                               target_lang: str = "hi",
                               engine: str = "gemini") -> Dict[str, Any]:
        """
        Enhanced PDF extraction with intelligent span batching and translation.
        
        Args:
            pdf_file: PDF file path, bytes, or file-like object
            source_lang: Source language code
            target_lang: Target language code  
            engine: Translation engine ("google" or "gemini")
            
        Returns:
            Dictionary with translated pages and metadata
        """
        try:
            doc = self._open_pdf(pdf_file)
            if not doc:
                raise ValueError("Could not open PDF file")
            
            logger.info(f"Processing PDF: {doc.page_count} pages")
            
            results = {"pages": [], "metadata": {
                "total_pages": doc.page_count,
                "source_lang": source_lang,
                "target_lang": target_lang,
                "engine": engine,
                "errors": []
            }}
            
            for page_num, page in enumerate(doc, start=1):
                try:
                    page_data = self._process_page(page, page_num, source_lang, target_lang, engine)
                    results["pages"].append(page_data)
                    logger.debug(f"Processed page {page_num}/{doc.page_count}")
                    
                except Exception as e:
                    error_msg = f"Page {page_num} processing failed: {e}"
                    logger.error(error_msg)
                    results["metadata"]["errors"].append(error_msg)
                    
                    # Add empty page to maintain structure
                    results["pages"].append({
                        "number": page_num,
                        "blocks": [],
                        "error": str(e)
                    })
            
            # Save debug information
            if self.debug_mode:
                with open("final_results.json", "w", encoding="utf-8") as f:
                    json.dump(results, f, indent=2, ensure_ascii=False)
            
            doc.close()
            return results
            
        except Exception as e:
            logger.error(f"PDF processing failed: {e}", exc_info=True)
            return {
                "pages": [],
                "metadata": {
                    "total_pages": 0,
                    "errors": [str(e)]
                }
            }
    
    def _open_pdf(self, pdf_file: Union[str, IO]) -> Optional[fitz.Document]:
        """Open PDF with multiple strategies."""
        try:
            if isinstance(pdf_file, str):
                return fitz.open(pdf_file)
            elif isinstance(pdf_file, bytes):
                return fitz.open(stream=pdf_file, filetype="pdf")
            else:
                pdf_file.seek(0)
                return fitz.open(stream=pdf_file.read(), filetype="pdf")
        except Exception as e:
            logger.error(f"Failed to open PDF: {e}")
            return None
    
    def _process_page(self, page: fitz.Page, page_num: int,
                     source_lang: str, target_lang: str, engine: str) -> Dict[str, Any]:
        """Process a single page with smart span batching."""
        
        blocks = page.get_text("dict")["blocks"]
        page_data = {"number": page_num, "blocks": []}
        
        if self.debug_mode:
            self.debug_files['output'].write(f"\n=== PAGE {page_num} ===\n")
        
        for block_idx, block in enumerate(blocks):
            try:
                if "lines" in block:
                    # Process text block with smart batching
                    processed_block = self._process_text_block(
                        block, page_num, block_idx, source_lang, target_lang, engine
                    )
                    page_data["blocks"].append(processed_block)
                    
                elif "image" in block:
                    # Process image block with OCR
                    processed_block = self._process_image_block(
                        page, block, page_num, block_idx, source_lang, target_lang, engine
                    )
                    page_data["blocks"].append(processed_block)
                    
                else:
                    # Other block types (drawings, etc.)
                    page_data["blocks"].append({
                        "type": "other",
                        "bbox": block["bbox"]
                    })
                    
            except Exception as e:
                logger.warning(f"Block {block_idx} on page {page_num} failed: {e}")
                continue
        
        return page_data
    
    def _process_text_block(self, block: Dict[str, Any], page_num: int, block_idx: int,
                           source_lang: str, target_lang: str, engine: str) -> Dict[str, Any]:
        """Process text block with intelligent span batching."""
        
        # Group spans by style
        text_runs = self._create_text_runs(block)
        
        if self.debug_mode:
            self.debug_files['output'].write(f"\nBlock {block_idx}: Found {len(text_runs)} text runs\n")
            
            # Save text runs for debugging
            runs_debug = []
            for style_hash, run in text_runs.items():
                runs_debug.append({
                    "style": asdict(run.style),
                    "text": run.text,
                    "clean_text": run.clean_text(),
                    "span_count": len(run.spans)
                })
            
            json.dump(runs_debug, self.debug_files['runs'], indent=2, ensure_ascii=False)
            self.debug_files['runs'].write('\n')
        
        # Translate text runs
        translated_runs = self._translate_text_runs(
            text_runs, source_lang, target_lang, engine, page_num, block_idx
        )
        
        # Reconstruct block with translated spans
        translated_block = self._reconstruct_text_block(block, translated_runs)
        
        return translated_block
    
    def _create_text_runs(self, block: Dict[str, Any]) -> Dict[int, TextRun]:
        """Create text runs by grouping spans with identical styling."""
        
        text_runs = {}
        current_y = None
        line_height_threshold = 0  # Will be calculated dynamically
        
        for line in block["lines"]:
            line_bbox = line["bbox"]
            line_y = line_bbox[1]  # Top y-coordinate
            
            # Calculate line height for paragraph detection
            if current_y is not None:
                line_gap = abs(line_y - current_y)
                if line_height_threshold == 0:
                    line_height_threshold = line_gap * 1.5  # 1.5x normal line height
            current_y = line_y
            
            # Check for paragraph break (large vertical gap)
            is_paragraph_break = (current_y is not None and 
                                line_gap > line_height_threshold if 'line_gap' in locals() else False)
            
            for span in line["spans"]:
                try:
                    # Create style signature
                    style = SpanStyle(
                        size=span.get("size", 12),
                        flags=span.get("flags", 0),
                        font=span.get("font", "default"),
                        color=span.get("color", 0),
                        alpha=span.get("alpha", 255),
                        ascender=span.get("ascender", 0.8),
                        descender=span.get("descender", -0.2),
                        bidi=span.get("bidi", 0),
                        char_flags=span.get("char_flags", 0)
                    )
                    
                    style_hash = hash(style)
                    text = span.get("text", "").strip()
                    
                    if not text:
                        continue
                    
                    # Create or update text run
                    if style_hash not in text_runs:
                        text_runs[style_hash] = TextRun(
                            style=style,
                            text="",
                            spans=[],
                            line_breaks=[]
                        )
                    """
                    # We find spans with these styles:
                    span1: "Hello" (Arial, 12pt) → hash = 111
                    span2: " world" (Arial, 12pt) → hash = 111 (same style!)  
                    span3: "TITLE" (Arial, 18pt) → hash = 222 (different style)

                    # Step 1: Process span1
                    if 111 not in text_runs:  # True, first time seeing this style
                        text_runs[111] = TextRun(text="", spans=[], ...)  # Create new group
                        
                    text_runs[111].add_span(span1, "Hello")  # Add "Hello" to the group

                    # Step 2: Process span2  
                    if 111 not in text_runs:  # False, we already have this style
                        # Skip creating new group
                        
                    text_runs[111].add_span(span2, " world")  # Add " world" to existing group
                    # Now text_runs[111].text = "Hello world"

                    # Step 3: Process span3
                    if 222 not in text_runs:  # True, new style
                        text_runs[222] = TextRun(text="", spans=[], ...)  # Create new group
                        
                    text_runs[222].add_span(span3, "TITLE")  # Add "TITLE" to new group

                    # Final result:
                    text_runs = {
                        111: TextRun(text="Hello world", spans=[span1, span2]),  # Same style grouped
                        222: TextRun(text="TITLE", spans=[span3])                # Different style separate
                    }
                    """

                    # Add span to run (with paragraph break detection)
                    text_runs[style_hash].add_span(
                        span, text, is_line_break=is_paragraph_break
                    )
                    
                    if is_paragraph_break:
                        is_paragraph_break = False  # Reset flag
                        
                except Exception as e:
                    logger.warning(f"Failed to process span: {e}")
                    continue
        
        return text_runs
    
    def _translate_text_runs(self, text_runs: Dict[int, TextRun], 
                            source_lang: str, target_lang: str, engine: str,
                            page_num: int, block_idx: int) -> Dict[int, TextRun]:
        """Translate all text runs for a block."""
        
        translated_runs = {}
        
        for style_hash, run in text_runs.items():
            try:
                clean_text = run.clean_text()
                
                if not clean_text or len(clean_text.strip()) < 2:
                    translated_runs[style_hash] = run
                    continue
                
                if self.debug_mode:
                    self.debug_files['output'].write(f"Translating: '{clean_text[:100]}...'\n")
                
                # Translate using the translation manager
                result = translation_manager.translate_text(
                    clean_text, source_lang, target_lang, engine
                )
                
                if result.success:
                    # Create updated style with appropriate font for target language
                    updated_style = self._get_compatible_font_style(run.style, target_lang)
                    
                    # Create new run with translated text
                    translated_run = TextRun(
                        style=updated_style,
                        text=result.translated_text,
                        spans=run.spans,  # Keep original spans for bbox info
                        line_breaks=run.line_breaks
                    )
                    translated_runs[style_hash] = translated_run
                    
                    if self.debug_mode:
                        self.debug_files['output'].write(
                            f"Translation result: '{result.translated_text[:100]}...'\n"
                        )
                else:
                    logger.warning(f"Translation failed for page {page_num}, block {block_idx}: {result.error_message}")
                    translated_runs[style_hash] = run  # Keep original
                    
            except Exception as e:
                logger.error(f"Translation error: {e}")
                translated_runs[style_hash] = run  # Keep original on error
        
        return translated_runs
    
    def _get_compatible_font_style(self, original_style: SpanStyle, target_lang: str) -> SpanStyle:
        """Get font style compatible with target language."""
        
        from django.conf import settings

           # Get font info for target language
        try:
            font_name, font_file = settings.FONTS.get(target_lang, settings.FONTS["default"])
        except:
            font_name, font_file = settings.FONTS["default"]
        
        # If we need to change font, create new style
        if font_name != original_style.font:
            return SpanStyle(
                size=original_style.size,
                flags=original_style.flags,
                font=font_name,                    # Updated font
                color=original_style.color,
                alpha=original_style.alpha,
                ascender=original_style.ascender,
                descender=original_style.descender,
                bidi=original_style.bidi,
                char_flags=original_style.char_flags
            )
        
        return original_style  # No change needed
    
    def _reconstruct_text_block(self, original_block: Dict[str, Any], 
                               translated_runs: Dict[int, TextRun]) -> Dict[str, Any]:
        """Reconstruct the text block with translated content."""
        
        # Create a mapping from original spans to translated text
        span_translations = {}
        
        for style_hash, run in translated_runs.items():
            translated_text = run.text
            original_spans = run.spans
            
            if not original_spans:
                continue
            
            # Distribute translated text across original spans
            # Simple approach: put all text in first span, empty others
            total_chars = sum(len(span.get("text", "")) for span in original_spans)
            translated_chars = list(translated_text)
            cursor = 0

            for span in original_spans:
                span_len = len(span.get("text", ""))
                if total_chars > 0:
                    alloc = max(1, round(len(translated_chars) * (span_len / total_chars)))
                else:
                    alloc = len(translated_chars)

                chunk = "".join(translated_chars[cursor:cursor+alloc])
                span_translations[id(span)] = chunk
                cursor += alloc
                    
        # Reconstruct the block structure
        reconstructed_block = {
            "type": "text",
            "bbox": original_block["bbox"],
            "lines": []
        }
        
        # Process lines and spans
        for line in original_block["lines"]:
            new_line = {
                "spans": [],
                "wmode": line.get("wmode", 0),
                "dir": line.get("dir", [1.0, 0.0]),
                "bbox": line["bbox"]
            }
            
            for span in line["spans"]:
                span_id = id(span)
                new_span = span.copy()
                
                # Replace text with translation if available
                if span_id in span_translations:
                    new_span["text"] = span_translations[span_id]

                    for run in translated_runs.values():
                        if any(s is span for s in run.spans):
                            new_span["font"] = run.style.font  # Updated font
                            break
                
                new_line["spans"].append(new_span)
            
            reconstructed_block["lines"].append(new_line)
        
        # Extract combined text for compatibility
        all_text = " ".join([
            span["text"] for line in reconstructed_block["lines"] 
            for span in line["spans"] if span["text"]
        ])
        
        reconstructed_block["text"] = all_text.strip()
        
        return reconstructed_block
    
    def _process_image_block(self, page: fitz.Page, block: Dict[str, Any], 
                            page_num: int, block_idx: int,
                            source_lang: str, target_lang: str, engine: str) -> Dict[str, Any]:
        """Process image block with OCR and translation."""
        
        try:
            # Extract image
            pix = page.get_pixmap(clip=block["bbox"])
            
            # Ensure proper format for OCR
            if pix.alpha:
                pix = fitz.Pixmap(pix, 0)
            if pix.n != 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            
            # Perform OCR using PyMuPDF's built-in OCR
            # ocr_pdf_bytes = pix.pdfocr_tobytes(language="eng")
            # ocr_doc = fitz.open("pdf", ocr_pdf_bytes)
            # ocr_page = ocr_doc[0]
            # ocr_text = ocr_page.get_text("text").strip()
            
            ocr_doc.close()
            
            if self.debug_mode:
                self.debug_files['output'].write(f"\nImage Block {block_idx}: OCR extracted '{ocr_text[:100]}...'\n")
            
            # Translate OCR text if found
            # translated_text = None
            # if ocr_text and len(ocr_text.strip()) > 2:
            #     try:
            #         result = translation_manager.translate_text(
            #             ocr_text, source_lang, target_lang, engine
            #         )
                    
            #         if result.success:
            #             translated_text = result.translated_text
            #             if self.debug_mode:
            #                 self.debug_files['output'].write(f"OCR Translation: '{translated_text[:100]}...'\n")
            #         else:
            #             logger.warning(f"OCR translation failed: {result.error_message}")
                        
            #     except Exception as e:
            #         logger.error(f"OCR translation error: {e}")
            
            return {
                "type": "image",
                "bbox": block["bbox"],
                "image_text": translated_text or ocr_text,
                "original_ocr": ocr_text,
                "translated_ocr": translated_text
            }
            
        except Exception as e:
            logger.error(f"Image processing failed: {e}")
            return {
                "type": "image",
                "bbox": block["bbox"],
                "image_text": None,
                "error": str(e)
            }

# Main function for backward compatibility
def extract_blocks_from_pdf(pdf_file: Union[str, IO], 
                           source_lang: str = "en", 
                           target_lang: str = "hi",
                           engine: str = "gemini",
                           debug: bool = False) -> Dict[str, Any]:
    """
    Enhanced PDF extraction with smart span batching and translation.
    
    This function processes PDFs by:
    1. Grouping text spans by identical styling parameters
    2. Batching similar spans for better translation context
    3. Handling paragraph breaks and hyphenation
    4. Translating batched text runs
    5. Reconstructing the original structure with translations
    
    Args:
        pdf_file: PDF file (path, bytes, or file object)
        source_lang: Source language code (default: "en")  
        target_lang: Target language code (default: "hi")
        engine: Translation engine ("google" or "gemini")
        debug: Enable debug output files
        
    Returns:
        Dictionary with translated PDF structure
    """
    
    processor = SmartPDFProcessor(debug_mode=debug)
    return processor.extract_blocks_from_pdf(pdf_file, source_lang, target_lang, engine)

def extract_text_for_validation(pdf_file: Union[str, IO]) -> str:
    """
    Extract plain text for validation (quick preview).
    """
    try:
        processor = SmartPDFProcessor(debug_mode=False)
        data = processor.extract_blocks_from_pdf(pdf_file, "en", "en", "google")  # No translation
        
        text_content = []
        for page in data.get("pages", []):
            page_texts = []
            
            for block in page.get("blocks", []):
                if block.get("type") == "text" and block.get("text"):
                    page_texts.append(block["text"])
                elif block.get("type") == "image" and block.get("image_text"):
                    page_texts.append(f"[Image: {block['image_text']}]")
            
            if page_texts:
                page_text = "\n".join(page_texts)
                text_content.append(f"--- Page {page['number']} ---\n{page_text}")
        
        return "\n\n".join(text_content)
        
    except Exception as e:
        logger.error(f"Text validation failed: {e}")
        return ""