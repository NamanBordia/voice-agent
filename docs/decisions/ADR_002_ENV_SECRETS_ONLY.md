# ADR 002: .env Secrets Only

## Status

Accepted

## Context

Behavior values in `.env` make runtime behavior hard to manage and audit per bot profile.

## Decision

.env should contain secrets and infrastructure settings only. Prompts, model choices, voices, flow config, and behavior settings should move to UI/DB.

## Consequences

This improves runtime configurability and traceability, but requires profile/config persistence design.