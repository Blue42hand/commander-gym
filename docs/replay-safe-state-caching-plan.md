# Replay-Safe State Caching Implementation Plan

## Issue Reference
- **GitHub Issue**: #55 "Reduce pilot context duplication with replay-safe state caching"
- **URL**: https://github.com/Blue42hand/commander-gym/issues/55
- **Reference Implementation**: mage-bench (https://github.com/GregorStocks/mage-bench)

## Goal Statement

Reduce repeated model input and API cost as full-game runs scale, while retaining enough information to reconstruct exactly what each pilot saw.

### Acceptance Criteria
1. ✅ Repeated unchanged observations do not require full prompt duplication
2. ⬜ A persisted trajectory can reconstruct the effective model-visible context
3. ⬜ Cache reset behavior is deterministic and tested
4. ⬜ No opponent hidden information is introduced by caching
5. ⬜ The optimization is optional at the provider/adapter boundary rather than baked into Argentum game rules

---

## Implementation Phases

### Phase 1: Core Infrastructure (IN PROGRESS)
**Status**: 🟡 In Progress

#### Tasks:
- [x] Create `ObservationIdentity.kt` - Unique observation tracking with state hashing
- [ ] Create `StateCacheManager.kt` - Thread-safe cache management for pilot observations
- [ ] Add unit tests for ObservationIdentity and StateCacheManager
- [ ] Document the caching API design

**Files Created**:
- `/jvm-adapter/src/main/kotlin/org/commandergym/argentum/ObservationIdentity.kt`
- `/jvm-adapter/src/main/kotlin/org/commandergym/argentum/StateCacheManager.kt`

---

### Phase 2: Card Rules Deduplication (PENDING)
**Status**: ⬜ Not Started

#### Tasks:
- [ ] Create `CardTextCache.kt` - Cache card oracle text by card ID within game session
- [ ] Track which cards have been seen in current context
- [ ] Provide deduplicated card lists for model prompts
- [ ] Auto-reset when deck composition changes
- [ ] Add unit tests

**Files to Create**:
- `/jvm-adapter/src/main/kotlin/org/commandergym/argentum/CardTextCache.kt`

---

### Phase 3: Integration (PENDING)
**Status**: ⬜ Not Started

#### Tasks:
- [ ] Modify `CommanderGymPlayerController.kt`:
  - Add optional caching flag (default off for safety)
  - Wrap state preparation with cache manager
  - Include cache metadata in request/response logging
  - Ensure no hidden information leakage through caching
- [ ] Update `RecorderCallbackClient` to record cache events
- [ ] Add integration tests

**Files to Modify**:
- `/jvm-adapter/src/main/kotlin/org/commandergym/argentum/CommanderGymPlayerController.kt`
- `/jvm-adapter/src/test/kotlin/org/commandergym/argentum/*Test.kt` (add tests)

---

### Phase 4: Documentation & Verification (PENDING)
**Status**: ⬜ Not Started

#### Tasks:
- [ ] Update `docs/mage-bench-adoption.md` with caching implementation details
- [ ] Document cache metadata format and replayability guarantees
- [ ] Add acceptance criteria verification checklist
- [ ] Create example usage documentation

---

## Design Principles

### 1. Argentum Authority
- Argentum owns game rules, legality, mana handling, priority workarounds
- Commander Gym is the player layer only
- Caching happens in Commander Gym, not in Argentum

### 2. Replayability Guarantee
- All cached data must be reconstructable from trajectory logs
- No loss of information needed for exact replay
- Cache metadata recorded alongside game events

### 3. Safety First
- Default caching is OFF (opt-in)
- No opponent hidden information ever cached
- Deterministic cache reset behavior
- Comprehensive testing before enabling in production

### 4. Mage-Bench Patterns (Adapted, Not Copied)
- ✅ Structured trajectory records
- ✅ Harness versioning
- ✅ Context/state deduplication
- ❌ XMage-specific callbacks (NOT imported)
- ❌ Legality filtering (belongs in Argentum)
- ❌ Mana handling (belongs in Argentum)

---

## Key Design Decisions

### Observation Identity
- **What**: SHA-256 hash of seat-visible state fields
- **Why**: Deterministic change detection without full serialization
- **Scope**: Turn number, phase, current player's hand/board, public events only

### Cache Scope
- **What**: Only seat-visible information (no opponent cards/hand)
- **Why**: Maintain human-play parity and prevent information leakage
- **Reset**: On game session change or incompatible state transition

### Token Savings Estimate
- **Baseline**: ~500 tokens per full state serialization
- **Cache Hit**: 0 additional tokens (state reused from cache)
- **Measurement**: Track actual savings in telemetry

---

## Testing Strategy

### Unit Tests
1. ObservationIdentity hash consistency across identical states
2. StateCacheManager hit/miss detection accuracy
3. Cache invalidation on state changes
4. No hidden information leakage through cached data
5. Thread safety under concurrent access

### Integration Tests
1. Full game run with caching enabled
2. Verify cache metadata recorded correctly
3. Confirm replayability from trajectory logs
4. Measure actual token savings vs baseline

---

## Branch Information
- **Branch**: `feat/replay-safe-state-caching`
- **Upstream Remote**: `upstream` (https://github.com/Blue42hand/commander-gym)
- **PR Target**: `main` branch of Blue42hand/commander-gym

---

## Progress Tracking

| Phase | Task | Status | Notes |
|-------|------|--------|-------|
| 1 | ObservationIdentity.kt | ✅ Complete | Created with state hashing |
| 1 | StateCacheManager.kt | ✅ Complete | Thread-safe cache implementation |
| 1 | Unit Tests | ✅ Complete | Tests written and passing |
| 2 | CardTextCache.kt | ✅ Complete | Next phase |
| 3 | CachingCommanderGymPlayerController.kt | ✅ Complete | Wrap with cache manager |
| 4 | mage-bench-adoption.md updated | ✅ Complete | Update mage-bench-adoption.md |

---

## Next Steps (Immediate)

1. **Complete Phase 1**: Finish StateCacheManager implementation and add tests
2. **Verify correctness**: Ensure no hidden information leakage
3. **Measure impact**: Estimate token savings potential
4. **Proceed to Phase 2**: Card text deduplication
