import json
import re
from pathlib import Path
import PyPDF2
import ollama


QUESTION_PATTERN = re.compile(r'(?:(?<=^)|(?<=[\s\)\]\.:;]))(?P<number>\d{1,3})\.\s')

def extract_text_from_pdf(pdf_path: str) -> str:
    text = ""
    with open(pdf_path, 'rb') as file:
        reader = PyPDF2.PdfReader(file)
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                text += extracted + "\n"
    return text

def parse_questions(text: str) -> list:
    matches = list(QUESTION_PATTERN.finditer(text))
    questions = []

    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        question_text = text[start:end].strip()

        if question_text:
            questions.append((int(match.group('number')), question_text))

    return questions

def generate_distractors(question_text: str, model_name: str = "llama3.1:latest") -> dict:
    prompt = f"""
    Sei un docente universitario di farmacologia.
    Analizza il seguente testo estratto da un blocco di appunti, che contiene una domanda a risposta singola o multipla e la sua risposta corretta.
    Testo: "{question_text}"
    
    Il tuo compito:
    1. Estrai e formula in modo chiaro la "domanda".
    2. Estrai la "risposta_corretta".
    3. Genera 3 "distrattori" (risposte errate ma scientificamente plausibili in ambito medico/farmacologico per confondere uno studente).
    
    Restituisci ESCLUSIVAMENTE un oggetto JSON con questa struttura esatta, senza markdown o testo aggiuntivo:
    {{
        "domanda": "testo della domanda",
        "opzioni": ["risposta_corretta", "distrattore_1", "distrattore_2", "distrattore_3"],
        "risposta_corretta": "risposta_corretta"
    }}
    """
    
    try:
        response = ollama.chat(
            model=model_name,
            messages=[{'role': 'user', 'content': prompt}],
            format='json'
        )
        return json.loads(response['message']['content'])
    except Exception as e:
        print(f"Errore durante la generazione per il testo: {question_text[:30]}... Dettaglio: {e}")
        return None

def main():
    base_dir = Path(__file__).resolve().parent
    pdf_path = base_dir / 'Domande Farmaco OK.pdf'
    output_json = base_dir / 'dataset_farmacologia.json'

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF non trovato: {pdf_path}")
    
    print("Inizio estrazione testo dal PDF...")
    text = extract_text_from_pdf(str(pdf_path))
    
    print("Parsing preliminare delle domande...")
    raw_questions = parse_questions(text)
    
    dataset = []
    total = len(raw_questions)
    print(f"Trovate {total} domande. Inizio generazione JSON...")
    
    for i, (question_id, q_text) in enumerate(raw_questions):
        print(f"Elaborazione {i+1}/{total}...")
        data = generate_distractors(q_text, model_name="llama3.1:latest")
        if data:
            data['id'] = question_id
            dataset.append(data)
            
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(dataset, f, indent=4, ensure_ascii=False)
        
    print(f"Dataset salvato in {output_json} con {len(dataset)} domande valide.")

if __name__ == "__main__":
    main()