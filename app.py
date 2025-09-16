import os
import sys
import io
import json
import tempfile
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parent
sys.path.append(str(ROOT))

# Must set this BEFORE importing django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

import django
django.setup()

from deep_translator import GoogleTranslator

from translator.utils import (
    LANGUAGE_SLUGS,
    chunk_text,
    translate_chunks,
    gemini_translate_text,
    gemini_translate_chunks,
    translate_blocks,
    rebuild_pdf,
)

from translator.PDF2text import (
    extract_text_for_validation,
    extract_blocks_from_pdf
)

from translator.models import Translation 

import logging

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# --- Helpers ---
def save_bytes_to_temp_pdf(b: bytes):
    """Write bytes to a temporary pdf file and return its path."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.write(b)
    tmp.flush()
    tmp.close()
    return tmp.name


def generate_pdf_from_text(text: str):
    """Create a simple PDF from plain text and return bytes (based on your views.download_pdf code)."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from django.conf import settings

    FONTS = getattr(settings, "FONTS", {})
    FONT_PATH = getattr(settings, "FONT_PATH", "")

    font_name, font_file = FONTS.get("default", ("NotoSans", "NotoSans.ttf"))
    font_path = os.path.join(FONT_PATH, font_file) if FONT_PATH else None
    try:
        if font_path and os.path.exists(font_path):
            pdfmetrics.registerFont(TTFont(font_name, font_path))
    except Exception:
        pass

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    c.setFont(font_name, 12)

    # Simple word-wrapping similar to your view
    max_width = width - 100
    x, y = 50, height - 50
    for line in text.split("\n"):
        words = line.split(" ")
        current_line = ""
        for word in words:
            trial = (current_line + " " + word).strip()
            from reportlab.pdfbase.pdfmetrics import stringWidth
            if stringWidth((trial), font_name, 12) <= max_width:
                current_line = trial
            else:
                c.drawString(x, y, current_line)
                y -= 15
                current_line = word
                if y < 50:
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
    return buffer.getvalue()
# --- End helpers ---

# --- Streamlit UI ---
st.set_page_config(page_title="Translator", layout="wide")
st.title("Translator")

col1, col2 = st.columns([1, 1.0])

with col1:
    st.subheader("Input")
    source_text = st.text_area("Enter source text (leave empty if uploading PDF)", height=200, placeholder="Type or paste text here...")
    uploaded_file = st.file_uploader("Upload PDF (optional)", type=["pdf"])
    # language selects: show friendly labels but keep key as slug.
    langs = list(LANGUAGE_SLUGS.keys())
    def fmt(k): return LANGUAGE_SLUGS.get(k, k)
    source_lang = st.selectbox("From", langs, index=langs.index("en") if "en" in langs else 0, format_func=fmt)
    target_lang = st.selectbox("To", langs, index=langs.index("hi") if "hi" in langs else 0, format_func=fmt)
    engine = st.radio("Translation Engine", ("google", "gemini"), index=1, format_func=lambda v: "Google Translator" if v=="google" else "Gemini (LLM)")

    if st.button("Translate"):
        # Validation
        if not source_text and not uploaded_file:
            st.error("Please provide text OR upload a PDF.")
        else:
            status = st.empty()
            try:
                if uploaded_file:
                    status.info("Reading PDF…")
                    pdf_bytes = uploaded_file.read()
                    # try to pass a file-like object to extract_blocks_from_pdf
                    buffer = io.BytesIO(pdf_bytes)
                    buffer.seek(0)
                    status.info("Extracting text blocks from PDF…")
                    data = extract_blocks_from_pdf(buffer)
                    status.info("Translating PDF blocks…")
                    translated_pages = []
                    for page in data.get("pages", []):
                        translated_blocks = translate_blocks(page.get("blocks", []), source_lang, target_lang, engine)
                        translated_pages.append({"blocks": translated_blocks})

                    # store in session state
                    st.session_state["translated_pages"] = translated_pages
                    st.session_state["original_pdf_bytes"] = pdf_bytes

                    status.info("Rebuilding PDF… (this may take a moment)")
                    # rebuild_pdf expects a path to original PDF -> write temp file
                    tmp_path = save_bytes_to_temp_pdf(pdf_bytes)
                    pdf_out = rebuild_pdf(translated_pages, tmp_path)
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

                    # save generated PDF bytes to session for download
                    st.session_state["translated_pdf_bytes"] = pdf_out
                    st.success("PDF translated and ready for download.")
                else:
                    # Text pipeline
                    status.info("Translating text…")
                    if engine == "google":
                        if len(source_text) <= 4000:
                            translated_text = GoogleTranslator(source=source_lang, target=target_lang).translate(source_text)
                        else:
                            chunks = chunk_text(source_text)
                            translated_text = translate_chunks(chunks, source_lang, target_lang)
                    elif engine == "gemini":
                        logger.debug(f"Using Gemini for translation | Source lang={source_lang}, Target lang={target_lang}")
                        
                        try:
                            with st.spinner("Translating with Gemini…"):
                                if len(source_text) <= 4000:
                                    logger.debug(f"Sending full text ({len(source_text)} chars) to gemini_translate_text")
                                    translated_text = gemini_translate_text(source_text, source_lang, target_lang)
                                    logger.debug("Gemini translation returned successfully")
                                else:
                                    logger.debug(f"Text too long ({len(source_text)} chars), chunking...")
                                    chunks = chunk_text(source_text)
                                    translated_text = gemini_translate_chunks(chunks, source_lang, target_lang)
                                    logger.debug("Gemini chunked translation returned successfully")
                        except Exception as e:
                            logger.error(f"Gemini translation failed: {e}", exc_info=True)
                            st.error("Gemini translation failed. Check logs.")
                            translated_text = None

                    # Optionally save to DB:
                    try:
                        Translation.objects.create(
                            source_text=source_text,
                            source_lang_slug=source_lang,
                            target_lang_slug=target_lang,
                            translated_text=translated_text
                        )
                    except Exception:
                        # ignore DB save errors — but you can surface them as needed
                        pass

                    st.session_state["translated_text"] = translated_text
                    st.success("Text translated.")
                status.empty()
            except Exception as e:
                status.error(f"Translation failed: {e}")

with col2:
    st.subheader("Result / Download")
    # If text translation result exists
    if st.session_state.get("translated_text"):
        t = st.session_state["translated_text"]
        st.text_area("Translated Text", t, height=360)

        # Copy-to-clipboard button via small html/js component
        try:
            escaped = json.dumps(t)  # safe JS string literal
            copy_button_html = f"""
            <button onclick='navigator.clipboard.writeText({escaped}).then(()=>{{alert("Copied to clipboard")}}).catch(()=>{{alert("Copy failed")}})'>Copy to clipboard</button>
            """
            components.html(copy_button_html, height=45)
        except Exception:
            st.write("Copy button unavailable on this browser.")

        pdf_bytes = generate_pdf_from_text(t)
        st.download_button("Download translated text as PDF", data=pdf_bytes, file_name="translated_text.pdf", mime="application/pdf")

    # If PDF pipeline result exists
    if st.session_state.get("translated_pdf_bytes"):
        st.write("Translated PDF (from uploaded source):")
        st.download_button("Download translated PDF", data=st.session_state["translated_pdf_bytes"], file_name="translated.pdf", mime="application/pdf")