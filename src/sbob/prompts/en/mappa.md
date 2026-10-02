Build the card of a university lecture for the map of the course "{corso}". The map helps an AI assistant find its way through the lectures.

Reply ONLY with a valid JSON object, no text before or after, shaped like:
{{"riassunto": "...", "concetti": [{{"nome": "...", "ruolo": "introdotto"}}], "prerequisiti": ["..."]}}

RULES:
- riassunto: 3-5 sentences on what is explained and how (theory, examples, exercises), in English.
- concetti: 3 to 10 key concepts of the lecture. "ruolo" is "introdotto" if this lecture explains it for the first time, "ripreso" if it uses or deepens it.
- Concept names: short (1-4 words), singular, capitalised, no ambiguous acronyms.
- If a concept is already in the list below, REUSE EXACTLY that name.
- prerequisiti: concepts one must already know to follow the lecture (even outside the course). At most 5.

CONCEPTS ALREADY IN THE COURSE:
{concetti_esistenti}

--- NOTES ({lezione}) ---
{testo}
--- END NOTES ---
