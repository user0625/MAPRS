Extract only these low-confidence or missing fields:
{requested_fields}

Current deterministic metadata:
{current_metadata}

Fields requiring candidate adjudication even when already populated:
{adjudicate_fields}

First-page layout candidates (`bbox` is x0,y0,x1,y1; rotation is degrees):
{layout_candidates}

First page text:
{first_page_text}

Abstract candidate:
{abstract_candidate}

Section heading candidates:
{section_candidates}

For every populated field, add a confidence entry using the same field name. Do not alter fields outside requested_fields. For adjudicated title, authors, and venue, select or combine only text present in the supplied candidates. Ignore rotated arXiv stamps, running headers, affiliations, email addresses, dates, and section headings. A title may combine adjacent lines with matching typography. Authors normally occur immediately after the complete title and before affiliations.

{schema_instruction}
