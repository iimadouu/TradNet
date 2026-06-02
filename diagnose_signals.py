"""
Diagnostic script to see why no signals are being generated
"""
import MetaTrader5 as mt5
from market_analyzer import MarketAnalyzer
import pandas as pd

# Initialize MT5
if not mt5.initialize():
    print("MT5 initialization failed")
    exit()

analyzer = MarketAnalyzer()

# Test symbols
test_symbols = ['EURUSD', 'GBPUSD', 'USDJPY', 'XAUUSD']

print("="*70)
print("SIGNAL DIAGNOSTIC TEST")
print("="*70)

for symbol in test_symbols:
    print(f"\n{'='*70}")
    print(f"Testing: {symbol}")
    print(f"{'='*70}")
    
    # Get price data
    df = analyzer.get_price_data(symbol, bars=100)
    if df is None:
        print(f"❌ No price data for {symbol}")
        continue
    
    print(f"✓ Got {len(df)} bars of data")
    
    # Test each strategy
    strategies = {
        'scalp': lambda: analyzer.analyze_scalping(df),
        'range': lambda: analyzer.analyze_ranging(df),
        'breakout': lambda: analyzer.analyze_breakout(df),
        'momentum': lambda: analyzer.analyze_momentum(df),
        'reversal': lambda: analyzer.analyze_trend_reversal(df),
        'sr_bounce': lambda: analyzer.analyze_support_resistance(df)
    }
    
    for strat_name, strat_func in strategies.items():
        try:
            result = strat_func()
            signal = result.get('signal', 'ERROR')
            conf = result.get('confidence', 0)
            signal_type = result.get('indicators', {}).get('signal_type', 'N/A')
            
            if signal != 'NONE':
                print(f"  ✓ {strat_name:12} | {signal:4} | Conf: {conf:.3f} | Type: {signal_type}")
            else:
                print(f"  - {strat_name:12} | NONE | Conf: {conf:.3f}")
        except Exception as e:
            print(f"  ✗ {strat_name:12} | ERROR: {str(e)[:40]}")
    
    # Now test get_market_signals (full pipeline)
    print(f"\n  Full Pipeline Test:")
    try:
        signal = analyzer.get_market_signals(symbol, debug=True)
        final_signal = signal.get('signal', 'ERROR')
        final_conf = signal.get('confidence', 0)
        
        if final_signal != 'NONE':
            print(f"  ✓ FINAL: {final_signal} | Conf: {final_conf:.3f}")
        else:
            print(f"  ✗ FINAL: NONE (filtered out)")
            
            # Check why it was filtered
            indicators = signal.get('indicators', {})
            if 'filtered' in indicators:
                print(f"     Reason: {indicators['filtered']}")
            if 'correlation_risk' in indicators:
                print(f"     Correlation: {indicators['correlation_risk']}")
            if 'sl_tp_issue' in indicators:
                print(f"     SL/TP: {indicators['sl_tp_issue']}")
    except Exception as e:
        print(f"  ✗ FINAL: ERROR - {str(e)[:60]}")

print(f"\n{'='*70}")
print("Diagnostic complete!")
print(f"{'='*70}")

mt5.shutdown()
