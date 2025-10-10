# views.py - Updated for Enhanced PDF2text.py

from django.shortcuts import render, redirect
from django.http import HttpResponse
from deep_translator import GoogleTranslator
from .models import Translation
from .forms import TranslationForm
from .PDF2text import extract_text_for_validation, extract_blocks_from_pdf

from django.contrib import messages
from .utils import (
    chunk_text,
    translate_chunks,           # Google
    gemini_translate_text,      # Gemini
    gemini_translate_chunks,    # Gemini
    translate_blocks,           # Keep for backward compatibility
    rebuild_pdf
)

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT

import io
import os
import logging

logger = logging.getLogger(__name__)

from django.conf import settings

FONTS = settings.FONTS
FONT_PATH = settings.FONT_PATH

for lang, (font_name, font_file) in FONTS.items():
    try:
        pdfmetrics.registerFont(TTFont(font_name, os.path.join(FONT_PATH, font_file)))
    except:
        pass

def translate_view(request):
    translated_text = ''
    form = TranslationForm()

    if request.method == 'POST':
        form = TranslationForm(request.POST, request.FILES)

        if form.is_valid():
            source_text = form.cleaned_data.get('source_text')
            source_file = form.cleaned_data.get('source_file')
            source_lang_slug = form.cleaned_data['source_lang']
            target_lang_slug = form.cleaned_data['target_lang']
            engine = form.cleaned_data['engine']

            try:
                if source_file:  
                    # ✅ Enhanced PDF pipeline with smart span batching
                    logger.info(f"Processing PDF with enhanced extraction: {source_file.name}")
                    
                    # The new extract_blocks_from_pdf now handles translation internally
                    data = extract_blocks_from_pdf(
                        source_file,
                        source_lang=source_lang_slug,
                        target_lang=target_lang_slug,
                        engine=engine,
                        debug=True  # Enable debug for development
                    )
                    
                    # Check if translation was successful
                    metadata = data.get("metadata", {})
                    if metadata.get("errors"):
                        logger.warning(f"PDF processing had errors: {metadata['errors']}")
                        for error in metadata['errors'][:3]:  # Show first 3 errors
                            messages.warning(request, f"Processing warning: {error}")
                    
                    translated_pages = data.get("pages", [])
                    
                    if not translated_pages:
                        messages.error(request, "No content could be extracted from the PDF.")
                        return render(request, 'translator/translate.html', {'form': form})
                    
                    # Directly save the simplified output to the session. No loop needed.
                    request.session["translated_pages"] = translated_pages
                    
                    # Store translation metadata
                    request.session["translation_metadata"] = {
                        "source_lang": source_lang_slug,
                        "target_lang": target_lang_slug,
                        "engine": engine,
                        "total_pages": metadata.get("total_pages", len(safe_pages)),
                        "processing_errors": len(metadata.get("errors", []))
                    }

                    # Save original PDF to session
                    temp_path = os.path.join(settings.MEDIA_ROOT, f"uploaded_{request.session.session_key or 'temp'}.pdf")
                    os.makedirs(os.path.dirname(temp_path), exist_ok=True)
                    
                    with open(temp_path, "wb+") as dest:
                        for chunk in source_file.chunks():
                            dest.write(chunk)
                    request.session["original_pdf_path"] = temp_path

                    # Success message with details
                    page_count = metadata.get("total_pages", len(safe_pages))
                    error_count = len(metadata.get("errors", []))
                    
                    if error_count == 0:
                        messages.success(request, f"PDF translated successfully! {page_count} pages processed with enhanced formatting.")
                    else:
                        messages.info(request, f"PDF translated with {error_count} minor issues. {page_count} pages processed.")

                    return redirect("download_pdf")

                else:  
                    # ✅ Text pipeline (unchanged - works with existing utils)
                    if engine == "google":
                        if len(source_text) <= 4000:
                            translated_text = GoogleTranslator(
                                source=source_lang_slug, target=target_lang_slug
                            ).translate(source_text)
                        else:
                            chunks = chunk_text(source_text)
                            translated_text = translate_chunks(chunks, source_lang_slug, target_lang_slug)

                    elif engine == "gemini":
                        if len(source_text) <= 4000:
                            translated_text = gemini_translate_text(source_text, source_lang_slug, target_lang_slug)
                        else:
                            chunks = chunk_text(source_text)
                            translated_text = gemini_translate_chunks(chunks, source_lang_slug, target_lang_slug)

                    # Save to database
                    Translation.objects.create(
                        source_text=source_text[:5000],  # Limit for database
                        source_lang_slug=source_lang_slug,
                        target_lang_slug=target_lang_slug,
                        translated_text=translated_text[:5000] if translated_text else ""
                    )

                    request.session["translated_text_only"] = translated_text
                    request.session["translated_pages"] = []  # Clear old PDF session
                    request.session["original_pdf_path"] = None
                    request.session["translation_metadata"] = {
                        "source_lang": source_lang_slug,
                        "target_lang": target_lang_slug,
                        "engine": engine,
                        "text_length": len(source_text)
                    }

                    messages.success(request, "Text translated successfully!")

            except Exception as e:
                logger.error(f"Translation failed: {e}", exc_info=True)
                messages.error(request, f"Translation failed: {str(e)}")

    return render(request, 'translator/translate.html', {
        'form': form,
        'translated_text': translated_text
    })


