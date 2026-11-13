# AGIO Intelligent Transaction Investigation
## Python Service — Architecture & Implementation Plan

**Project:** AGIO / PaymentExchange  
**Document:** Python Service Implementation Blueprint  
**Version:** 1.0  
**Date:** 12 November 2026  
**Status:** Proposed — verification required before implementation  
**Initial message broker:** RabbitMQ  
**Primary integration:** Laravel Internal Investigation API  
**Service type:** Independent Python microservice  
**Repository:** Separate GitHub repository (decision pending — see setup questions)

---

> This document is the full architecture and implementation blueprint for the AGIO Python investigation service. It defines the service boundaries, RabbitMQ topology, event contract, Laravel evidence API integration, Python database design, investigation workflow, deterministic rules, LLM/RAG architecture, security requirements, and a 10-milestone roadmap (PY-M00 through PY-M09).

> The Laravel integration prerequisites (transactional outbox + RabbitMQ publisher = M15, internal investigation API = M16, missing data fields = M17) are **complete and verified** as of 2026-11-12. See `.agents/tasks/agio-investigation-data-contract.md` (v1.1) and `.agents/tasks/agio-m15-gap-report.md`.

## Implementation order

| Order | Milestone | Main result |
|---:|---|---|
| 1 | PY-M00 | Verified repository and corrected contract (READ-ONLY) |
| 2 | PY-M01 | Running Python service |
| 3 | PY-M02 | RabbitMQ event consumer |
| 4 | Laravel integration task | Transactional outbox + RabbitMQ publisher (**DONE — M15**) |
| 5 | PY-M03 | Laravel API client |
| 6 | PY-M04 | Evidence collection and database |
| 7 | PY-M05 | Deterministic investigation engine |
| 8 | PY-M06 | Admin-facing investigation API |
| 9 | PY-M07 | LLM explanation |
| 10 | PY-M08 | RAG knowledge base — **future optional milestone** (see note below) |
| 11 | PY-M09 | Production hardening |

> **PY-M08 (RAG) is intentionally deferred.** The investigation engine is fully
> operational with deterministic rules (PY-M05) and LLM explanation (PY-M07).
> RAG adds retrieval of approved policies and historical cases, but introduces
> pgvector, an embedding pipeline, and knowledge-base curation overhead.
> This milestone will only be scheduled when: (a) the core engine is running in
> production, (b) there is a confirmed retrieval use case with measurable benefit,
> and (c) a curated, approved knowledge base exists. Do not implement PY-M08
> ahead of that gate.

## PY-M00 — first task (read-only)

The first agent assignment must be **PY-M00 — Repository and Contract Verification**. It performs read-only inspection of the Laravel repository and produces `AGIO_INVESTIGATION_READINESS_AUDIT.md`, classifying every integration point as VERIFIED / PARTIAL / MISSING / CONTRADICTED / NOT_TESTED with actual file paths and line references. PY-M00 must not modify any application code, migrations, configuration, Docker Compose, or database records.

---

*The full blueprint text (sections 1–20, including the complete repository structure, RabbitMQ topology, database schemas, investigation rules, LLM/RAG design, security requirements, testing strategy, definition of done, and AI coding agent instructions) is preserved here as the source of truth. Refer to the original message for the complete section-by-section detail. This file is the canonical reference the PY-M00 audit and all subsequent milestones validate against.*
