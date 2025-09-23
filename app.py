# app.py - Fixed for Simple Models

import os
import sys
import io
import json
import tempfile
import time
from pathlib import Path
from datetime import datetime, timedelta

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
    translate_blocks,  # Keep for backward compatibility
    rebuild_pdf,
)

from translator.PDF2text import (
    extract_text_for_validation,
    extract_blocks_from_pdf  # Now with enhanced smart batching
)

from translator.models import Translation

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# --- Enhanced Helpers ---
def save_bytes_to_temp_pdf(b: bytes) -> str:
    """Write bytes to a temporary pdf file and return its path."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.write(b)
    tmp.flush()
    tmp.close()
    return tmp.name

def generate_pdf_from_text(text: str, target_lang: str = "default") -> bytes:
    """Create a PDF from plain text with proper font support for target language."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from django.conf import settings

    FONTS = getattr(settings, "FONTS", {})
    FONT_PATH = getattr(settings, "FONT_PATH", "")

    # Get appropriate font for target language
    try:
        font_name, font_file = FONTS.get(target_lang, FONTS["default"])
    except:
        font_name, font_file = FONTS["default"]
    
    font_path = os.path.join(FONT_PATH, font_file) if FONT_PATH else None
    
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    # Register and set font
    try:
        if font_path and os.path.exists(font_path):
            pdfmetrics.registerFont(TTFont(font_name, font_path))
            c.setFont(font_name, 12)
        else:
            c.setFont("Helvetica", 12)  # Fallback
    except Exception as e:
        logger.warning(f"Font setup failed: {e}")
        c.setFont("Helvetica", 12)

    # Enhanced word wrapping with better spacing
    max_width = width - 100
    x, y = 50, height - 50
    line_height = 16
    
    for paragraph in text.split("\n\n"):
        if not paragraph.strip():
            continue
            
        for line in paragraph.split("\n"):
            words = line.split(" ")
            current_line = ""
            for word in words:
                trial = (current_line + " " + word).strip()
                try:
                    from reportlab.pdfbase.pdfmetrics import stringWidth
                    if stringWidth(trial, font_name, 12) <= max_width:
                        current_line = trial
                    else:
                        if current_line:
                            c.drawString(x, y, current_line)
                            y -= line_height
                        current_line = word
                        if y < 50:
                            c.showPage()
                            c.setFont(font_name, 12)
                            y = height - 50
                except:
                    # Fallback without stringWidth
                    if len(trial) * 7 <= max_width:
                        current_line = trial
                    else:
                        if current_line:
                            c.drawString(x, y, current_line)
                            y -= line_height
                        current_line = word
                        if y < 50:
                            c.showPage()
                            c.setFont(font_name, 12)
                            y = height - 50
            
            if current_line:
                c.drawString(x, y, current_line)
                y -= line_height
                if y < 50:
                    c.showPage()
                    c.setFont(font_name, 12)
                    y = height - 50
        
        # Paragraph spacing
        y -= line_height // 2

    c.save()
    buffer.seek(0)
    return buffer.getvalue()

# --- Enhanced Streamlit UI ---
st.set_page_config(
    page_title="Advanced Translator", 
    layout="wide",
    page_icon="🌐"
)

# Header with styling
st.markdown("""
<style>
.main-header {
    background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
    padding: 1rem;
    border-radius: 10px;
    color: white;
    text-align: center;
    margin-bottom: 2rem;
}
.success-box {
    background: #d4edda;
    border: 1px solid #c3e6cb;
    color: #155724;
    padding: 0.75rem;
    border-radius: 0.25rem;
    margin: 1rem 0;
}
.info-box {
    background: #cce7ff;
    border: 1px solid #99d6ff;
    color: #004085;
    padding: 0.75rem;
    border-radius: 0.25rem;
    margin: 1rem 0;
}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="main-header">
    <h1>🌐 Advanced Translator</h1>
    <p>Enhanced PDF translation with smart span batching and OCR</p>
</div>
""", unsafe_allow_html=True)

# Sidebar with statistics and options
with st.sidebar:
    st.header("📊 Stats")
    try:
        total_translations = Translation.objects.count()
        # Simplified stats - only count basic fields
        st.metric("Total Translations", total_translations)
        st.metric("Languages", len(LANGUAGE_SLUGS))
        st.metric("Status", "Ready")
    except Exception as e:
        st.metric("Status", "Database Error")
        logger.warning(f"Stats error: {e}")
    
    st.markdown("---")
    st.header("⚙️ Options")
    
    debug_mode = st.checkbox("Debug Mode", help="Enable detailed processing logs")
    preserve_formatting = st.checkbox("Preserve Formatting", value=True, help="Maintain document structure")
    show_metadata = st.checkbox("Show Processing Details", help="Display extraction and translation metadata")

