Find, download, and register research papers with the RAG corpus.

**Skill**: Load and follow `@research-article-ingest` SKILL.md.

## Instructions

### 1. Parse the request

The user provides one or more of:
- A topic to search for papers on
- Specific arXiv IDs or paper URLs
- A paper title to locate

### 2. Locate papers

If the user gave a topic (not specific IDs):
1. Search the web for relevant papers (prefer arXiv)
2. Present a shortlist with: title, arXiv ID, authors, date, one-line relevance note
3. Ask the user to confirm which papers to ingest (unless they said "add all" or similar)

If the user gave specific IDs/URLs, skip to step 3.

### 3. Choose placement

For each paper, determine the subdirectory and scope. Use the table in the
skill file. If no existing subdirectory fits, propose a new one and confirm
with the user before creating it.

### 4. Ingest

Run `scripts/ingest-article` for each paper. Use `--index` if RAG is running.

```bash
scripts/ingest-article --arxiv <ID> --subdir <subdir> \
    --filename <slug>.pdf \
    --title "<title>" --authors "<authors>" --date <YYYY-MM-DD>
```

### 5. Report

After ingestion, report:
- How many papers were downloaded and registered
- Any failures (download errors, API errors)
- Whether indexing was triggered or deferred to next RAG restart

### 6. Suggest RAG searches (optional)

If the user's goal is to use the papers for context injection, suggest
`rag(op="search", arguments='{"scope": "...", "query": "..."}')` calls with the appropriate scope to verify the new content
is retrievable.
