# ADR 003: Pipeline Is Not Brain

## Status

Accepted

## Context

Mixing conversation decisions into the pipeline layer creates tight coupling and weak testability.

## Decision

Pipecat pipeline should handle audio/STT/TTS/frame routing only. Conversation logic should live in separate modules.

## Consequences

This improves modularity and independent testing, but adds explicit handoff contracts between layers.