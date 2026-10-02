Act as a university professor. Write official lecture notes based on this transcript.
Your task is to convert the lecture transcript into complete university notes, preserving the professor's voice.

OBJECTIVE:
Transform spoken language into rigorous academic prose WITHOUT summarizing.
The length and level of detail must be nearly identical to the input.
**Natural Register:** Keep the lecturer's tone. If they use simple terms, use simple terms. Only clean up syntax (remove filler words, repetitions), but do not make the text artificially formal.

FORMATTING INSTRUCTIONS:
1. Use `## Title` and `### Subtitle` to structure the text.
2. Always insert a blank line between paragraphs.
3. Convert ALL formulas to LaTeX: inline $f(x)$ or block $$ \int f(x) dx $$.
4. Avoid bullet points unless strictly necessary; prefer flowing discursive prose.

CONTEXT: Part {part_number} of {total_parts}.

--- TRANSCRIPT ---
{chunk_text}
--- END TRANSCRIPT ---

Generate the structured lecture notes:
