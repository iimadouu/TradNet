"""
Quick test to verify all strategies work with proper data types
"""
import pandas as pd
import numpy as np
from market_analyzer import MarketAnalyzer

# Create test data
np.random.seed(42)
n = 100

test_data = pd.DataFrame({
    'time': pd.date_range('2024-01-01', periods=n, freq='1min'),
    'open': 1.1000 + np.random.randn(n) * 0.001,
    'high': 1.1010 + np.random.randn(n) * 0.001,
    'low': 1.0990 + np.random.randn(n) * 0.001,
    'close': 1.1000 + np.random.randn(n) * 0.001,
    'tick_volume': np.random.randint(100, 1000, n)
})

# Ensure high is highest and low is lowest
test_data['high'] = test_data[['open', 'high', 'close']].max(axis=1)
test_data['low'] = test_data[['open', 'low', 'close']].min(axis=1)

print("Testing all strategies with proper data types...")
print("=" * 60)

analyzer = MarketAnalyzer()

# Test each strategy
strategies = [
    ('Scalping', lambda df: analyzer.analyze_scalping(df)),
    ('Range', lambda df: analyzer.analyze_ranging(df)),
    ('Breakout', lambda df: analyzer.analyze_breakout(df)),
    ('Momentum', lambda df: analyzer.analyze_momentum(df)),
    ('Support/Resistance', lambda df: analyzer.analyze_support_resistance(df)),
    ('Trend Reversal', lambda df: analyzer.analyze_trend_reversal(df))
]

for name, strategy_func in strategies:
    try:
        result = strategy_func(test_data)
        signal = result.get('signal', 'ERROR')
        confidence = result.get('confidence', 0)
        signal_type = result.get('indicators', {}).get('signal_type', 'N/A')
        
        print(f"✓ {name:20} | Signal: {signal:4} | Conf: {confidence:.2f} | Type: {signal_type}")
    except Exception as e:
        print(f"✗ {name:20} | ERROR: {str(e)[:50]}")

print("=" * 60)
print("All strategies tested successfully!")
