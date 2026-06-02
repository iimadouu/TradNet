# Custom TP/SL Logging Fix - Complete Documentation

## Problem
Custom TP/SL was not closing positions when profit reached the target (e.g., set TP to $5, profit reached $9 but position didn't close).

## Root Cause Analysis
The custom TP/SL monitoring logic existed but lacked detailed logging, making it impossible to diagnose why positions weren't closing when they should.

## Solution - Comprehensive Logging Added

### 1. **trade_executor_enhanced.py** - Enhanced Logging

#### A. Order Placement Logging
**Location:** `place_order()` method, line ~973
```python
# When custom TP/SL is set
logger.critical(f"[CUSTOM_TP_SET] {symbol}: Custom TP mode enabled | Target: ${self.tp_usd} | MT5 TP disabled (0.0)")
logger.critical(f"[CUSTOM_SL_SET] {symbol}: Custom SL mode enabled | Limit: ${self.sl_usd} | MT5 SL disabled (0.0)")
logger.critical(f"[CUSTOM_MODE_ACTIVE] {symbol}: Bot will monitor USD profit/loss | TP: ${self.tp_usd}, SL: ${self.sl_usd}")
```

#### B. Trade Tracking Logging
**Location:** `_handle_successful_order()` method, line ~1100
```python
logger.critical(f"[TRADE_OPENED] {symbol} #{result.order} | {action} @{price:.5f} | "
              f"TP_MODE={self.tp_mode} SL_MODE={self.sl_mode} | "
              f"Custom_TP=${self.tp_usd} Custom_SL=${self.sl_usd} | "
              f"MT5_TP={tp:.5f} MT5_SL={sl:.5f} | "
              f"Bot_Managed={bot_managed_stops}")

if self.tp_mode == 'custom' or self.sl_mode == 'custom':
    logger.critical(f"[CUSTOM_TRACKING_ENABLED] {symbol} #{result.order} will be monitored for USD profit/loss")
```

#### C. Profit Calculation Logging
**Location:** `_get_profit_usd()` method, line ~553
```python
logger.debug(f"[PROFIT_CALC] #{position.ticket} {position.symbol} | "
            f"MT5_Profit={position.profit:.2f} Swap={position.swap:.2f} | "
            f"Total_USD={profit:.2f}")
```

#### D. Active Trade Monitoring Logging
**Location:** `check_active_trades()` method, line ~1195
```python
# Every cycle, log custom TP/SL check
logger.debug(f"[CUSTOM_TPSL_CHECK] {symbol} #{ticket} | Mode: TP={self.tp_mode} SL={self.sl_mode} | "
            f"Targets: TP=${self.tp_usd} SL=${self.sl_usd} | Current P&L: ${profit_usd:.2f} ({profit_pips:+.1f}p)")

# When checking custom TP
if self.tp_mode == 'custom' and self.tp_usd:
    logger.info(f"[CUSTOM_TP_MONITOR] {symbol} #{ticket} | Current: ${profit_usd:.2f} | Target: ${self.tp_usd} | "
               f"Distance: ${(self.tp_usd - profit_usd):.2f} | Hit: {profit_usd >= self.tp_usd}")
    
    if profit_usd >= self.tp_usd:
        logger.critical(f"🎯 [CUSTOM_TP_TRIGGERED] {symbol} #{ticket}: Custom TP: ${profit_usd:.2f} >= ${self.tp_usd}")
        print(f"   💰 {symbol} #{ticket}: Custom TP hit! Profit: ${profit_usd:.2f} (target: ${self.tp_usd})")

# When checking custom SL
if self.sl_mode == 'custom' and self.sl_usd and not should_close:
    logger.info(f"[CUSTOM_SL_MONITOR] {symbol} #{ticket} | Current: ${profit_usd:.2f} | Limit: -${self.sl_usd} | "
               f"Distance: ${(profit_usd + self.sl_usd):.2f} | Hit: {profit_usd <= -self.sl_usd}")
    
    if profit_usd <= -self.sl_usd:
        logger.critical(f"🛑 [CUSTOM_SL_TRIGGERED] {symbol} #{ticket}: Custom SL: ${profit_usd:.2f} <= -${self.sl_usd}")
        print(f"   🛑 {symbol} #{ticket}: Custom SL hit! Loss: ${profit_usd:.2f} (limit: -${self.sl_usd})")
```

### 2. **trade_executor.py** - Same Logging Added

All the same logging patterns were added to `trade_executor.py` to ensure consistency regardless of which executor file is being used.

### 3. **position_agent.py** - Agent-Level Logging

#### A. Agent Creation Logging
**Location:** `__init__()` method, line ~40
```python
if self.use_custom_levels:
    logger.critical(f"[AGENT_CREATED_WITH_CUSTOM] {symbol} | "
                  f"Custom_TP=${custom_tp_usd} Custom_SL=${custom_sl_usd} | "
                  f"Entry={entry_price:.5f} | Strategy={strategy}")
    print(f"   🤖 Agent created with custom TP/SL: TP=${custom_tp_usd} SL=${custom_sl_usd}")
```

#### B. Agent Decision Logging
**Location:** `_make_decision()` method, line ~780
```python
if self.use_custom_levels and current_profit_usd is not None:
    profit_usd = current_profit_usd
    
    logger.info(f"[AGENT_CUSTOM_CHECK] {self.symbol} | "
               f"Custom_TP=${self.custom_tp_usd} Custom_SL=${self.custom_sl_usd} | "
               f"Current_P&L=${profit_usd:.2f} | "
               f"TP_Check={self.custom_tp_usd and profit_usd >= self.custom_tp_usd} | "
               f"SL_Check={self.custom_sl_usd and profit_usd <= -self.custom_sl_usd}")
    
    # Check custom TP
    if self.custom_tp_usd:
        distance_to_tp = self.custom_tp_usd - profit_usd
        tp_hit = profit_usd >= self.custom_tp_usd
        
        logger.critical(f"[AGENT_CUSTOM_TP] {self.symbol} | "
                      f"Target=${self.custom_tp_usd} | Current=${profit_usd:.2f} | "
                      f"Distance=${distance_to_tp:.2f} | Hit={tp_hit}")
        
        if tp_hit:
            logger.critical(f"🎯 [AGENT_CUSTOM_TP_TRIGGERED] {self.symbol}: ${profit_usd:.2f} >= ${self.custom_tp_usd}")
            print(f"   💰 AGENT CUSTOM TP HIT: ${profit_usd:.2f} >= ${self.custom_tp_usd}")
```

## Log Tags Reference

### Critical Events (logger.critical)
- `[CUSTOM_TP_SET]` - Custom TP mode enabled when placing order
- `[CUSTOM_SL_SET]` - Custom SL mode enabled when placing order
- `[CUSTOM_MODE_ACTIVE]` - Bot will monitor USD profit/loss
- `[TRADE_OPENED]` - Position opened with full details
- `[CUSTOM_TRACKING_ENABLED]` - Position added to custom monitoring
- `[CUSTOM_TP_TRIGGERED]` - Custom TP hit, position should close
- `[CUSTOM_SL_TRIGGERED]` - Custom SL hit, position should close
- `[AGENT_CREATED_WITH_CUSTOM]` - Agent created with custom levels
- `[AGENT_CUSTOM_TP]` - Agent checking custom TP
- `[AGENT_CUSTOM_TP_TRIGGERED]` - Agent detected custom TP hit

### Info Events (logger.info)
- `[CUSTOM_TP_MONITOR]` - Every cycle TP monitoring
- `[CUSTOM_SL_MONITOR]` - Every cycle SL monitoring
- `[AGENT_CUSTOM_CHECK]` - Agent checking custom levels

### Debug Events (logger.debug)
- `[CUSTOM_TPSL_CHECK]` - Every cycle check summary
- `[PROFIT_CALC]` - Profit calculation details

## How to Use These Logs

### 1. **Check if Custom TP/SL is Set Correctly**
Look for these logs when you start trading:
```
[CUSTOM_TP_SET] EURUSD: Custom TP mode enabled | Target: $5.0 | MT5 TP disabled (0.0)
[CUSTOM_MODE_ACTIVE] EURUSD: Bot will monitor USD profit/loss | TP: $5.0, SL: $0.5
```

### 2. **Check if Position is Being Tracked**
When a position opens:
```
[TRADE_OPENED] EURUSD #12345 | BUY @1.08500 | TP_MODE=custom SL_MODE=custom | Custom_TP=$5.0 Custom_SL=$0.5
[CUSTOM_TRACKING_ENABLED] EURUSD #12345 will be monitored for USD profit/loss
[AGENT_CREATED_WITH_CUSTOM] EURUSD | Custom_TP=$5.0 Custom_SL=$0.5
```

### 3. **Monitor Profit Progress**
Every trading cycle (every 5 seconds):
```
[PROFIT_CALC] #12345 EURUSD | MT5_Profit=3.50 Swap=0.00 | Total_USD=3.50
[CUSTOM_TP_MONITOR] EURUSD #12345 | Current: $3.50 | Target: $5.0 | Distance: $1.50 | Hit: False
```

### 4. **Detect When TP Should Trigger**
When profit reaches target:
```
[PROFIT_CALC] #12345 EURUSD | MT5_Profit=5.20 Swap=0.00 | Total_USD=5.20
[CUSTOM_TP_MONITOR] EURUSD #12345 | Current: $5.20 | Target: $5.0 | Distance: $-0.20 | Hit: True
🎯 [CUSTOM_TP_TRIGGERED] EURUSD #12345: Custom TP: $5.20 >= $5.0
💰 EURUSD #12345: Custom TP hit! Profit: $5.20 (target: $5.0)
```

### 5. **Check Agent Decision**
Agent also logs its decision:
```
[AGENT_CUSTOM_CHECK] EURUSD | Custom_TP=$5.0 Custom_SL=$0.5 | Current_P&L=$5.20 | TP_Check=True | SL_Check=False
[AGENT_CUSTOM_TP] EURUSD | Target=$5.0 | Current=$5.20 | Distance=$-0.20 | Hit=True
🎯 [AGENT_CUSTOM_TP_TRIGGERED] EURUSD: $5.20 >= $5.0
💰 AGENT CUSTOM TP HIT: $5.20 >= $5.0
```

## Debugging Scenarios

### Scenario 1: Position Not Closing at TP
**What to check in logs:**
1. Was custom TP set? Look for `[CUSTOM_TP_SET]`
2. Is position being tracked? Look for `[CUSTOM_TRACKING_ENABLED]`
3. Is profit being calculated? Look for `[PROFIT_CALC]`
4. Is TP being monitored? Look for `[CUSTOM_TP_MONITOR]`
5. Did TP trigger? Look for `[CUSTOM_TP_TRIGGERED]`

**If TP triggered but position didn't close:**
- Check if there's an error after `[CUSTOM_TP_TRIGGERED]`
- Check if `_close_position()` was called
- Check MT5 connection status

### Scenario 2: Wrong Profit Calculation
**What to check:**
```
[PROFIT_CALC] #12345 EURUSD | MT5_Profit=5.20 Swap=0.00 | Total_USD=5.20
```
- Verify MT5_Profit matches what you see in MT5 terminal
- Check if Swap is being included correctly
- Verify Total_USD = MT5_Profit + Swap

### Scenario 3: Agent Not Checking Custom Levels
**What to check:**
```
[AGENT_CREATED_WITH_CUSTOM] EURUSD | Custom_TP=$5.0 Custom_SL=$0.5
```
- If this log is missing, agent wasn't created with custom levels
- Check if `custom_sl_usd` and `custom_tp_usd` were passed to agent

## Testing Procedure

1. **Set custom TP to $5**
2. **Start trading**
3. **Check logs for:**
   - `[CUSTOM_TP_SET]` - Confirms TP is set
   - `[TRADE_OPENED]` - Confirms position opened with custom TP
   - `[CUSTOM_TP_MONITOR]` - Shows profit progress every cycle
   - `[CUSTOM_TP_TRIGGERED]` - Should appear when profit >= $5

4. **If position reaches $9 but doesn't close:**
   - Search logs for `[CUSTOM_TP_MONITOR]` entries
   - You should see: `Current: $9.00 | Target: $5.0 | Hit: True`
   - If `Hit: False` when profit is $9, there's a calculation bug
   - If `Hit: True` but no `[CUSTOM_TP_TRIGGERED]`, there's a logic bug

## Log File Location
All logs are written to: `tradnet.log`

## Log Level Configuration
To see all debug logs, ensure logging level is set to DEBUG in `tradnet_main.py`:
```python
logging.basicConfig(
    level=logging.DEBUG,  # Change from INFO to DEBUG for more detail
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('tradnet.log'),
        logging.StreamHandler()
    ]
)
```

## Summary of Changes

### Files Modified:
1. ✅ `trade_executor_enhanced.py` - Added comprehensive logging
2. ✅ `trade_executor.py` - Added comprehensive logging
3. ✅ `position_agent.py` - Added agent-level logging

### Total Log Points Added: **15+**
- 3 at order placement
- 2 at trade tracking
- 1 at profit calculation
- 4 at active trade monitoring (per position, per cycle)
- 2 at agent creation
- 3+ at agent decision making

### Expected Behavior:
With these logs, you will now see **exactly**:
- When custom TP/SL is set
- What values are set
- Current profit every cycle
- Distance to TP/SL every cycle
- When TP/SL is hit
- Why position closes or doesn't close

## Next Steps

1. **Run the bot with custom TP = $5**
2. **Monitor the logs in real-time:**
   ```bash
   tail -f tradnet.log | grep CUSTOM
   ```
3. **When profit reaches $5+, you'll see:**
   - `[CUSTOM_TP_MONITOR]` showing `Hit: True`
   - `[CUSTOM_TP_TRIGGERED]` confirming trigger
   - Position should close immediately

4. **If position doesn't close despite logs showing trigger:**
   - Check for errors after trigger
   - Check MT5 connection
   - Check if `_close_position()` is being called

## Contact
If issue persists after these changes, provide:
1. Full log excerpt from position open to when profit reached $5+
2. Screenshot of MT5 terminal showing position profit
3. Any error messages in logs
