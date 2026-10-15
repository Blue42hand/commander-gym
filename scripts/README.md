# Agentic Harness for Replay-Safe State Caching

This directory contains tools to help maintain focus on the implementation goal and track progress.

## Goal Statement

**Reduce pilot context duplication with replay-safe state caching** while maintaining:
- Full reproducibility of game trajectories
- Human-play parity (no opponent hidden information leakage)
- Optional opt-in behavior at the adapter boundary

## Quick Start

```bash
# Check overall progress
./scripts/check-progress.sh

# Check specific phase
./scripts/check-progress.sh phase-1
./scripts/check-progress.sh phase-2
./scripts/check-progress.sh phase-3
```

## Components

### 1. Progress Tracker (`check-progress.sh`)
A bash script that:
- ✅ Verifies implementation plan exists
- ✅ Checks required files are created
- ✅ Validates no hidden information leakage
- ✅ Displays current status table

### 2. GitHub Actions Workflow (`.github/workflows/replay-cache-check.yml`)
Automated checks on every push/PR that:
- Ensures plan file is present
- Verifies core implementation files exist
- Checks for potential security issues (hidden info leakage)
- Reports progress summary

## Usage Guidelines

### During Development
1. **Before starting work**: Run `./scripts/check-progress.sh` to see current state
2. **After completing a task**: Update the plan file (`docs/replay-safe-state-caching-plan.md`)
3. **Before committing**: Verify no hidden information leakage with the script
4. **When ready for PR**: Ensure all Phase 1 tasks are complete and tested

### Key Safety Checks
The harness enforces these critical constraints:
- ❌ No opponent hidden/secret/private card data in caches
- ✅ Only seat-visible information cached
- ✅ Deterministic cache reset behavior
- ✅ Replayability from trajectory logs maintained

## Acceptance Criteria Checklist

Use this as your checklist for completion:

- [ ] Repeated unchanged observations do not require full prompt duplication
- [ ] A persisted trajectory can reconstruct the effective model-visible context
- [ ] Cache reset behavior is deterministic and tested
- [ ] No opponent hidden information is introduced by caching
- [ ] The optimization is optional at the provider/adapter boundary

## Next Steps After Phase 1 Complete

1. Write comprehensive unit tests for `ObservationIdentity` and `StateCacheManager`
2. Measure actual token savings in test scenarios
3. Proceed to Phase 2: Card text deduplication
4. Integrate into `CommanderGymPlayerController` (Phase 3)
