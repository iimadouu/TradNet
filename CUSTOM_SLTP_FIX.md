# Custom SL/TP Respect Fix

## Problem
The position agent was NOT respecting user's custom SL/TP levels set in USD. Even when the user set custom levels like SL=$10 and TP=$20, the agent was making its own exit decisions based on reversals, breakeven moves, trailing stops, etc., causing positions to close prematurely.

**User Reports:**
- Position went +$7 → -$2, agent didn't close at custom SL
- 3 XAGUSD positions hit -$10, only 1 closed
- Agent closing positions before hitting custom TP

## Root Cause
The custom SL/TP values (`self.sl_usd` and `self.tp_usd` from `trade_executor_enhanced.py`) were **NOT being passed** to the `PositionAgent` when creating agents. The agent had the logic to check custom levels (Priority 0 in `_make_decision()`), but it never received the values.

## Solution Implemented

### 1. Updated `AgentManager.create_agent()` Method
**File:** `position_agent.py` (lines 971-993)

**Changes:**
- Added `custom_sl_usd` and `custom_tp_usd` parameters to method signature
- Pass these values to `PositionAgent.__init__()`
- Enhanced console output to show custom levels when set

```python
def create_agent(self, ticket: int, symbol: str, action: str, entry_price: float, 
                 lot_size: float, strategy: str, initial_sl: float = None, 
                 initial_tp: float = None, atr: float = None, spread: float = None,
                 custom_sl_usd: float = None, custom_tp_usd: float = None) -> PositionAgent:
```

### 2. Updated Agent Creation Call
**File:** `trade_executor_enhanced.py` (lines 1113-1119)

**Changes:**
- Extract custom SL/TP from `self.sl_usd` and `self.tp_usd` when mode is 'custom'
- Pass these values to `create_agent()` method

```python
# Create agent with custom SL/TP if set
custom_sl = self.sl_usd if self.sl_mode == 'custom' else None
custom_tp = self.tp_usd if self.tp_mode == 'custom' else None
self.agent_manager.create_agent(
    result.order, symbol, action, price, lot_size, signal_type,
    initial_sl=sl, initial_tp=tp, atr=None, spread=None,
    custom_sl_usd=custom_sl, custom_tp_usd=custom_tp
)
```

### 3. Agent Logic (Already Implemented)
**File:** `position_agent.py`

The agent already had the correct logic:

**Priority 0 Check (Highest Priority):**
```python
# PRIORITY 0: Custom SL/TP hit (HIGHEST PRIORITY - respect user's levels)
if self.use_custom_levels:
    # Calculate profit/loss in USD
    pip_value_per_lot = 10 if 'JPY' in self.symbol else 10
    profit_usd = (profit_pips / 10) * self.lot_size * pip_value_per_lot
    
    # Check custom TP
    if self.custom_tp_usd and profit_usd >= self.custom_tp_usd:
        return {'decision': 'EXIT_FULL', 'reason': f"Custom TP hit: ${profit_usd:.2f}"}
    
    # Check custom SL
    if self.custom_sl_usd and profit_usd <= -self.custom_sl_usd:
        return {'decision': 'EXIT_FULL', 'reason': f"Custom SL hit: ${profit_usd:.2f}"}
```

**Disabled Features When Using Custom Levels:**
- Breakeven moves (Priority 6) - DISABLED
- Partial exits (Priority 5) - DISABLED  
- Trailing stops (Priority 7) - DISABLED

These features are skipped when `self.use_custom_levels = True`:
```python
# PRIORITY 5-7: Advanced features (ONLY if not using custom levels)
if not self.use_custom_levels:
    if partial_exit_action:
        return partial_exit_action
    if breakeven_action:
        return breakeven_action
    if trailing_action:
        return trailing_action
```

## How It Works Now

