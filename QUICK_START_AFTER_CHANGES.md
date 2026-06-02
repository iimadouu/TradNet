# Quick Start Guide - After Signal Aggressiveness Reduction

## What Changed?
The bot was too strict and rejected all signals. We've made it **significantly more aggressive** to detect trading opportunities on 5-minute charts.

## Key Changes at a Glance

| Filter | Before | After | Change |
|--------|--------|-------|--------|
| **Min Confidence** | 0.40 | 0.30 | -25% |
| **Caution Pairs** | 0.60 | 0.45 | -25% |
| **MTF Penalty** | 0.5x (50% cut) | 0.85x (15% cut) | Much less harsh |
| **Risk/Reward** | 1.5-2.0 | 1.2-1.5 | More realistic |
| **Range Strategy** | Very strict | Relaxed by 30-50% | More signals |

## How to Test

### 1. Run with Debug Mode
```bash
# Create debug flag
echo "1" > DEBUG_MODE.txt

# Run the bot
python tradnet_main.py
```

### 2. What to Look For in Logs

**GOOD SIGNS** (signals passing through):
```
✓ EURUSD: Using fallback strategy 'sr_bounce' with confidence 0.63
✓ GBPUSD: Using strategy 'momentum' with confidence 0.58
```

**BAD SIGNS** (still being rejected):
```
Signal rejected - confidence 0.32 below minimum 0.30
Signal rejected - poor_risk_reward
```

### 3. Monitor First 30 Minutes
- **Expected**: 1-5 signals in first 30 minutes (depending on market conditions)
- **If 0 signals**: Check logs for rejection reasons
- **If 10+ signals**: May be too aggressive, monitor win rate

## Expected Behavior

### Before
- 300 cycles = 0 signals ❌
- Strategies found opportunities but filters rejected them

### After
- Should see signals within first 10-20 cycles ✅
- Fallback mechanism tries all strategies
- Lower confidence threshold allows more trades

## Monitoring Performance

### First Hour
- **Goal**: See at least 2-3 signals
- **Watch**: Are signals being executed or still rejected?
- **Check**: Are SL/TP levels reasonable?

### First Day
- **Goal**: 10-20 trades (depending on volatility)
- **Watch**: Win rate (should be > 40%)
- **Check**: Are losses controlled by SL?

## If Still No Signals

Check these in order:

1. **Spread Filter** - May be rejecting pairs with wide spreads
   ```
   Signal filtered (spread too wide: X pips)
   ```

2. **Correlation Filter** - May be blocking correlated pairs
   ```
   Signal blocked (correlated BUY position active)
   ```

3. **Max Positions** - Check if limit is reached
   ```
   signals_filtered['max_positions']
   ```

4. **Market Conditions** - Very low volatility = fewer signals
   - Check if market is open (not weekend)
   - Check if it's a major news event (high volatility)

## Adjusting Further (If Needed)

### Too Few Signals?
Lower confidence even more in `market_analyzer.py`:
```python
MIN_CONFIDENCE = 0.25  # Currently 0.30
```

### Too Many Signals?
Raise confidence slightly:
```python
MIN_CONFIDENCE = 0.35  # Currently 0.30
```

### Poor Win Rate?
Increase risk/reward requirements in `calculate_stop_loss_take_profit()`:
```python
'min_rr': 1.8  # Currently 1.2-1.5
```

## Important Notes

⚠️ **Lower confidence = More trades but potentially lower quality**
- Monitor win rate closely
- If win rate drops below 35%, consider tightening filters

✅ **5-minute charts are noisy**
- Expect more false signals than higher timeframes
- SL/TP management is critical
- Position sizing should be conservative

📊 **Give it time to gather statistics**
- First 20-30 trades are learning period
- Strategy performance tracking will improve over time
- Don't judge too quickly on first few trades

## Quick Troubleshooting

| Issue | Solution |
|-------|----------|
| Still 0 signals after 50 cycles | Check spread filter, may need to increase max spread |
| Too many signals (>20/hour) | Increase MIN_CONFIDENCE to 0.35 |
| Signals found but not executed | Check tradnet_main.py confidence thresholds |
| All signals from one strategy | Normal, market conditions favor different strategies |
| Win rate < 30% | Increase risk/reward requirements or confidence |

## Contact Points

- **Signal detection**: `market_analyzer.py` (strategies)
- **Signal filtering**: `tradnet_main.py` (confidence, spread, correlation)
- **Risk management**: `calculate_stop_loss_take_profit()` in `market_analyzer.py`

---

**Ready to test!** Run the bot and watch for signals in the first 30 minutes. Good luck! 🚀
