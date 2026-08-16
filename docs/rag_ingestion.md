# RAG knowledge-base ingestion

This repository's RAG ingestion is an explicit offline operation. It reads only approved Markdown source documents in `rag_documents/` from these categories: `care_navigation`, `clinical_guidelines`, `emergency_guidelines`, `emr`, `patient_education`, `rules_procedures`, and `us_insurance_policies`. `README.md`, `rag_source_registry.md`, and `.gitkeep` files are guidance/placeholders and are not indexed.

The Pinecone index stores approved knowledge-base content only. It does not store member-specific or patient-specific embeddings.

RAG retrieval is not a clinical decision mechanism.

## Configuration

Copy the names in `.env.example` into a local `.env` or environment. Required values are `GEMINI_API_KEY`, `PINECONE_API_KEY`, and `PINECONE_INDEX_NAME`. `PINECONE_KNOWLEDGE_NAMESPACE` defaults to `knowledge-base`; it must remain a shared knowledge namespace, never a member or patient identifier.

The default embedding configuration is `gemini-embedding-2` with a requested output dimension of `768`. Both values are configuration-driven through `RAG_EMBEDDING_MODEL` and `RAG_EMBEDDING_DIMENSION`. Google GenAI's supported `models.embed_content` API receives `EmbedContentConfig(output_dimensionality=...)`; the ingestion job validates every returned vector against the configured dimension.

Create the Pinecone index outside this application. Its dimension must equal `RAG_EMBEDDING_DIMENSION` and its metric must equal `PINECONE_INDEX_METRIC` (default `cosine`, appropriate for semantic retrieval). The job validates both before writing, does not create indexes, and does not execute during Flask startup.

## Chunking and records

Markdown is parsed deterministically: each level-two section is normalized and split into approximately 1,200-character chunks with up to 200 characters of overlap. A standalone level-one title preamble is not indexed independently because title and section context prefix every meaningful chunk. This avoids title-only vectors while retaining substantive short sections. Each vector ID is a SHA-256 digest of the stable document ID, ordinal, and normalized chunk text. Re-running unchanged sources therefore upserts the same IDs rather than creating duplicate logical chunks.

Pinecone records have the configured embedding as `values` and compact metadata:

- `document_id`, `source_file`, `category`, `title`, `source_type`, `chunk_ordinal`
- `source_organization`, `source_url`, and `version` when available
- `text`, containing only the retrieved chunk (not an entire source document), so future grounded retrieval can recover its context

The supported metadata filters are `category`, `document_id`, and `source_type`; source-file filtering is also available. Provenance is extracted only from the source document. Missing source URL/version values are omitted rather than fabricated.

## Run and verify

Install project dependencies, configure the values above, then run:

```powershell
.venv\Scripts\python backend\ingest_rag_documents.py
```

Successful output reports the discovered document, chunk, and upserted-record counts. The command validates the source directory, readability, non-empty Markdown/title/chunks, embedding count/dimension, required configuration, existing Pinecone index dimension, and upsert result. It intentionally does not print API keys.

Do not index BENE_ID values, member identifiers, names, dates of birth, addresses, claims, encounters, raw EMR records, model scores/ranks, patient symptoms, triage requests, or PostgreSQL data. The shared knowledge index must contain only the reviewed sources above; synthetic EMR documentation is allowed only because it contains no real patient data.
