Please generate a structured final paper reading report.

Paper metadata:
- Title: {title}
- Authors: {authors}
- Year: {year}
- Venue: {venue}
- Abstract: {abstract}

Output language:
{output_language}

Analysis plan:
{analysis_plan}

Reader notes:
{reader_notes}

Critic notes:
{critic_notes}

Retrieved evidence:
{evidence_context}

Report requirements:
1. Generate a structured paper reading report.
2. Include basic information, TL;DR, problem statement, main contributions, method summary, experiments, strengths, limitations, reproducibility notes, and overall assessment.
3. Use the reader notes for factual paper content.
4. Use the critic notes for evaluation and critique.
5. Use evidence IDs only when directly relevant.
6. Do not invent unsupported technical details.
7. The report should be useful for research review and future paper writing.
8. Return a valid JSON object matching the FinalReport schema.
9. The sections field must not be empty.
10. Each section must include title, content, order, and evidence_ids.

{schema_instruction}