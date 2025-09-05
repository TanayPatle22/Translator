

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
        logger.info("Received POST request")
        form = TranslationForm(request.POST, request.FILES)
        if form.is_valid():
            logger.info("Form is valid")
            source_text = form.cleaned_data['source_text']
            source_lang_slug = form.cleaned_data['source_lang']
            target_lang_slug = form.cleaned_data['target_lang']
            engine = form.cleaned_data['engine']

            logger.info(f"Engine selected: {engine}")
            logger.info(f"Source lang: {source_lang_slug}, Target lang: {target_lang_slug}")
            logger.info(f"Source text length: {len(source_text)} characters")

            try:
                if engine == "google":
                    
                    if len(source_text) <= 4000:
                        translated_text = GoogleTranslator(
                            source=source_lang_slug,
                            target=target_lang_slug
                        ).translate(source_text)
                        
                    else:
                        chunks = chunk_text(source_text)
                        translated_text = translate_chunks(
                            chunks, source_lang_slug, target_lang_slug
                        )
                        

                elif engine == "gemini":
                    logger.info("Using Gemini Translator...")
                    if len(source_text) <= 4000:
                        logger.info("Sending single request to Gemini")
                        translated_text = gemini_translate_text(
                            source_text, source_lang_slug, target_lang_slug
                        )
                        logger.info("Gemini single-text translation complete")

                    else:
                        logger.info("Chunking text for Gemini translation...")
                        chunks = chunk_text(source_text)
                        translated_text = gemini_translate_chunks(
                            chunks, source_lang_slug, target_lang_slug
                        )
                        logger.info("Gemini chunk translation complete")



                # new_translation = Translation(
                #     source_text = source_text,
                #     source_lang_slug = source_lang_slug,
                #     target_lang_slug = target_lang_slug,
                #     translated_text = translated_text
                # )
                # new_translation.save()

                Translation.objects.create(
                        source_text=source_text,
                        source_lang_slug=source_lang_slug,
                        target_lang_slug=target_lang_slug,
                        translated_text=translated_text
                    )

            except Exception as e:
                # Handle the exception (e.g., log it, display an error message, etc.)
                print(f"Error during translation: {e}")

    return render(request, 'translator/translate.html', {
    'form': form,
    'translated_text': translated_text
    })


def translate_pdf_view(request):

    logger.info("📥 Entered translate_pdf_view")

    if request.method == "POST":
        form = TranslationForm(request.POST, request.FILES)
        if form.is_valid():
            pdf_file = request.FILES["source_file"]
            source_lang = form.cleaned_data['source_lang']
            target_lang = form.cleaned_data['target_lang']
            engine = form.cleaned_data['engine']
            # blocks = form.cleaned_data.get('blocks')

            try:

                data = extract_blocks_from_pdf(pdf_file)

                translated_pages = []
                for page in data["pages"]:
                    translated_blocks = translate_blocks(
                        page["blocks"], source_lang, target_lang, engine
                    )
                    translated_pages.append({"blocks": translated_blocks})

                # normalized_blocks = []
                # for b in translated_blocks:
                #     if isinstance(b, dict):
                #         normalized_blocks.append(b)
                #     elif isinstance(b, str):
                #         normalized_blocks.append({"text": b, "type": "paragraph"})
                #     else:
                        # normalized_blocks.append({"text": str(b), "type": "paragraph"})

                # Save into session so download_pdf can use it
                request.session["translated_pages"] = translated_pages

                # response = HttpResponse(output_pdf, content_type="application/pdf")
                # response['Content-Disposition'] = 'attachment; filename="translated.pdf"'
                # return response
            
                return redirect("download_pdf")
            
            except Exception as e:
                logger.error(f"PDF translation failed: {e}")
                messages.error(request, f"PDF translation failed: {str(e)}")

        else:
            form = TranslationForm()

    return render(request, "translator/translate.html", {"form": form})




def download_pdf(request):
    """
    Download the last translated PDF with formatting (blocks).
    """

    try:
        # Retrieve blocks from session (saved after translation)
        translated_pages = request.session.get("translated_pages", [])

        if not translated_pages:
            return HttpResponse("No translated PDF available.", content_type="text/plain")

        # Rebuild PDF with formatting
        output_pdf = rebuild_pdf(translated_pages)

        response = HttpResponse(output_pdf, content_type="application/pdf")
        response['Content-Disposition'] = 'attachment; filename="translated.pdf"'
        return response

    except Exception as e:
        return HttpResponse(f"Error: {str(e)}", content_type="text/plain")

# def download_pdf(request):
#     """Generates a PDF with the last translated text"""
#     translated_text = request.GET.get("translated_text", "")
#     target_lang = request.GET.get("target_lang", "default")

#     # If nothing to download, return error response
#     if not translated_text:
#         return HttpResponse("No translated text to download.", content_type="text/plain")
    
#     # Pick font for target language (fallback to default)
#     font_name = FONTS.get(target_lang, FONTS["default"])[0]
#     font_path = os.path.join(FONT_PATH, font_file)

#     try:
#         pdfmetrics.registerFont(TTFont(font_name, font_path))
#     except:
#         font_name = "Helvetica"  # fallback if font missing

#     # Create a BytesIO buffer to hold PDF data
#     buffer = io.BytesIO()
#     doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=50, leftMargin=50, topMargin=50, bottomMargin=50)
#     width, height = A4

#     # Define paragraph style
#     styles = getSampleStyleSheet()
#     custom_style = ParagraphStyle(
#         name="CustomStyle",
#         parent=styles["Normal"],
#         fontName=font_name,
#         fontSize=12,
#         leading=16,
#         alignment=TA_JUSTIFY,   # nice block-style text
#     )

#     story = []

#     story.append(Paragraph("<b>Translated Text</b>", styles["Title"]))
#     story.append(Spacer(1, 20))

#     # Convert translated_text (preserve line breaks)
#     for para in translated_text.split("\n"):
#         if para.strip():
#             story.append(Paragraph(para, custom_style))
#             story.append(Spacer(1, 10))

#     # Build PDF
#     doc.build(story)

#     buffer.seek(0)
#     return HttpResponse(buffer, content_type="application/pdf")

    # # Write title
    # p.setFont("Helvetica", 12)
    # p.drawString(72, height - 72, "Translated Text")

    # # Write actual translated text (wrap lines)
    # p.setFont(font_name, 12)
    # y = height - 100
    # for line in translated_text.split("\n"):
    #     p.drawString(72, y, line)
    #     y -= 20
    #     if y < 72:  # New page if content overflows
    #         p.showPage()
    #         y = height - 72

    # p.showPage()
    # p.save()

    # buffer.seek(0)
    # return HttpResponse(buffer, content_type="application/pdf")