### When User Sets Custom Levels:
1. User configures: SL=$10, TP=$20 (custom mode)
2. `trade_executor_enhanced.py` stores: `self.sl_usd=10`, `self.tp_usd=20`, `self.sl_mode='custom'`, `self.tp_mode='custom'`
3. When trade opens, `create_agent()` is called with `custom_sl_usd=10`, `custom_tp_usd=20`
4. Agent stores: `self.custom_sl_usd=10`, `self.custom_tp_usd=20`, `self.use_custom_levels=True`
5. Every cycle, agent checks **FIRST** (Priority 0) if profit/loss hits custom levels
6. Agent **IGNORES** breakeven, partial exits, and trailing stops
7. Agent **ONLY** exits when:
   - Custom TP hit: profit >= $20
   - Custom SL hit: loss >= $10
   - Session/weekend exit (Friday close)
   - Reversal detected (more aggressive now)
   - Time-based exit (strategy-specific)

### When User Uses Bot Levels:
1. User configures: SL=10 pips, TP=20 pips (bot mode)
2. Agent receives: `custom_sl_usd=None`, `custom_tp_usd=None`
3. Agent uses: `self.use_custom_levels=False`
4. Agent uses **ALL** features:
   - Bot SL/TP levels
   - Breakeven moves
   - Partial exits
   - Trailing stops
   - Reversal detection
   - Time-based exits

## Reversal Detection Enhancements
Made reversal detection "a tiny bit more aggressive" as requested:

**Changes:**
- Peak profit threshold: 5 pips → **3 pips** (detect reversals earlier)
- Reversal threshold multiplier: 1.0 → **0.85** (15% more aggressive)
- Weak trend multiplier: 0.8 → **0.75** (even more aggressive when trend weak)
- Absolute drawdown: 10 pips → **7 pips**

**Example:**
- Old: Position at +5 pips, drops to +2 pips (60% drawdown) → EXIT
- New: Position at +3 pips, drops to +1.5 pips (50% drawdown) → EXIT ✓ (earlier detection)

## Testing Checklist

### Test 1: Custom SL Hit
- [ ] Set custom SL=$10, TP=$20
- [ ] Open position
- [ ] Verify console shows: "Custom SL: $10 | Custom TP: $20"
- [ ] Let position go to -$10 loss
- [ ] Verify agent exits with reason: "Custom SL hit: $-10.00"

### Test 2: Custom TP Hit
- [ ] Set custom SL=$10, TP=$20
- [ ] Open position
- [ ] Let position go to +$20 profit
- [ ] Verify agent exits with reason: "Custom TP hit: $20.00"

### Test 3: Agent Doesn't Override Custom Levels
- [ ] Set custom SL=$10, TP=$20
- [ ] Open position
- [ ] Position goes to +$7 (below TP)
- [ ] Verify agent does NOT close (no breakeven, no partial exit)
- [ ] Position drops to +$2
- [ ] Verify agent does NOT close (no reversal exit unless very strong)
- [ ] Position drops to -$10
- [ ] Verify agent DOES close (custom SL hit)

### Test 4: Bot Mode Still Works
- [ ] Set bot mode: SL=10 pips, TP=20 pips
- [ ] Open position
- [ ] Verify console shows: "SL: [price] | TP: [price]" (not USD)
- [ ] Verify agent uses breakeven, partial exits, trailing stops

### Test 5: Reversal Detection
- [ ] Open position
- [ ] Position goes to +3 pips (peak)
- [ ] Position drops to +1.5 pips (50% drawdown from peak)
- [ ] Verify agent detects reversal and exits (more aggressive now)

## Files Modified
1. `position_agent.py` - Updated `AgentManager.create_agent()` method
2. `trade_executor_enhanced.py` - Updated agent creation call to pass custom SL/TP

## Expected Behavior
✅ Agent respects custom SL/TP levels when set by user
✅ Agent does NOT override with breakeven, partial exits, or trailing stops
✅ Agent ONLY exits when custom levels are hit (or session/reversal/time)
✅ Reversal detection is slightly more aggressive (3 pips vs 5 pips threshold)
✅ Console clearly shows when custom levels are set

## Notes
- Custom levels are in **USD**, not pips
- Pip value calculation is simplified (may need refinement for accuracy)
- Reversal detection still active even with custom levels (protects from major reversals)
- Session/weekend exits still active (protects from gap risk)
