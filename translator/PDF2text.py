
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
import statistics
from dataclasses import dataclass, asdict
from collections import defaultdict
import easyocr
import cv2
import numpy as np
# Use a global dictionary to cache multiple reader instances.
_OCR_READER_CACHE = {}

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
            page_data_json = f"page_{page_num}_data.json"
            try:
                with open(page_data_json, 'w', encoding='utf-8') as f:
                    # Use json.dump to write the dictionary to the file
                    # 'indent=4' makes the file human-readable (pretty-printed)
                    json.dump(page_data, f, indent=4)
            except Exception as e:
                # Handle potential errors during file writing
                print(f"Error writing page {page_num} data to file: {e}")
        return page_data
    
    # def _process_text_block(self, block: Dict[str, Any], page_num: int, block_idx: int,
    #                        source_lang: str, target_lang: str, engine: str) -> Dict[str, Any]:
    #     """Process text block with intelligent span batching."""
        
    #     # Group spans by style
    #     text_runs = self._create_text_runs(block)
        
    #     if self.debug_mode:
    #         self.debug_files['output'].write(f"\nBlock {block_idx}: Found {len(text_runs)} text runs\n")
            
    #         # Save text runs for debugging
    #         runs_debug = []
    #         for style_hash, run in text_runs.items():
    #             runs_debug.append({
    #                 "style": asdict(run.style),
    #                 "text": run.text,
    #                 "clean_text": run.clean_text(),
    #                 "span_count": len(run.spans)
    #             })
            
    #         json.dump(runs_debug, self.debug_files['runs'], indent=2, ensure_ascii=False)
    #         self.debug_files['runs'].write('\n')
        
    #     # Translate text runs
    #     translated_runs = self._translate_text_runs(
    #         text_runs, source_lang, target_lang, engine, page_num, block_idx
    #     )
        
    #     # Reconstruct block with translated spans
    #     translated_block = self._reconstruct_text_block(block, translated_runs)
        
    #     return translated_block
    

    def _process_text_block(self, block: Dict[str, Any], page_num: int, block_idx: int,
                       source_lang: str, target_lang: str, engine: str) -> Dict[str, Any]:
        """
        Processes a text block by first segmenting it into paragraphs, then creating
        style-based text runs within each paragraph.
        """
        # 1. Segment the block's spans into paragraphs
        paragraphs = self._segment_block_into_paragraphs(block)

        # 2. Process each paragraph to create text runs
        all_text_runs = []
        for paragraph_spans in paragraphs:
            runs = self._process_spans_into_runs(paragraph_spans)
            all_text_runs.extend(runs)

        if self.debug_mode:
            logger.debug(f"Block {block_idx}: Found {len(paragraphs)} paragraphs, creating {len(all_text_runs)} text runs.")

        # 3. Translate the collected text runs
        translated_runs = self._translate_text_runs(
            all_text_runs, source_lang, target_lang, engine, page_num, block_idx
        )

        # 4. Reconstruct the block with translated content
        translated_block = self._reconstruct_text_block(block, translated_runs)
        return translated_block

    def _segment_block_into_paragraphs(self, block: Dict[str, Any]) -> List[List[Dict[str, Any]]]:
        """
        Analyzes lines in a block and segments all its spans into a list of paragraphs.
        Each paragraph is a list of its constituent spans.
        """
        if not block.get("lines"):
            return []

        # First pass: Collect detailed line data for analysis
        line_data = []
        for line in block["lines"]:
            line_bbox = line["bbox"]
            line_text = " ".join(span.get("text", "") for span in line.get("spans", [])).strip()
            line_data.append({
                "bbox": line_bbox,
                "text": line_text,
                "y_pos": line_bbox[1],
                "x_start": line_bbox[0],
                "line_width": line_bbox[2] - line_bbox[0],
                "spans": line.get("spans", [])
            })

        # --- Calculate dynamic thresholds based on block's content ---
        line_gaps = [abs(line_data[i]["y_pos"] - line_data[i-1]["y_pos"])
                    for i in range(1, len(line_data))]
        base_line_height = statistics.median(g for g in line_gaps if g > 0) if any(g > 0 for g in line_gaps) else 15
        vertical_gap_threshold = base_line_height * 1.5 # A gap > 1.5x the median line height is a para break

        line_widths = [ld["line_width"] for ld in line_data if ld["line_width"] > 0]
        avg_line_width = statistics.mean(line_widths) if line_widths else 400
        short_line_threshold = avg_line_width * 0.7

        # --- Segment spans into paragraphs ---
        all_paragraphs = []
        current_paragraph_spans = []

        for i, line_info in enumerate(line_data):
            is_paragraph_break = False
            if i > 0:
                previous_line = line_data[i-1]
                vertical_gap = abs(line_info["y_pos"] - previous_line["y_pos"])

                # PRIMARY SIGNAL: Large vertical gap
                if vertical_gap > vertical_gap_threshold:
                    is_paragraph_break = True
                else:
                    # SECONDARY SIGNALS
                    previous_is_short = previous_line["line_width"] < short_line_threshold
                    is_indented = abs(line_info["x_start"] - previous_line["x_start"]) > 10

                    previous_ends_punct = previous_line["text"].rstrip().endswith(('.', '!', '?', ':'))
                    current_starts_capital = line_info["text"] and line_info["text"][0].isupper()

                    if previous_is_short and is_indented:
                        is_paragraph_break = True
                    elif (previous_ends_punct and current_starts_capital and
                        vertical_gap > base_line_height * 1.2):
                        is_paragraph_break = True

            if is_paragraph_break and current_paragraph_spans:
                all_paragraphs.append(current_paragraph_spans)
                current_paragraph_spans = []

            current_paragraph_spans.extend(line_info["spans"])

        # Add the last paragraph
        if current_paragraph_spans:
            all_paragraphs.append(current_paragraph_spans)

        try:
            with open('paragraphs_output.json', 'w', encoding='utf-8') as f:
                json.dump(all_paragraphs, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error writing paragraphs_output.json: {e}")
        return all_paragraphs

    def _process_spans_into_runs(self, paragraph_spans: List[Dict[str, Any]]) -> List[TextRun]:
        """
        Takes a list of spans for a single paragraph and groups them into
        style-consistent TextRun objects.
        """
        if not paragraph_spans:
            return []

        text_runs = []
        current_run = None

        for span in paragraph_spans:
            text = span.get("text", "").strip()
            if not text:
                continue

            style = self._create_span_style(span)

            # If no current run, or if the style is significantly different, create a new run
            if not current_run or self._should_force_new_run(style, current_run):
                if current_run:
                    text_runs.append(current_run)
                current_run = TextRun(style=style, text="", spans=[], line_breaks=[])

            current_run.add_span(span, text)

        if current_run:
            text_runs.append(current_run)

        return text_runs
    
    def _create_span_style(self, span: Dict[str, Any]) -> SpanStyle:
        """Helper to create a SpanStyle object from a span dictionary."""
        return SpanStyle(
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

    def _should_force_new_run(self, current_style: SpanStyle, existing_run: Optional[TextRun]) -> bool:
        """Check if we should force a new text run due to significant style changes."""
        if not existing_run:
            return True

        existing_style = existing_run.style

        # Force new run for major style differences
        is_font_family_change = current_style.font != existing_style.font
        is_major_size_change = abs(current_style.size - existing_style.size) > 2.0
        is_bold_change = (current_style.flags & 16) != (existing_style.flags & 16)
        is_italic_change = (current_style.flags & 2) != (existing_style.flags & 2)
        is_color_change = abs(current_style.color - existing_style.color) > 100000
        is_run_too_long = len(existing_run.text) > 2000 # Split long runs for better API context

        return any([is_font_family_change, is_major_size_change, is_bold_change,
                    is_italic_change, is_color_change, is_run_too_long])
    
    def _translate_text_runs(self, text_runs: List[TextRun],
                        source_lang: str, target_lang: str, engine: str,
                        page_num: int, block_idx: int) -> List[TextRun]:
        """Translates a list of TextRun objects."""
        
        translated_runs = []
        
        # Iterate directly over the list of runs
        for run in text_runs:
            try:
                clean_text = run.clean_text()
                
                if not clean_text or len(clean_text.strip()) < 2:
                    translated_runs.append(run) # Keep original if empty
                    continue

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
                    translated_runs.append(translated_run)
                else:
                    logger.warning(f"Translation failed on page {page_num}, block {block_idx}: {result.error_message}")
                    translated_runs.append(run)  # Keep original on failure
                    
            except Exception as e:
                logger.error(f"Error during translation of run: {e}")
                translated_runs.append(run) # Keep original on error
        
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
                           translated_runs: List[TextRun]) -> Dict[str, Any]:
        """Reconstructs the text block from a list of translated TextRun objects."""
        
        span_translations = {} # Map span ID to its new text and font

        for run in translated_runs:
            translated_text = run.text
            original_spans = run.spans
            
            if not original_spans or not translated_text:
                continue
            
            # Distribute the translated text back across the original spans proportionally
            words = translated_text.split()
            total_original_chars = sum(len(span.get("text", "")) for span in original_spans)
            
            word_cursor = 0
            for i, span in enumerate(original_spans):
                original_char_len = len(span.get("text", ""))
                
                # Allocate a number of words proportional to the span's original size
                share = original_char_len / total_original_chars if total_original_chars > 0 else 1/len(original_spans)
                num_words_for_span = round(len(words) * share)
                
                # Ensure the last span gets all remaining words
                if i == len(original_spans) - 1:
                    span_text = " ".join(words[word_cursor:])
                else:
                    span_text = " ".join(words[word_cursor : word_cursor + num_words_for_span])
                
                span_translations[id(span)] = {
                    "text": span_text,
                    "font": run.style.font
                }
                word_cursor += num_words_for_span
                
        # Rebuild the block structure line by line, span by span
        reconstructed_block = {
            "type": "text",
            "bbox": original_block["bbox"],
            "lines": []
        }
        
        for line in original_block.get("lines", []):
            new_line = line.copy()
            new_line["spans"] = []
            for span in line.get("spans", []):
                new_span = span.copy()
                if id(span) in span_translations:
                    translation_info = span_translations[id(span)]
                    new_span["text"] = translation_info["text"]
                    new_span["font"] = translation_info["font"]
                
                new_line["spans"].append(new_span)
            
            reconstructed_block["lines"].append(new_line)
        
        # Create the final combined text for the block
        all_text = " ".join(
            span.get("text", "") 
            for line in reconstructed_block["lines"] 
            for span in line["spans"] if span.get("text")
        )
        reconstructed_block["text"] = all_text.strip()
        
        return reconstructed_block
    
    def get_ocr_reader(source_lang: str):
        """
        Lazily initializes and caches easyocr.Reader instances for different
        language combinations, ensuring each is created only once.
        """
        # Create a consistent key for the language combination (e.g., ('en', 'hi')).
        # We sort to ensure ('en', 'hi') and ('hi', 'en') use the same reader.
        lang_list = sorted(list(set(['en', source_lang])))
        cache_key = tuple(lang_list)

        # If this specific reader isn't in our cache, create and store it.
        if cache_key not in _OCR_READER_CACHE:
            logging.info(f"Creating and caching new OCR reader for languages: {cache_key}...")
            # This is the slow part that now only runs once per language combo.
            _OCR_READER_CACHE[cache_key] = easyocr.Reader(lang_list, gpu=False) # Set gpu=True for GPU support
        
        # Return the cached reader.
        return _OCR_READER_CACHE[cache_key]
    
    
    def _process_image_block(self, page: fitz.Page, block: Dict[str, Any], page_num: int, block_idx: int, source_lang: str, target_lang: str, engine: str) -> Dict[str, Any]:
        """Process image block with easyocr, translate text, and prepare for in-painting."""
        
        try:
            # Step 1: Get the correct, cached OCR reader for the source language.
            ocr_reader = get_ocr_reader(source_lang)

            # Step 2: Extract image bytes from the PDF
            pix = page.get_pixmap(clip=block["bbox"])
            image_bytes = pix.tobytes("png")

            # Step 3: Perform OCR on the image bytes
            results = ocr_reader.readtext(image_bytes)

            ocr_details = []
            original_texts = []

            # Step 4: Process and translate each detected text fragment
            for (bbox, text, confidence) in results:
                original_texts.append(text)
                translated_text = text # Default to original text on failure

                if text.strip():
                    try:
                        res = translation_manager.translate_text(
                            text, source_lang, target_lang, engine
                        )
                        if res.success:
                            translated_text = res.translated_text
                    except Exception as e:
                        logger.warning(f"Translation for OCR text failed: {e}")

                ocr_details.append({
                    "bbox": [[int(p[0]), int(p[1])] for p in bbox],
                    "original_text": text,
                    "translated_text": translated_text,
                    "confidence": float(confidence)
                })

            # --- Your Future Goal: In-painting and Replacing Text (Debug Mode) ---
            if self.debug_mode and ocr_details:
                # Convert image bytes to an OpenCV image
                nparr = np.frombuffer(image_bytes, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

                for detail in ocr_details:
                    points = np.array(detail["bbox"], dtype=np.int32)
                    
                    # 1. Draw a green rectangle for the bounding box
                    # We use polylines to draw the exact shape, even if rotated.
                    cv2.polylines(img, [points], isClosed=True, color=(0, 255, 0), thickness=2)


                    # 2. Write the translated text on top of the white box
                    # Note: cv2.putText has poor support for non-ASCII chars.
                    # For production, a library like Pillow is better for text rendering.
                    top_left = tuple(points[0])
                    font = cv2.FONT_HERSHEY_SIMPLEX
                    cv2.putText(img, detail["translated_text"], top_left, font, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
                
                # Save the debug image
                debug_img_path = f"debug_page_{page_num}_block_{block_idx}.png"
                cv2.imwrite(debug_img_path, img)
                logger.info(f"Saved debug image to {debug_img_path}")

            # Step 5: Structure the final return value
            combined_original_text = "\n".join(original_texts)
            combined_translated_text = "\n".join([d["translated_text"] for d in ocr_details])

            return {
                "type": "image",
                "bbox": block["bbox"],
                "image_text": combined_translated_text or combined_original_text, #Not Required for me, will remove later
                "original_ocr": combined_original_text, #Not Required for me
                "translated_ocr": combined_translated_text, #Not Required for me
                "ocr_details": ocr_details # Store structured data for future use
            }

        except Exception as e:
            logger.error(f"Image processing failed entirely for block {block_idx} on page {page_num}: {e}")
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