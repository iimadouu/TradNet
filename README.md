# TradNet - Smart Trading Bot

AI-powered trading bot for MetaTrader 5 with dual strategy approach.

## Features

- **Dual Strategy System:**
  - Trend Reversal: Captures 5-min chart reversals using RSI + MACD
  - Scalping: Fast EMA crossover with quick exits
  
- **Smart Analysis:** Combines technical indicators with confidence scoring
- **Risk Management:** Automatic position sizing based on account balance
- **Multi-Currency:** Trades EURUSD, GBPUSD, US30, XAUUSD simultaneously
- **Fast Execution:** 5-second scan cycle to minimize lag

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

**Note:** TA-Lib requires additional setup:
- Windows: Download wheel from https://www.lfd.uci.edu/~gohlke/pythonlibs/#ta-lib
- Linux: `sudo apt-get install ta-lib`
- Mac: `brew install ta-lib`

### 2. Configure MT5

Your MT5 credentials are already configured in `tradnet_main.py`:
- Account: 5049304548
- Server: MetaQuotes-Demo

### 3. Run TradNet

```bash
python tradnet_main.py
```

When prompted, enter your desired lot size (default: 0.01).

## Current Mode: PAPER TRADING

The bot is currently in paper trading mode - it will:
- Analyze markets in real-time
- Generate signals
- Log trades to console
- NOT execute real orders

To enable live trading, uncomment the order execution code in `trade_executor.py` (line 103).

## Strategy Details

### Trend Reversal Strategy
- RSI oversold/overbought detection
- MACD crossover confirmation
- Stop Loss: 50 pips
- Take Profit: 100 pips

### Scalping Strategy
- 5/10 EMA crossover
- Momentum confirmation
- Stop Loss: 20 pips
- Take Profit: 15 pips
- Quick exit: 5 pip profit or 10 pip loss

## Risk Management

- Maximum 2% risk per trade
- Automatic position sizing
- One trade per symbol at a time
- Respects symbol min/max lot sizes

## Controls

- **Start:** Run `python tradnet_main.py`
- **Stop:** Press Ctrl+C
- **Modify lot size:** Enter at startup prompt

## File Structure

```
tradnet_main.py       - Main bot orchestration
market_analyzer.py    - Signal generation and analysis
trade_executor.py     - Order execution and risk management
requirements.txt      - Python dependencies
```

## Next Steps

1. Test in paper trading mode for 24-48 hours
2. Review signal quality and win rate
3. Adjust strategy parameters if needed
4. Enable live trading when confident