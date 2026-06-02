"""
Diagnostic script to understand why no signals are being generated
"""

import MetaTrader5 as mt5
from market_analyzer import MarketAnalyzer
import json

# Initialize MT5
if not mt5.initialize():
    print("❌ Failed to initialize MT5")
    exit()

# Login
if not mt5.login(5049304548, "Ux*m2wCm", "MetaQuotes-Demo"):
    print("❌ Failed to login to MT5")
    exit()

print("=" * 70)
print("🔍 SIGNAL GENERATION DIAGNOSTIC")
print("=" * 70)

analyzer = MarketAnalyzer()
symbols = ['XAUUSD', 'XAGUSD']

for symbol in symbols:
    print(f"\n📊 Analyzing {symbol}...")
    print("-" * 70)
    
    # Get current market data
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 100)
    if rates is None:
        print(f"   ❌ Failed to get rates for {symbol}")
        continue
    
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        print(f"   ❌ Failed to get tick for {symbol}")
        continue
    
    print(f"   Current Price: {tick.ask:.5f}")
    print(f"   Spread: {(tick.ask - tick.bid):.5f}")
    
    # Try each strategy
    strategies = ['scalp', 'momentum', 'range', 'breakout', 'reversal', 'sr_bounce']
    
    for strategy in strategies:
        signal = analyzer.get_market_signals(symbol, allowed_strategy=strategy, debug=True)
        
        if signal and signal['signal'] != 'NONE':
            print(f"   ✅ {strategy:10} → {signal['signal']:4} (confidence: {signal['confidence']:.2f})")
        else:
            print(f"   ❌ {strategy:10} → NONE")

print("\n" + "=" * 70)
print("💡 RECOMMENDATIONS:")
print("=" * 70)

print("\nIf all strategies return NONE:")
print("   1. Market is ranging/choppy (no clear trend)")
print("   2. Volatility is too low")
print("   3. No clear support/resistance levels")
print("   4. Wait for better market conditions")
print("\nThis is GOOD - the bot is protecting you from bad trades!")

mt5.shutdown()
