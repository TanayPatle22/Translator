from django import forms
from .utils import LANGUAGE_SLUGS
from .PDF2text import extract_text_for_validation, extract_blocks_from_pdf


ENGINE_CHOICES = [
    ("google", "Google Translator"),
    ("gemini", "Gemini (LLM-based)"),
]

class TranslationForm(forms.Form):
    source_text = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 4, 'cols': 40, 'style': 'overflow-y: auto;', 'placeholder' : 'Enter source text'}))
    source_lang = forms.ChoiceField(label = 'Source language', choices=LANGUAGE_SLUGS.items)
    target_lang = forms.ChoiceField(label = 'Target language', choices=LANGUAGE_SLUGS.items)
    source_file = forms.FileField(required=False, label='Upload PDF file')
    engine = forms.ChoiceField(choices=ENGINE_CHOICES, required=True, initial="gemini")

    def clean_source_file(self):
        file = self.cleaned_data.get('source_file')
        if file:
            # Validate file extension
            if not file.name.lower().endswith('.pdf'):
                raise forms.ValidationError("Only PDF files are allowed.")
            max_size = 10 * 1024 * 1024  # 10 MB
            if file.size > max_size:
                raise forms.ValidationError("File size must be under 10 MB.")
        return file
    
    def clean(self):
        cleaned_data = super().clean()
        source_text = cleaned_data.get('source_text')
        source_file = cleaned_data.get('source_file')

        # Ensure at least one input is provided
        if not source_text and not source_file:
            raise forms.ValidationError("Please provide text or upload a PDF file.")
        
        if source_file and not source_text:
            try:
                # Extract text for validation (quick check)
                extracted_text = extract_text_for_validation(source_file.file)
                if not extracted_text.strip():
                    raise forms.ValidationError("Uploaded PDF contains no readable text.")
                cleaned_data['source_text'] = extracted_text

                # 🔹 Extract full blocks for hybrid pipeline
                source_file.file.seek(0)  # reset pointer after validation
                blocks = extract_blocks_from_pdf(source_file.file)
                cleaned_data['blocks'] = blocks   # ✅ store blocks
                
            except Exception as e:
                raise forms.ValidationError(f"Error extracting text from PDF: {str(e)}")


        return cleaned_data