# Main content
col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("📝 Input")
    
    # Input method
    input_method = st.radio(
        "Choose input method:",
        ["Text Input", "PDF Upload"],
        help="Select how you want to provide content for translation"
    )
    
    source_text = ""
    uploaded_file = None
    
    if input_method == "Text Input":
        source_text = st.text_area(
            "Enter text to translate:",
            height=250,
            placeholder="Type or paste your text here...",
            help="Enter up to 50,000 characters"
        )
        
        if source_text:
            char_count = len(source_text)
            if char_count > 50000:
                st.error("Text exceeds 50,000 character limit")
            else:
                st.caption(f"Characters: {char_count:,}")
    
    else:
        uploaded_file = st.file_uploader(
            "Upload PDF file:",
            type=["pdf"],
            help="Upload a PDF document (max 10 MB) for translation"
        )
        
        if uploaded_file:
            file_size = uploaded_file.size / (1024 * 1024)
            if file_size > 10:
                st.error("File size exceeds 10 MB limit")
            else:
                st.success(f"File loaded: {uploaded_file.name} ({file_size:.1f} MB)")

    # Language selection
    st.markdown("### 🌍 Languages")
    
    languages = list(LANGUAGE_SLUGS.items())
    lang_dict = dict(languages)
    
    col_from, col_to = st.columns(2)
    
    with col_from:
        source_lang = st.selectbox(
            "From:",
            options=[code for code, _ in languages],
            index=0,  # Default to English
            format_func=lambda x: lang_dict[x]
        )
    
    with col_to:
        target_lang = st.selectbox(
            "To:",
            options=[code for code, _ in languages],
            index=3 if len(languages) > 3 else 1,  # Default to Hindi
            format_func=lambda x: lang_dict[x]
        )

    # Engine selection
    engine = st.selectbox(
        "Translation Engine:",
        ["gemini", "google"],
        index=0,
        format_func=lambda x: "Gemini AI (Recommended)" if x == "gemini" else "Google Translate"
    )

    # Translate button
    if st.button("🚀 Translate", type="primary", use_container_width=True):
        # Validation
        if not source_text and not uploaded_file:
            st.error("Please provide text or upload a PDF file.")
        elif source_lang == target_lang:
            st.error("Source and target languages must be different.")
        else:
            # Processing
            progress_container = st.container()
            start_time = time.time()
            
            with progress_container:
                progress_bar = st.progress(0)
                status_text = st.empty()
            
            try:
                if uploaded_file:
                    # Enhanced PDF processing
                    status_text.info("🔍 Analyzing PDF structure...")
                    progress_bar.progress(10)
                    
                    pdf_bytes = uploaded_file.read()
                    buffer = io.BytesIO(pdf_bytes)
                    
                    status_text.info("🧠 Processing with smart span batching...")
                    progress_bar.progress(30)
                    
                    # Use the enhanced PDF processing with internal translation
                    data = extract_blocks_from_pdf(
                        buffer,
                        source_lang=source_lang,
                        target_lang=target_lang,
                        engine=engine,
                        debug=debug_mode
                    )
                    
                    progress_bar.progress(70)
                    
                    # Check results
                    metadata = data.get("metadata", {})
                    pages = data.get("pages", [])
                    
                    if not pages:
                        st.error("No content could be extracted from the PDF.")
                    else:
                        status_text.info("📄 Rebuilding PDF with translations...")
                        progress_bar.progress(85)
                        
                        # Store results
                        st.session_state["translated_pages"] = pages
                        st.session_state["original_pdf_bytes"] = pdf_bytes
                        st.session_state["translation_metadata"] = metadata
                        
                        # Rebuild PDF
                        tmp_path = save_bytes_to_temp_pdf(pdf_bytes)
                        try:
                            pdf_out = rebuild_pdf(pages, tmp_path, target_lang)
                            st.session_state["translated_pdf_bytes"] = pdf_out
                        finally:
                            try:
                                os.remove(tmp_path)
                            except:
                                pass
                        
                        progress_bar.progress(100)
                        processing_time = time.time() - start_time
                        
                        # Clear progress
                        progress_container.empty()
                        
                        # Success message
                        st.markdown(f"""
                        <div class="success-box">
                            ✅ <strong>PDF translated successfully!</strong><br>
                            📄 Pages processed: {metadata.get('total_pages', len(pages))}<br>
                            ⏱️ Processing time: {processing_time:.1f}s<br>
                            🔧 Engine: {metadata.get('engine', engine).title()}
                        </div>
                        """, unsafe_allow_html=True)
                
                else:
                    # Text processing
                    status_text.info("🌐 Translating text...")
                    progress_bar.progress(30)
                    
                    if engine == "google":
                        if len(source_text) <= 4000:
                            translated_text = GoogleTranslator(
                                source=source_lang, target=target_lang
                            ).translate(source_text)
                        else:
                            chunks = chunk_text(source_text)
                            translated_text = translate_chunks(chunks, source_lang, target_lang)
                    
                    elif engine == "gemini":
                        with st.spinner("Processing with Gemini AI..."):
                            if len(source_text) <= 4000:
                                translated_text = gemini_translate_text(
                                    source_text, source_lang, target_lang
                                )
                            else:
                                chunks = chunk_text(source_text)
                                translated_text = gemini_translate_chunks(
                                    chunks, source_lang, target_lang
                                )
                    
                    progress_bar.progress(80)
                    
                    if translated_text:
                        # Save to database - simplified for basic model
                        try:
                            Translation.objects.create(
                                source_text=source_text[:5000],  # Limit for storage
                                source_lang_slug=source_lang,
                                target_lang_slug=target_lang,
                                translated_text=translated_text[:5000] if translated_text else ""
                            )
                        except Exception as db_error:
                            logger.warning(f"Database save failed: {db_error}")
                        
                        st.session_state["translated_text"] = translated_text
                        st.session_state["translation_metadata"] = {
                            "source_lang": source_lang,
                            "target_lang": target_lang,
                            "engine": engine,
                            "character_count": len(source_text),
                            "processing_time": time.time() - start_time
                        }
                        
                        progress_bar.progress(100)
                        processing_time = time.time() - start_time
                        
                        # Clear progress
                        progress_container.empty()
                        
                        # Success message
                        st.markdown(f"""
                        <div class="success-box">
                            ✅ <strong>Text translated successfully!</strong><br>
                            📝 Characters: {len(source_text):,}<br>
                            ⏱️ Processing time: {processing_time:.1f}s<br>
                            🔧 Engine: {engine.title()}
                        </div>
                        """, unsafe_allow_html=True)
                    
                    else:
                        st.error("Translation failed. Please try again.")
            
            except Exception as e:
                progress_container.empty()
                st.error(f"Translation failed: {str(e)}")
                logger.error(f"Translation error: {e}", exc_info=True)

