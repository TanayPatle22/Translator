#views.py

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
    translate_blocks,
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

# FONT_PATH = os.path.join(os.path.dirname(__file__), "fonts")

# FONTS = {
#     "default": ("NotoSans", "NotoSans.ttf"),   # universal fallback
#     "hi": ("NotoSansDevanagari", "NotoSansDevanagari.ttf"),
#     "bn": ("NotoSansBengali", "NotoSansBengali.ttf"),
#     "ta": ("NotoSansTamil", "NotoSansTamil.ttf"),
#     "te": ("NotoSansTelugu", "NotoSansTelugu.ttf"),
#     "gu": ("NotoSansGujarati", "NotoSansGujarati.ttf"),
#     "mr": ("NotoSansDevanagari", "NotoSansDevanagari.ttf"),
#     "pa": ("NotoSansGurmukhi", "NotoSansGurmukhi.ttf"),
#     "zh": ("NotoSansSC", "NotoSansSC.ttf"),   # Simplified Chinese
#     "ja": ("NotoSansJP", "NotoSansJP.ttf"),
#     "ko": ("NotoSansKR", "NotoSansKR.ttf"),
#     "ar": ("NotoNaskhArabic", "NotoNaskhArabic.ttf"),
#     "ru": ("NotoSans", "NotoSans.ttf"),
#     "fa": ("NotoNaskhArabic", "NotoNaskhArabic.ttf"),
#     "ur": ("NotoNaskhArabic", "NotoNaskhArabic.ttf"),
#     # Add more scripts if needed
# }

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
                    # ✅ PDF pipeline
                    data = extract_blocks_from_pdf(source_file)

                    translated_pages = []
                    for page in data["pages"]:
                        translated_blocks = translate_blocks(
                            page["blocks"], source_lang_slug, target_lang_slug, engine
                        )
                        translated_pages.append({"blocks": translated_blocks})

                    # Save safe blocks into session
                    safe_pages = []
                    for page in translated_pages:
                        safe_blocks = []
                        for block in page["blocks"]:
                            if block["type"] == "text":
                                # only keep serializable fields and normalize bbox to tuple
                                clean_block = {
                                    "type": "text",
                                    "bbox": tuple(block.get("bbox", ())),
                                    "text": block.get("text", "")
                                }
                                safe_blocks.append(clean_block)
                            else:
                                # keep only bbox & type for non-text blocks
                                safe_blocks.append({
                                    "type": block.get("type"),
                                    "bbox": tuple(block.get("bbox", ()))
                                })
                        safe_pages.append({"blocks": safe_blocks})

                    request.session["translated_pages"] = safe_pages

                    # Save original PDF to session
                    temp_path = os.path.join(settings.MEDIA_ROOT, "last_uploaded.pdf")
                    with open(temp_path, "wb+") as dest:
                        for chunk in source_file.chunks():
                            dest.write(chunk)
                    request.session["original_pdf_path"] = temp_path

                    return redirect("download_pdf")

                else:  
                    # ✅ Text pipeline
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

                    Translation.objects.create(
                        source_text=source_text,
                        source_lang_slug=source_lang_slug,
                        target_lang_slug=target_lang_slug,
                        translated_text=translated_text
                    )

                    request.session["translated_text_only"] = translated_text
                    request.session["translated_pages"] = []  # clear old PDF session
                    request.session["original_pdf_path"] = None

            except Exception as e:
                logger.error(f"Translation failed: {e}")
                messages.error(request, f"Translation failed: {str(e)}")

    return render(request, 'translator/translate.html', {
        'form': form,
        'translated_text': translated_text
    })

def download_pdf(request):
    """
    Download the last translated result as PDF:
    - If uploaded PDF → rebuild with formatting
    - If pasted text → create plain text PDF
    """
    try:
        translated_pages = request.session.get("translated_pages", [])
        original_pdf_path = request.session.get("original_pdf_path", None)
        translated_text_only = request.session.get("translated_text_only", None)

        # ✅ Case 1: PDF Upload
        if translated_pages and original_pdf_path:
            output_pdf = rebuild_pdf(translated_pages, original_pdf_path)
            response = HttpResponse(output_pdf, content_type="application/pdf")
            response['Content-Disposition'] = 'attachment; filename="translated.pdf"'
            return response

        # ✅ Case 2: Text Input
        elif translated_text_only:
            buffer = io.BytesIO()
            c = canvas.Canvas(buffer, pagesize=A4)
            width, height = A4

            # Load default font
            font_name, font_file = FONTS.get("default", FONTS["default"])
            font_path = os.path.join(FONT_PATH, font_file)
            pdfmetrics.registerFont(TTFont(font_name, font_path))
            c.setFont(font_name, 12)

            # Simple word wrapping
            from reportlab.pdfbase.pdfmetrics import stringWidth
            max_width = width - 100  # left+right margins
            x, y = 50, height - 50
            for line in translated_text_only.split("\n"):
                words = line.split(" ")
                current_line = ""
                for word in words:
                    trial = (current_line + " " + word).strip()
                    if stringWidth(trial, font_name, 12) <= max_width:
                        current_line = trial
                    else:
                        c.drawString(x, y, current_line)
                        y -= 15
                        current_line = word
                        if y < 50:  # new page
                            c.showPage()
                            c.setFont(font_name, 12)
                            y = height - 50
                if current_line:
                    c.drawString(x, y, current_line)
                    y -= 15
                    if y < 50:
                        c.showPage()
                        c.setFont(font_name, 12)
                        y = height - 50

            c.save()
            buffer.seek(0)
            response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
            response['Content-Disposition'] = 'attachment; filename="translated_text.pdf"'
            return response

        else:
            return HttpResponse("No translated result available.", content_type="text/plain")

    except Exception as e:
        return HttpResponse(f"Error: {str(e)}", content_type="text/plain")

