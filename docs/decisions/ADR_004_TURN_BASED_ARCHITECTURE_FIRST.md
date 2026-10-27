# ADR 004: Turn-Based Architecture First

## Status

Accepted

## Context

Speculative latency paths increase complexity before core correctness is stable.

## Decision

Implement stable committed-turn conversation first. Do not implement speculative two-lane latency optimization yet.

## Consequences

This prioritizes correctness and predictable behavior, with slower perceived response potential until later optimization.