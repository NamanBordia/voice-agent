# ADR 001: Sample Rate 16000

## Status

Accepted

## Context

The current prototype is tuned around Exotel + Pipecat behavior at a known transport/pipeline sample rate.

## Decision

Keep transport and pipeline sample rate fixed at 16000 Hz for v1. Do not make sample rate configurable yet.

## Consequences

This reduces configuration drift and debugging variables, but limits flexibility until later phases.