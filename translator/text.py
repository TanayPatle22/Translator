import os
import google.generativeai as genai
from .utils import chunk_text, translate_chunks, gemini_translate_text, gemini_translate_chunks


# Explicitly configure Gemini
genai.configure(api_key="AIzaSyDWo4i6KWUUdJZPXcHsMuuSQ1EIkmRz1qE")

sample_text = """Hello world. 
This is a test translation.
Let's see if Gemini can translate it to Hindi."""

print("🔹 Using Gemini (short text):")
print(gemini_translate_text(sample_text, "en", "hi"))

print("\n🔹 Using Gemini (chunked text):")
chunks = chunk_text(sample_text, max_chars=50)
print(gemini_translate_chunks(chunks, "en", "hi"))

