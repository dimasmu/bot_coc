# Card Count OCR Detection — Design Spec

**Date**: 2026-08-01
**Status**: Approved
**Goal**: Detect card type (hero vs troop/spell) and count (X0, X1, X2...) using OCR on the count badge. Replace unreliable saturation-based grey detection with definitive count-based skipping.

## Problem

`_card_is_grey()` uses HSV saturation (`s_mean < 50`) to detect depleted cards. This is unreliable because:

- Some grey cards retain high saturation
- The saturation threshold is a heuristic, not definitive
- No ability to know how many troops remain (always deploys 50 taps)
- Card type (hero vs troop/spell) is unknown

The real source of truth is the **count badge** ("X0", "X2", "X10") in the top-right corner of each troop/spell card. When it shows X0, the card is empty. Heroes have no badge at all.

## Design

### Card Types (3 categories)

| Type | Badge | Count | Deploy Action |
|------|-------|-------|---------------|
| Hero | None | N/A | Tap card → 1 deploy → done |
| Troop/Spell | X > 0 | 1..N | Tap card → min(N, 10) deploy → re-OCR |
| Empty | X = 0 | 0 | SKIP, mark depleted |

### New method: `_read_card_count(adb, card) → CardResult`

```python
@dataclass
class CardResult:
    count: int | None   # None=hero, 0=empty, >0=count available
    has_badge: bool      # True if badge detected (even if OCR misses number)
```

**Badge region crop** (offsets from card position):
- `badge_x = card["x"] + 8`
- `badge_y = card["card_top"] + 4`
- `badge_w, badge_h = 28, 24`

**OCR pipeline**:
1. Crop badge region from color screencap
2. Convert to grayscale
3. Scale up 2x (small text needs enlargement)
4. OTSU threshold (white text on dark background)
5. EasyOCR with `allowlist="0123456789"`
6. Parse result: regex `\d+` → int

### Hero vs OCR-failed detection

When OCR returns no number, determine if region is truly empty (hero) or badge exists but OCR missed it:

1. Binarize the badge region with OTSU threshold
2. `white_pct = count_nonzero(threshold) / total_pixels`
3. If `white_pct < 0.05` → uniform/dark region → HERO (`count=None, has_badge=False`)
4. If `white_pct >= 0.05` → texture exists → retry OCR with inverted threshold

### Error Handling

| Scenario | Action | Risk |
|----------|--------|------|
| OCR returns 0 | Skip card, mark depleted | Low — definitive |
| OCR returns N > 0 | Deploy min(N, 10), re-OCR next loop | Low — definitive |
| OCR empty + uniform region | HERO: tap once, mark done | Medium |
| OCR empty + textured region | Retry OCR with invert; fallback: deploy 5 taps | Medium |
| OCR empty + retry fails | Conservative: deploy 5 taps, re-check next loop | High |
| Screencap fails | Skip card entirely | Low |

### Modified `_do_attack()` loop

```
Phase 1: classify all cards once
  - _read_card_count() per card
  - Categorize: depleted (X0), heroes (no badge), active (X>0)

Phase 2: deploy loop (until time runs out or all cards depleted)
  for each card (skip depleted):
    if hero:
      tap card + 1 deploy tap → mark depleted
    else (troop/spell):
      re-OCR badge (count may have changed)
      if count == 0 or None → mark depleted
      else:
        tap card to select
        deploy min(count, 10) taps to random deploy zones
        short delay (50-150ms)

Phase 3: wait for battle end
```

### Key numbers
- `min(count, 10)` taps per card per iteration — prevents one card from exhausting all at once
- `badge region`: 28×24px at top-right of each card
- `OCR scale`: 2x enlargement before OCR
- `white_pct < 0.05`: threshold for hero (empty badge region)

### Files changed

| File | Change |
|------|--------|
| `backend/engine/sequence_runner.py` | Add `CardResult` dataclass, `_read_card_count()`, rewrite `_do_attack()`, remove `_card_is_grey()` |
| `backend/vision/ocr.py` | No changes — reuse existing `read_number()` or add small helper |

### What gets removed
- `_card_is_grey()` method — replaced by count-based detection
- Hardcoded `50` taps per card — replaced by count-based `min(count, 10)`

## Out of Scope
- Differentiating troops from spells in deployment behavior (both treated the same)
- Spell-specific placement (manual spell placement is out of scope)
- OCR calibration tool (offset tuning is done manually during testing)