with col2:
    st.subheader("📄 Results")
    
    # Text results
    if st.session_state.get("translated_text"):
        translated_text = st.session_state["translated_text"]
        metadata = st.session_state.get("translation_metadata", {})
        
        st.text_area(
            "Translated Text:",
            translated_text,
            height=250,
            help="Your translated text result"
        )
        
        # Statistics
        col_stats1, col_stats2, col_stats3 = st.columns(3)
        with col_stats1:
            st.metric("Characters", len(translated_text))
        with col_stats2:
            st.metric("Words", len(translated_text.split()))
        with col_stats3:
            st.metric("Time", f"{metadata.get('processing_time', 0):.1f}s")
        
        # Copy button
        try:
            escaped_text = json.dumps(translated_text)
            copy_html = f"""
            <button onclick='navigator.clipboard.writeText({escaped_text}).then(()=>alert("✅ Copied!")).catch(()=>alert("❌ Copy failed"))' 
                    style='background:#667eea;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;'>
                📋 Copy to Clipboard
            </button>
            """
            components.html(copy_html, height=50)
        except:
            pass
        
        # Download as PDF
        target_lang_code = metadata.get("target_lang", "default")
        pdf_bytes = generate_pdf_from_text(translated_text, target_lang_code)
        st.download_button(
            "📄 Download as PDF",
            data=pdf_bytes,
            file_name="translated_text.pdf",
            mime="application/pdf",
            use_container_width=True
        )
    
    # PDF results
    if st.session_state.get("translated_pdf_bytes"):
        pdf_metadata = st.session_state.get("translation_metadata", {})
        
        st.markdown("### 📄 Translated PDF Ready")
        
        # Download button
        st.download_button(
            "📥 Download Translated PDF",
            data=st.session_state["translated_pdf_bytes"],
            file_name="translated_document.pdf",
            mime="application/pdf",
            use_container_width=True
        )
        
        # Show metadata if requested
        if show_metadata and pdf_metadata:
            with st.expander("🔍 Processing Details"):
                col_meta1, col_meta2 = st.columns(2)
                
                with col_meta1:
                    st.write("**Pages:**", pdf_metadata.get("total_pages", "N/A"))
                    st.write("**Engine:**", pdf_metadata.get("engine", "N/A").title())
                    st.write("**Source:**", LANGUAGE_SLUGS.get(pdf_metadata.get("source_lang"), "N/A"))
                
                with col_meta2:
                    st.write("**Target:**", LANGUAGE_SLUGS.get(pdf_metadata.get("target_lang"), "N/A"))
                    errors = pdf_metadata.get("errors", [])
                    st.write("**Errors:**", len(errors))
                    
                    if errors and debug_mode:
                        st.write("**Error Details:**")
                        for i, error in enumerate(errors[:3], 1):
                            st.write(f"{i}. {error}")

# Footer
st.markdown("---")
st.markdown("""
<div style='text-align: center; padding: 20px; color: #666;'>
    <p>🌐 Advanced Translator with Smart PDF Processing</p>
    <p>Powered by Enhanced Span Batching & OCR Technology</p>
</div>
""", unsafe_allow_html=True)