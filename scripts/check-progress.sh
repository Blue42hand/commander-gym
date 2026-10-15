#!/bin/bash
# Agentic Harness: replay-safe-state-caching Progress Tracker
# Usage: ./scripts/check-progress.sh [phase]

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLAN_FILE="$REPO_ROOT/docs/replay-safe-state-caching-plan.md"

echo "=========================================="
echo "🎯 replay-safe-state-caching Progress Check"
echo "=========================================="
echo ""

# Check if plan exists
if [ ! -f "$PLAN_FILE" ]; then
    echo "❌ ERROR: Implementation plan not found at $PLAN_FILE"
    exit 1
fi

echo "📁 Plan file: $PLAN_FILE"
echo ""

# Display current phase status
echo "📊 Current Status:"
echo "-----------------"
grep -A 50 "Progress Tracking" "$PLAN_FILE" | head -20
echo ""

# Check for specific files based on requested phase or all phases
if [ -n "$1" ]; then
    case "$1" in
        "phase-1")
            echo "🔍 Checking Phase 1 (Core Infrastructure)..."
            echo ""
            
            if [ -f "$REPO_ROOT/jvm-adapter/src/main/kotlin/org/commandergym/argentum/ObservationIdentity.kt" ]; then
                echo "✅ ObservationIdentity.kt exists ($(wc -l < "$REPO_ROOT/jvm-adapter/src/main/kotlin/org/commandergym/argentum/ObservationIdentity.kt") lines)"
            else
                echo "❌ ObservationIdentity.kt missing!"
            fi
            
            if [ -f "$REPO_ROOT/jvm-adapter/src/main/kotlin/org/commandergym/argentum/StateCacheManager.kt" ]; then
                echo "✅ StateCacheManager.kt exists ($(wc -l < "$REPO_ROOT/jvm-adapter/src/main/kotlin/org/commandergym/argentum/StateCacheManager.kt") lines)"
            else
                echo "❌ StateCacheManager.kt missing!"
            fi
            
            # Check for tests
            TEST_COUNT=$(find "$REPO_ROOT" -name "*ObservationIdentity*Test*" -o -name "*StateCacheManager*Test*" 2>/dev/null | wc -l)
            if [ "$TEST_COUNT" -gt 0 ]; then
                echo "✅ Found $TEST_COUNT test file(s)"
            else
                echo "⚠️  No unit tests found for Phase 1 components"
            fi
            
            ;;
        "phase-2")
            echo "🔍 Checking Phase 2 (Card Rules Deduplication)..."
            
            if [ -f "$REPO_ROOT/jvm-adapter/src/main/kotlin/org/commandergym/argentum/CardTextCache.kt" ]; then
                echo "✅ CardTextCache.kt exists ($(wc -l < "$REPO_ROOT/jvm-adapter/src/main/kotlin/org/commandergym/argentum/CardTextCache.kt") lines)"
            else
                echo "❌ CardTextCache.kt not yet implemented"
            fi
            
            ;;
        "phase-3")
            echo "🔍 Checking Phase 3 (Integration)..."
            
            if [ -f "$REPO_ROOT/jvm-adapter/src/main/kotlin/org/commandergym/argentum/CachingCommanderGymPlayerController.kt" ]; then
                echo "✅ CachingCommanderGymPlayerController.kt exists ($(wc -l < "$REPO_ROOT/jvm-adapter/src/main/kotlin/org/commandergym/argentum/CachingCommanderGymPlayerController.kt") lines)"
            else
                echo "❌ CachingCommanderGymPlayerController.kt missing!"
            fi
            
            if grep -q "enableStateCaching" "$REPO_ROOT/jvm-adapter/src/main/kotlin/org/commandergym/argentum/CommanderGymControllerProvider.kt"; then
                echo "✅ CommanderGymControllerProvider has caching support"
            else
                echo "⚠️  No caching integration found in CommanderGymControllerProvider"
            fi
            
            ;;
        *)
            echo "Unknown phase: $1"
            exit 1
            ;;
    esac
else
    # Check all phases
    echo "📋 Full Implementation Checklist:"
    echo ""
    
    echo "Phase 1 - Core Infrastructure:"
    if [ -f "$REPO_ROOT/jvm-adapter/src/main/kotlin/org/commandergym/argentum/ObservationIdentity.kt" ]; then
        echo "  ✅ ObservationIdentity.kt ($(wc -l < "$REPO_ROOT/jvm-adapter/src/main/kotlin/org/commandergym/argentum/ObservationIdentity.kt") lines)"
    else
        echo "  ❌ ObservationIdentity.kt missing"
    fi
    
    if [ -f "$REPO_ROOT/jvm-adapter/src/main/kotlin/org/commandergym/argentum/StateCacheManager.kt" ]; then
        echo "  ✅ StateCacheManager.kt ($(wc -l < "$REPO_ROOT/jvm-adapter/src/main/kotlin/org/commandergym/argentum/StateCacheManager.kt") lines)"
    else
        echo "  ❌ StateCacheManager.kt missing"
    fi
    
    echo ""
    echo "Phase 2 - Card Rules Deduplication:"
    if [ -f "$REPO_ROOT/jvm-adapter/src/main/kotlin/org/commandergym/argentum/CardTextCache.kt" ]; then
        echo "  ✅ CardTextCache.kt ($(wc -l < "$REPO_ROOT/jvm-adapter/src/main/kotlin/org/commandergym/argentum/CardTextCache.kt") lines)"
    else
        echo "  ❌ CardTextCache.kt missing"
    fi
    
    echo ""
    echo "Phase 3 - Integration:"
    if [ -f "$REPO_ROOT/jvm-adapter/src/main/kotlin/org/commandergym/argentum/CachingCommanderGymPlayerController.kt" ]; then
        echo "  ✅ CachingCommanderGymPlayerController.kt ($(wc -l < "$REPO_ROOT/jvm-adapter/src/main/kotlin/org/commandergym/argentum/CachingCommanderGymPlayerController.kt") lines)"
    else
        echo "  ❌ CachingCommanderGymPlayerController.kt missing"
    fi
    
    if grep -q "enableStateCaching" "$REPO_ROOT/jvm-adapter/src/main/kotlin/org/commandergym/argentum/CommanderGymControllerProvider.kt"; then
        echo "  ✅ CommanderGymControllerProvider has caching support"
    else
        echo "  ❌ No integration in CommanderGymControllerProvider"
    fi
    
    echo ""
    echo "Phase 4 - Documentation:"
    if grep -qi "Replay-Safe State Caching" "$REPO_ROOT/docs/mage-bench-adoption.md"; then
        echo "  ✅ mage-bench-adoption.md updated with caching implementation"
    else
        echo "  ❌ mage-bench-adoption.md not yet updated"
    fi
    
fi

echo ""
echo "=========================================="
echo "🎯 Goal: Reduce pilot context duplication while maintaining replayability"
echo "=========================================="