def download_pdf(request):
    """
    Enhanced PDF download with support for new span-based structure.
    """
    try:
        translated_pages = request.session.get("translated_pages", [])
        original_pdf_path = request.session.get("original_pdf_path", None)
        translated_text_only = request.session.get("translated_text_only", None)
        translation_metadata = request.session.get("translation_metadata", {})

        # ✅ Case 1: Enhanced PDF Upload
        if translated_pages and original_pdf_path:
            logger.info("Rebuilding PDF with enhanced formatting")
            
            # Get target language for font selection
            target_lang = translation_metadata.get("target_lang", "default")
            
            try:
                output_pdf = rebuild_pdf(translated_pages, original_pdf_path, target_lang)
                
                # Cleanup temporary file
                try:
                    if os.path.exists(original_pdf_path):
                        os.remove(original_pdf_path)
                        request.session["original_pdf_path"] = None
                except Exception as cleanup_error:
                    logger.warning(f"Failed to cleanup temp file: {cleanup_error}")
                
                response = HttpResponse(output_pdf, content_type="application/pdf")
                response['Content-Disposition'] = 'attachment; filename="translated_document.pdf"'
                response['Content-Length'] = len(output_pdf)
                
                return response
                
            except Exception as rebuild_error:
                logger.error(f"PDF rebuild failed: {rebuild_error}")
                return HttpResponse(f"PDF generation failed: {str(rebuild_error)}", 
                                  content_type="text/plain", status=500)

        # ✅ Case 2: Text Input with target language font support
        elif translated_text_only:
            logger.info("Generating PDF from translated text")
            
            buffer = io.BytesIO()
            c = canvas.Canvas(buffer, pagesize=A4)
            width, height = A4

            # Get appropriate font for target language
            target_lang = translation_metadata.get("target_lang", "default")
            try:
                font_name, font_file = FONTS.get(target_lang, FONTS["default"])
            except:
                font_name, font_file = FONTS["default"]
            
            font_path = os.path.join(FONT_PATH, font_file)
            
            try:
                pdfmetrics.registerFont(TTFont(font_name, font_path))
                c.setFont(font_name, 12)
            except Exception as font_error:
                logger.warning(f"Font registration failed: {font_error}")
                # Fallback to default
                default_font, default_file = FONTS["default"]
                font_path = os.path.join(FONT_PATH, default_file)
                pdfmetrics.registerFont(TTFont(default_font, font_path))
                c.setFont(default_font, 12)
                font_name = default_font

            # Enhanced word wrapping with better spacing
            from reportlab.pdfbase.pdfmetrics import stringWidth
            max_width = width - 100  # left+right margins
            x, y = 50, height - 50
            line_height = 16  # Better line spacing
            
            for paragraph in translated_text_only.split("\n\n"):
                if not paragraph.strip():
                    continue
                    
                # Process each paragraph
                for line in paragraph.split("\n"):
                    words = line.split(" ")
                    current_line = ""
                    
                    for word in words:
                        trial = (current_line + " " + word).strip()
                        try:
                            line_width = stringWidth(trial, font_name, 12)
                        except:
                            line_width = len(trial) * 7  # Approximate fallback
                        
                        if line_width <= max_width:
                            current_line = trial
                        else:
                            if current_line:
                                c.drawString(x, y, current_line)
                                y -= line_height
                            current_line = word
                            
                            # Check for page break
                            if y < 50:
                                c.showPage()
                                c.setFont(font_name, 12)
                                y = height - 50
                    
                    # Draw remaining text in line
                    if current_line:
                        c.drawString(x, y, current_line)
                        y -= line_height
                        
                        if y < 50:
                            c.showPage()
                            c.setFont(font_name, 12)
                            y = height - 50
                
                # Add paragraph spacing
                y -= line_height // 2

            c.save()
            buffer.seek(0)
            
            response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
            response['Content-Disposition'] = 'attachment; filename="translated_text.pdf"'
            response['Content-Length'] = len(buffer.getvalue())
            
            return response

        else:
            return HttpResponse("No translated content available. Please translate a document first.", 
                              content_type="text/plain", status=400)

    except Exception as e:
        logger.error(f"Download failed: {e}", exc_info=True)
        return HttpResponse(f"Download failed: {str(e)}", content_type="text/plain", status=500)
