# Farmacologia Quiz

Dashboard Streamlit per ripassare farmacologia con domande casuali, tracking delle visualizzazioni e statistiche persistenti.

## Avvio locale

```bash
pip install -r requirements.txt
streamlit run app.py
```

Su Windows puoi anche fare doppio clic su `start_windows.bat`: crea il venv se serve, installa le dipendenze e avvia l'app.

## Rigenerare il dataset

```bash
python parse.py
```

Il parser usa Ollama in locale per costruire le opzioni e la risposta corretta a partire dal PDF.

## Reset statistiche

Cancella `quiz_progress.sqlite3` per azzerare contatori, accuratezza e storico dei tentativi.

## Nota hosting

L'app usa SQLite locale. Per uso continuo su internet serve un hosting con storage persistente, altrimenti le stats possono sparire al riavvio.
