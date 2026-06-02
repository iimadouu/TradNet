"""
Market Analysis Engine - ROBUST VERSION
Handles price data analysis and signal generation with comprehensive error handling
market_analyzer.py
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import talib
import time
import logging
from collections import deque

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
MIN_BARS_FOR_ANALYSIS = 50
MAX_CORRELATION_AGE_HOURS = 24
STRATEGY_COOLDOWN_MINUTES = 30
MAX_PERFORMANCE_HISTORY = 100
SIGNAL_EXPIRY_MINUTES = 5
MAX_RETRY_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1

# Thresholds (configurable)
class Thresholds:
    SPREAD_HIGH_PCT = 0.05
    SPREAD_MEDIUM_PCT = 0.02
    CORRELATION_HIGH = 0.8
    VOLATILITY_EXTREME_PCT = 0.5
    VOLATILITY_HIGH_PCT = 0.2
    VOLATILITY_NORMAL_PCT = 0.08
    ADX_TREND_MIN = 25  # FIXED: Increased from 20 to 25 for real trends
    ADX_STRONG_TREND = 30  # FIXED: Increased from 25 to 30
    RSI_OVERSOLD = 30
    RSI_OVERBOUGHT = 70
    MIN_CONFIDENCE = 0.30  # Lowered from 0.40 to 0.30 for more signals (5-min charts need lower threshold)
    MIN_CONFIDENCE_CAUTION = 0.45  # Lowered from 0.55 to 0.45 for caution pairs
    PATTERN_BOOST = 1.15
    MAX_CONFIDENCE = 0.95
    
    # NEW: Risk management thresholds
    MAX_DAILY_LOSS_PCT = 2.0  # Max 2% account loss per day
    MAX_CORRELATED_POSITIONS = 3  # Max positions in correlated pairs
    MIN_RISK_REWARD = 1.5  # Minimum risk/reward ratio
    SCALP_MIN_PROFIT_SPREAD_RATIO = 3.0  # Profit target must be 3x spread


class MarketAnalyzer:
    def __init__(self):
        self.timeframe = mt5.TIMEFRAME_M5  # 5-minute charts
        self.last_analysis = {}  # Store indicator values for logging
        self.correlation_matrix = {}  # Store pair correlations
        self.correlation_timestamp = None  # Track when correlations were calculated
        self.strategy_performance = {}  # Track win rate per strategy per pair
        self.strategy_cooldowns = {}  # Track strategy cooldown periods
        self.signal_cache = {}  # Cache signals with expiry
        self.performance_history = {}  # Limited history with deque
        
        # NEW: Risk management tracking
        self.daily_loss = 0.0  # Track daily loss
        self.daily_loss_reset_date = datetime.now().date()
        self.active_positions = {}  # Track active positions by symbol
        self.volatility_regime = {}  # Track volatility regime per symbol

    def get_price_data(self, symbol: str, bars: int = 100, timeframe=None) -> Optional[pd.DataFrame]:
        """Get recent price data for analysis with retry logic and validation"""
        tf = timeframe or self.timeframe
        
        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
                
                if rates is None or len(rates) == 0:
                    if attempt < MAX_RETRY_ATTEMPTS - 1:
                        logger.warning(f"Failed to get data for {symbol}, attempt {attempt + 1}/{MAX_RETRY_ATTEMPTS}")
                        time.sleep(RETRY_DELAY_SECONDS)
                        continue
                    logger.error(f"No price data available for {symbol} after {MAX_RETRY_ATTEMPTS} attempts")
                    return None
                
                # Validate data
                if len(rates) < 10:
                    logger.warning(f"Insufficient data for {symbol}: only {len(rates)} bars")
                    return None
                
                df = pd.DataFrame(rates)
                df['time'] = pd.to_datetime(df['time'], unit='s')
                
                # Validate required columns
                required_cols = ['open', 'high', 'low', 'close', 'tick_volume']
                if not all(col in df.columns for col in required_cols):
                    logger.error(f"Missing required columns for {symbol}")
                    return None
                
                # Check for NaN values
                if df[required_cols].isnull().any().any():
                    logger.warning(f"NaN values detected in {symbol} data")
                    df = df.dropna()
                    if len(df) < 10:
                        return None
                
                return df
                
            except Exception as e:
                logger.error(f"Error getting price data for {symbol}: {e}")
                if attempt < MAX_RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue
                return None
        
        return None

    def analyze_multi_timeframe_trends(self, symbol: str) -> Dict:
        """
        Analyze multiple timeframes to get complete market picture
        Returns trend direction and strength across 5m, 15m, 1H, 4H
        FIXED: Better weighting and structure analysis
        """
        timeframes = {
            '5m': (mt5.TIMEFRAME_M5, 1.0),   # weight
            '15m': (mt5.TIMEFRAME_M15, 2.0),  # FIXED: Increased weight
            '1H': (mt5.TIMEFRAME_H1, 3.0),    # FIXED: Increased weight
            '4H': (mt5.TIMEFRAME_H4, 4.0)     # FIXED: Increased weight
        }

        mtf_analysis = {}
        weighted_scores = {'bullish': 0, 'bearish': 0, 'neutral': 0}
        
        # NEW: Track support/resistance from higher timeframes
        key_levels = {'support': [], 'resistance': []}

        for name, (tf, weight) in timeframes.items():
            df = self.get_price_data(symbol, bars=50, timeframe=tf)
            if df is None or len(df) < 20:
                mtf_analysis[name] = {'trend': 'unknown', 'strength': 0}
                continue

            try:
                # Calculate trend using EMA with validation
                close_values = df['close'].values
                ema_20 = talib.EMA(close_values, timeperiod=20)
                ema_50 = talib.EMA(close_values, timeperiod=50) if len(df) >= 50 else ema_20
                
                # Check for NaN values
                if np.isnan(ema_20[-1]) or np.isnan(ema_50[-1]):
                    logger.warning(f"NaN in EMA calculation for {symbol} on {name}")
                    mtf_analysis[name] = {'trend': 'unknown', 'strength': 0}
                    continue

                current_price = df['close'].iloc[-1]
                current_ema_20 = ema_20[-1]
                current_ema_50 = ema_50[-1]
                
                # NEW: Identify key levels from higher timeframes
                if name in ['1H', '4H']:
                    high_50 = df['high'].rolling(50).max().iloc[-1] if len(df) >= 50 else df['high'].max()
                    low_50 = df['low'].rolling(50).min().iloc[-1] if len(df) >= 50 else df['low'].min()
                    key_levels['resistance'].append(high_50)
                    key_levels['support'].append(low_50)

                # Determine trend with improved strength calculation
                if current_price > current_ema_20 > current_ema_50:
                    trend = 'bullish'
                    # Fixed: More realistic strength calculation (0-100 scale)
                    strength = min(100, abs((current_price - current_ema_50) / current_ema_50) * 10000)
                    weighted_scores['bullish'] += weight
                elif current_price < current_ema_20 < current_ema_50:
                    trend = 'bearish'
                    strength = min(100, abs((current_ema_50 - current_price) / current_ema_50) * 10000)
                    weighted_scores['bearish'] += weight
                else:
                    trend = 'neutral'
                    strength = 0
                    weighted_scores['neutral'] += weight

                mtf_analysis[name] = {
                    'trend': trend,
                    'strength': round(strength, 2)
                }
                
            except Exception as e:
                logger.error(f"Error analyzing {name} timeframe for {symbol}: {e}")
                mtf_analysis[name] = {'trend': 'unknown', 'strength': 0}

        # FIXED: Better trend alignment logic
        total_weight = sum(weight for _, weight in timeframes.values())
        
        # Require higher timeframe agreement (70% threshold)
        if weighted_scores['bullish'] > total_weight * 0.7:
            overall_trend = 'strong_bullish'
        elif weighted_scores['bearish'] > total_weight * 0.7:
            overall_trend = 'strong_bearish'
        elif weighted_scores['bullish'] > total_weight * 0.5:
            overall_trend = 'bullish'
        elif weighted_scores['bearish'] > total_weight * 0.5:
            overall_trend = 'bearish'
        elif abs(weighted_scores['bullish'] - weighted_scores['bearish']) < total_weight * 0.2:
            overall_trend = 'conflicting'
        else:
            overall_trend = 'neutral'

        mtf_analysis['overall'] = overall_trend
        mtf_analysis['weighted_scores'] = weighted_scores
        mtf_analysis['key_levels'] = key_levels  # NEW: Include key levels

        return mtf_analysis

    # ------------------------------------------------------------------ #
    #  CORRELATION ANALYSIS (IMPROVED)
    # ------------------------------------------------------------------ #

    def calculate_correlation_matrix(self, symbols: List[str], bars: int = 100) -> Dict:
        """
        Calculate correlation between all pairs with time decay and memory efficiency.
        """
        logger.info("🔗 Calculating pair correlations...")

        # Check if we have recent correlations (within last 24 hours)
        if (self.correlation_timestamp and 
            (datetime.now() - self.correlation_timestamp).total_seconds() < MAX_CORRELATION_AGE_HOURS * 3600):
            logger.info("   ✓ Using cached correlations (still fresh)")
            return {
                'matrix': self.correlation_matrix,
                'groups': self._find_correlated_groups()
            }

        price_data = {}
        for symbol in symbols:
            try:
                df = self.get_price_data(symbol, bars=bars)
                if df is not None and len(df) >= bars:
                    price_data[symbol] = df['close'].values
            except Exception as e:
                logger.error(f"Error getting data for {symbol} in correlation calc: {e}")
                continue

        if len(price_data) < 2:
            logger.warning("Insufficient data for correlation analysis")
            return {'matrix': {}, 'groups': []}

        # Calculate correlations with error handling
        correlation_matrix = {}
        for sym1 in price_data:
            correlation_matrix[sym1] = {}
            for sym2 in price_data:
                if sym1 == sym2:
                    correlation_matrix[sym1][sym2] = 1.0
                else:
                    try:
                        # Ensure equal length arrays
                        min_len = min(len(price_data[sym1]), len(price_data[sym2]))
                        corr = np.corrcoef(
                            price_data[sym1][-min_len:], 
                            price_data[sym2][-min_len:]
                        )[0, 1]
                        
                        # Check for NaN
                        if np.isnan(corr):
                            corr = 0.0
                            
                        correlation_matrix[sym1][sym2] = round(corr, 3)
                    except Exception as e:
                        logger.error(f"Error calculating correlation {sym1}-{sym2}: {e}")
                        correlation_matrix[sym1][sym2] = 0.0

        self.correlation_matrix = correlation_matrix
        self.correlation_timestamp = datetime.now()

        # Find highly correlated groups
        correlated_groups = self._find_correlated_groups()

        logger.info(f"   ✓ Found {len(correlated_groups)} correlated groups")
        for i, group in enumerate(correlated_groups[:5], 1):
            logger.info(f"      Group {i}: {', '.join(group[:4])}")

        return {
            'matrix': correlation_matrix,
            'groups': correlated_groups
        }

    def _find_correlated_groups(self) -> List[List[str]]:
        """Helper method to find correlated groups from matrix"""
        if not self.correlation_matrix:
            return []
            
        correlated_groups = []
        processed = set()

        for sym1 in self.correlation_matrix:
            if sym1 in processed:
                continue

            group = [sym1]
            for sym2 in self.correlation_matrix[sym1]:
                if sym2 != sym1 and abs(self.correlation_matrix[sym1][sym2]) > Thresholds.CORRELATION_HIGH:
                    group.append(sym2)
                    processed.add(sym2)

            if len(group) > 1:
                correlated_groups.append(group)
                processed.add(sym1)

        return correlated_groups

    def get_correlated_pairs(self, symbol: str, threshold: float = None) -> List[str]:
        """Get list of pairs highly correlated with given symbol."""
        if threshold is None:
            threshold = Thresholds.CORRELATION_HIGH
            
        if not self.correlation_matrix or symbol not in self.correlation_matrix:
            return []

        correlated = []
        for other_symbol, corr in self.correlation_matrix[symbol].items():
            if other_symbol != symbol and abs(corr) > threshold:
                correlated.append(other_symbol)

        return correlated
    
    def check_correlation_risk(self, symbol: str, signal: str) -> Dict:
        """
        NEW: Check if opening this position would violate correlation limits
        Returns risk assessment and correlated positions
        """
        if not self.active_positions:
            return {'allowed': True, 'risk': 'none', 'correlated_count': 0}
        
        correlated_pairs = self.get_correlated_pairs(symbol)
        
        # Count active positions in correlated pairs with same direction
        same_direction_count = 0
        correlated_positions = []
        
        for active_symbol, position_info in self.active_positions.items():
            if active_symbol in correlated_pairs:
                if position_info.get('direction') == signal:
                    same_direction_count += 1
                    correlated_positions.append(active_symbol)
        
        # Check if we'd exceed correlation limit
        if same_direction_count >= Thresholds.MAX_CORRELATED_POSITIONS:
            return {
                'allowed': False,
                'risk': 'high_correlation',
                'correlated_count': same_direction_count,
                'correlated_positions': correlated_positions,
                'reason': f'Already have {same_direction_count} {signal} positions in correlated pairs'
            }
        
        return {
            'allowed': True,
            'risk': 'low' if same_direction_count == 0 else 'medium',
            'correlated_count': same_direction_count,
            'correlated_positions': correlated_positions
        }
    
    def calculate_stop_loss_take_profit(self, symbol: str, signal: str, df: pd.DataFrame, 
                                       strategy: str, spread_info: Dict) -> Dict:
        """
        NEW: Calculate stop loss and take profit levels based on strategy and market conditions
        Returns entry, SL, TP levels with risk/reward ratio
        """
        try:
            if df is None or len(df) < 20:
                return {'valid': False, 'reason': 'insufficient_data'}
            
            current_price = df['close'].iloc[-1]
            
            # Calculate ATR for volatility-based stops
            atr = talib.ATR(df['high'].values, df['low'].values, df['close'].values, timeperiod=14)
            atr = atr[~np.isnan(atr)]
            if len(atr) == 0:
                return {'valid': False, 'reason': 'no_atr'}
            
            current_atr = atr[-1]
            
            # Get spread
            spread_pct = spread_info.get('spread_pct', 0.0002) if spread_info.get('valid') else 0.0002
            spread_points = current_price * spread_pct
            
            # Strategy-specific SL/TP multipliers - RELAXED for 5-min charts
            strategy_params = {
                'scalp': {'sl_atr': 1.0, 'tp_atr': 1.5, 'min_rr': 1.2},  # Was 1.5, now 1.2
                'momentum': {'sl_atr': 1.5, 'tp_atr': 3.0, 'min_rr': 1.5},  # Was 2.0, now 1.5
                'reversal': {'sl_atr': 1.2, 'tp_atr': 2.5, 'min_rr': 1.5},  # Was 2.0, now 1.5
                'breakout': {'sl_atr': 1.5, 'tp_atr': 3.5, 'min_rr': 1.5},  # Was 2.0, now 1.5
                'range': {'sl_atr': 1.0, 'tp_atr': 2.0, 'min_rr': 1.5},  # Was 2.0, now 1.5
                'sr_bounce': {'sl_atr': 1.2, 'tp_atr': 2.5, 'min_rr': 1.5}  # Was 2.0, now 1.5
            }
            
            params = strategy_params.get(strategy, {'sl_atr': 1.5, 'tp_atr': 2.5, 'min_rr': 1.5})
            
            # Calculate base SL/TP distances
            sl_distance = current_atr * params['sl_atr']
            tp_distance = current_atr * params['tp_atr']
            
            # Adjust for spread (especially important for scalping)
            if strategy == 'scalp':
                # Ensure TP is at least 3x spread
                min_tp_distance = spread_points * Thresholds.SCALP_MIN_PROFIT_SPREAD_RATIO
                if tp_distance < min_tp_distance:
                    tp_distance = min_tp_distance
                    # Recalculate SL to maintain risk/reward
                    sl_distance = tp_distance / params['min_rr']
            
            # Calculate actual levels
            if signal == 'BUY':
                entry = current_price + spread_points  # Account for spread on entry
                stop_loss = entry - sl_distance
                take_profit = entry + tp_distance
            else:  # SELL
                entry = current_price - spread_points
                stop_loss = entry + sl_distance
                take_profit = entry - tp_distance
            
            # Calculate risk/reward ratio
            risk = abs(entry - stop_loss)
            reward = abs(take_profit - entry)
            
            if risk == 0:
                return {'valid': False, 'reason': 'zero_risk'}
            
            risk_reward = reward / risk
            
            # Check minimum risk/reward
            if risk_reward < params['min_rr']:
                return {
                    'valid': False,
                    'reason': f'poor_risk_reward',
                    'risk_reward': round(risk_reward, 2),
                    'min_required': params['min_rr']
                }
            
            # Use higher timeframe levels for better SL/TP placement
            mtf = self.last_analysis.get(symbol, {}).get('mtf', {})
            key_levels = mtf.get('key_levels', {'support': [], 'resistance': []})
            
            # Adjust SL/TP to respect key levels
            if signal == 'BUY' and key_levels['support']:
                nearest_support = max([s for s in key_levels['support'] if s < entry], default=None)
                if nearest_support and nearest_support > stop_loss:
                    # Place SL below support
                    stop_loss = nearest_support - (current_atr * 0.2)
                    # Recalculate risk/reward
                    risk = abs(entry - stop_loss)
                    risk_reward = reward / risk if risk > 0 else 0
            
            elif signal == 'SELL' and key_levels['resistance']:
                nearest_resistance = min([r for r in key_levels['resistance'] if r > entry], default=None)
                if nearest_resistance and nearest_resistance < stop_loss:
                    # Place SL above resistance
                    stop_loss = nearest_resistance + (current_atr * 0.2)
                    risk = abs(entry - stop_loss)
                    risk_reward = reward / risk if risk > 0 else 0
            
            # Calculate position size based on risk (assuming 1% risk per trade)
            # This is informational - actual position sizing done by executor
            risk_pips = abs(entry - stop_loss) / current_price * 10000
            
            return {
                'valid': True,
                'entry': round(entry, 5),
                'stop_loss': round(stop_loss, 5),
                'take_profit': round(take_profit, 5),
                'risk_reward': round(risk_reward, 2),
                'risk_pips': round(risk_pips, 1),
                'reward_pips': round(risk_pips * risk_reward, 1),
                'atr': round(current_atr, 5),
                'spread_cost': round(spread_points, 5),
                'strategy': strategy
            }
            
        except Exception as e:
            logger.error(f"Error calculating SL/TP for {symbol}: {e}")
            return {'valid': False, 'reason': f'error: {str(e)}'}
    
    def check_daily_loss_limit(self, potential_loss: float = 0) -> Dict:
        """
        NEW: Check if daily loss limit would be exceeded
        """
        # Reset daily loss if new day
        today = datetime.now().date()
        if today != self.daily_loss_reset_date:
            self.daily_loss = 0.0
            self.daily_loss_reset_date = today
        
        # Check if adding potential loss would exceed limit
        total_loss = self.daily_loss + potential_loss
        
        if total_loss >= Thresholds.MAX_DAILY_LOSS_PCT:
            return {
                'allowed': False,
                'current_loss': round(self.daily_loss, 2),
                'limit': Thresholds.MAX_DAILY_LOSS_PCT,
                'reason': f'Daily loss limit reached: {self.daily_loss:.2f}%'
            }
        
        return {
            'allowed': True,
            'current_loss': round(self.daily_loss, 2),
            'remaining': round(Thresholds.MAX_DAILY_LOSS_PCT - total_loss, 2)
        }
    
    def record_trade_result(self, symbol: str, strategy: str, profit_pct: float):
        """
        NEW: Record trade result and update daily loss tracking
        """
        # Update daily loss
        if profit_pct < 0:
            self.daily_loss += abs(profit_pct)
        
        # Update strategy performance (existing method)
        profit_pips = profit_pct * 100  # Convert to pips for existing method
        self.record_strategy_result(symbol, strategy, profit_pips)
    
    def detect_volatility_regime(self, symbol: str, df: pd.DataFrame) -> Dict:
        """
        NEW: Detect volatility regime (expanding, contracting, stable)
        """
        try:
            if df is None or len(df) < 30:
                return {'regime': 'unknown', 'direction': 'unknown'}
            
            atr = talib.ATR(df['high'].values, df['low'].values, df['close'].values, timeperiod=14)
            atr = atr[~np.isnan(atr)]
            
            if len(atr) < 20:
                return {'regime': 'unknown', 'direction': 'unknown'}
            
            current_atr = atr[-1]
            recent_atr = np.mean(atr[-5:])
            medium_atr = np.mean(atr[-10:])
            long_atr = np.mean(atr[-20:])
            
            # Detect regime
            if recent_atr > medium_atr * 1.2 and medium_atr > long_atr * 1.1:
                regime = 'expanding'
                direction = 'accelerating'
            elif recent_atr < medium_atr * 0.8 and medium_atr < long_atr * 0.9:
                regime = 'contracting'
                direction = 'decelerating'
            elif recent_atr > long_atr * 1.3:
                regime = 'high'
                direction = 'volatile'
            elif recent_atr < long_atr * 0.7:
                regime = 'low'
                direction = 'quiet'
            else:
                regime = 'stable'
                direction = 'normal'
            
            # Store regime
            self.volatility_regime[symbol] = {
                'regime': regime,
                'direction': direction,
                'current_atr': current_atr,
                'avg_atr': long_atr,
                'timestamp': datetime.now()
            }
            
            return {
                'regime': regime,
                'direction': direction,
                'current_atr': round(current_atr, 5),
                'avg_atr': round(long_atr, 5),
                'ratio': round(current_atr / long_atr, 2) if long_atr > 0 else 1.0
            }
            
        except Exception as e:
            logger.error(f"Error detecting volatility regime for {symbol}: {e}")
            return {'regime': 'unknown', 'direction': 'unknown'}

    # ------------------------------------------------------------------ #
    #  PATTERN RECOGNITION (IMPROVED)
    # ------------------------------------------------------------------ #

    def detect_chart_patterns(self, df: pd.DataFrame) -> Dict:
        """
        Detect common chart patterns with volume confirmation
        FIXED: Dynamic confidence based on pattern quality
        """
        if df is None or len(df) < 50:
            return {'patterns': [], 'signal': 'NONE', 'pattern_count': 0}

        try:
            patterns = []
            highs = df['high'].values[-50:]
            lows = df['low'].values[-50:]
            closes = df['close'].values[-50:]
            volumes = df['tick_volume'].values[-50:] if 'tick_volume' in df.columns else None

            # Pattern detection with volume confirmation
            avg_volume = np.mean(volumes) if volumes is not None else 0

            # Pattern 1: Head and Shoulders
            hs_result = self._detect_head_shoulders(highs, lows)
            if hs_result['detected']:
                volume_confirmed = volumes is not None and np.mean(volumes[-5:]) > avg_volume * 1.2
                # FIXED: Dynamic confidence based on pattern quality
                base_confidence = 0.70
                if volume_confirmed:
                    base_confidence += 0.10
                if hs_result.get('symmetry', 0) > 0.95:  # Very symmetric shoulders
                    base_confidence += 0.05
                patterns.append({
                    'name': 'head_shoulders',
                    'type': 'bearish_reversal',
                    'confidence': min(0.90, base_confidence),
                    'volume_confirmed': volume_confirmed,
                    'quality': hs_result.get('symmetry', 0)
                })

            # Pattern 2: Inverse Head and Shoulders
            ihs_result = self._detect_inverse_head_shoulders(highs, lows)
            if ihs_result['detected']:
                volume_confirmed = volumes is not None and np.mean(volumes[-5:]) > avg_volume * 1.2
                base_confidence = 0.70
                if volume_confirmed:
                    base_confidence += 0.10
                if ihs_result.get('symmetry', 0) > 0.95:
                    base_confidence += 0.05
                patterns.append({
                    'name': 'inverse_head_shoulders',
                    'type': 'bullish_reversal',
                    'confidence': min(0.90, base_confidence),
                    'volume_confirmed': volume_confirmed,
                    'quality': ihs_result.get('symmetry', 0)
                })

            # Pattern 3: Ascending Triangle
            at_result = self._detect_ascending_triangle(highs, lows)
            if at_result['detected']:
                volume_confirmed = volumes is not None and np.mean(volumes[-5:]) > avg_volume * 1.3
                base_confidence = 0.65
                if volume_confirmed:
                    base_confidence += 0.10
                patterns.append({
                    'name': 'ascending_triangle',
                    'type': 'bullish_breakout',
                    'confidence': min(0.85, base_confidence),
                    'volume_confirmed': volume_confirmed
                })

            # Pattern 4: Descending Triangle
            dt_result = self._detect_descending_triangle(highs, lows)
            if dt_result['detected']:
                volume_confirmed = volumes is not None and np.mean(volumes[-5:]) > avg_volume * 1.3
                base_confidence = 0.65
                if volume_confirmed:
                    base_confidence += 0.10
                patterns.append({
                    'name': 'descending_triangle',
                    'type': 'bearish_breakout',
                    'confidence': min(0.85, base_confidence),
                    'volume_confirmed': volume_confirmed
                })

            # Pattern 5: Bull Flag
            bf_result = self._detect_bull_flag(closes, volumes)
            if bf_result['detected']:
                volume_confirmed = bf_result.get('volume_confirmed', False)
                base_confidence = 0.60
                if volume_confirmed:
                    base_confidence += 0.10
                patterns.append({
                    'name': 'bull_flag',
                    'type': 'bullish_continuation',
                    'confidence': min(0.80, base_confidence),
                    'volume_confirmed': volume_confirmed
                })

            # Pattern 6: Bear Flag
            bearf_result = self._detect_bear_flag(closes, volumes)
            if bearf_result['detected']:
                volume_confirmed = bearf_result.get('volume_confirmed', False)
                base_confidence = 0.60
                if volume_confirmed:
                    base_confidence += 0.10
                patterns.append({
                    'name': 'bear_flag',
                    'type': 'bearish_continuation',
                    'confidence': min(0.80, base_confidence),
                    'volume_confirmed': volume_confirmed
                })

            # Determine overall signal from patterns (weighted by confidence)
            signal = 'NONE'
            if patterns:
                bullish_score = sum(p['confidence'] for p in patterns if 'bullish' in p['type'])
                bearish_score = sum(p['confidence'] for p in patterns if 'bearish' in p['type'])

                if bullish_score > bearish_score * 1.2:
                    signal = 'BUY'
                elif bearish_score > bullish_score * 1.2:
                    signal = 'SELL'

            return {
                'patterns': patterns,
                'signal': signal,
                'pattern_count': len(patterns),
                'bullish_score': sum(p['confidence'] for p in patterns if 'bullish' in p['type']),
                'bearish_score': sum(p['confidence'] for p in patterns if 'bearish' in p['type'])
            }
            
        except Exception as e:
            logger.error(f"Error in pattern detection: {e}")
            return {'patterns': [], 'signal': 'NONE', 'pattern_count': 0}

    def _detect_head_shoulders(self, highs: np.ndarray, lows: np.ndarray) -> Dict:
        """Detect head and shoulders pattern (bearish reversal) with improved logic
        FIXED: Returns quality metrics"""
        if len(highs) < 30:
            return {'detected': False}

        try:
            peaks = []
            for i in range(5, len(highs) - 5):
                if highs[i] > highs[i-5:i].max() and highs[i] > highs[i+1:i+6].max():
                    peaks.append((i, highs[i]))

            if len(peaks) < 3:
                return {'detected': False}

            # Check last 3 peaks for pattern
            for i in range(len(peaks) - 2):
                left_shoulder = peaks[i][1]
                head = peaks[i+1][1]
                right_shoulder = peaks[i+2][1]

                # Head should be significantly higher
                if head > left_shoulder * 1.015 and head > right_shoulder * 1.015:
                    # Shoulders should be roughly equal (within 2%)
                    shoulder_diff = abs(left_shoulder - right_shoulder) / left_shoulder
                    if shoulder_diff < 0.02:
                        symmetry = 1.0 - shoulder_diff  # 0.98-1.0 range
                        return {'detected': True, 'symmetry': symmetry}

            return {'detected': False}
        except Exception as e:
            logger.error(f"Error in head_shoulders detection: {e}")
            return {'detected': False}

    def _detect_inverse_head_shoulders(self, highs: np.ndarray, lows: np.ndarray) -> Dict:
        """Detect inverse head and shoulders pattern (bullish reversal) with improved logic
        FIXED: Returns quality metrics"""
        if len(lows) < 30:
            return {'detected': False}

        try:
            troughs = []
            for i in range(5, len(lows) - 5):
                if lows[i] < lows[i-5:i].min() and lows[i] < lows[i+1:i+6].min():
                    troughs.append((i, lows[i]))

            if len(troughs) < 3:
                return {'detected': False}

            for i in range(len(troughs) - 2):
                left_shoulder = troughs[i][1]
                head = troughs[i+1][1]
                right_shoulder = troughs[i+2][1]

                # Head should be significantly lower
                if head < left_shoulder * 0.985 and head < right_shoulder * 0.985:
                    # Shoulders should be roughly equal (within 2%)
                    shoulder_diff = abs(left_shoulder - right_shoulder) / left_shoulder
                    if shoulder_diff < 0.02:
                        symmetry = 1.0 - shoulder_diff
                        return {'detected': True, 'symmetry': symmetry}

            return {'detected': False}
        except Exception as e:
            logger.error(f"Error in inverse_head_shoulders detection: {e}")
            return {'detected': False}

    def _detect_ascending_triangle(self, highs: np.ndarray, lows: np.ndarray) -> Dict:
        """Detect ascending triangle (bullish breakout pattern) with improved validation
        FIXED: Returns detection result"""
        if len(highs) < 20:
            return {'detected': False}

        try:
            recent_highs = highs[-20:]
            recent_lows = lows[-20:]

            # Check if highs are relatively flat (resistance level)
            high_variance = np.std(recent_highs[-10:]) / np.mean(recent_highs[-10:])
            
            # Check if lows are trending up (higher lows)
            lows_trend = np.polyfit(range(len(recent_lows)), recent_lows, 1)[0]

            # Ascending triangle: flat resistance + rising support
            if high_variance < 0.008 and lows_trend > 0:
                return {'detected': True}

            return {'detected': False}
        except Exception as e:
            logger.error(f"Error in ascending_triangle detection: {e}")
            return {'detected': False}

    def _detect_descending_triangle(self, highs: np.ndarray, lows: np.ndarray) -> Dict:
        """Detect descending triangle (bearish breakout pattern) with improved validation
        FIXED: Returns detection result"""
        if len(lows) < 20:
            return {'detected': False}

        try:
            recent_highs = highs[-20:]
            recent_lows = lows[-20:]

            # Check if lows are relatively flat (support level)
            low_variance = np.std(recent_lows[-10:]) / np.mean(recent_lows[-10:])
            
            # Check if highs are trending down (lower highs)
            highs_trend = np.polyfit(range(len(recent_highs)), recent_highs, 1)[0]

            # Descending triangle: flat support + falling resistance
            if low_variance < 0.008 and highs_trend < 0:
                return {'detected': True}

            return {'detected': False}
        except Exception as e:
            logger.error(f"Error in descending_triangle detection: {e}")
            return {'detected': False}

    def _detect_bull_flag(self, closes: np.ndarray, volumes: np.ndarray = None) -> Dict:
        """Detect bull flag pattern (bullish continuation) with improved validation
        FIXED: Volume confirmation"""
        if len(closes) < 30:
            return {'detected': False}

        try:
            pole = closes[-30:-10]
            flag = closes[-10:]

            # Pole should be strongly upward
            pole_trend = np.polyfit(range(len(pole)), pole, 1)[0]
            # Flag should be slightly downward or flat (consolidation)
            flag_trend = np.polyfit(range(len(flag)), flag, 1)[0]

            # Bull flag: strong up move followed by slight pullback/consolidation
            if pole_trend > 0 and flag_trend <= 0 and abs(flag_trend) < abs(pole_trend) * 0.25:
                # Check volume if available
                volume_confirmed = False
                if volumes is not None and len(volumes) >= 30:
                    pole_vol = np.mean(volumes[-30:-10])
                    flag_vol = np.mean(volumes[-10:])
                    # Volume should decrease during flag (consolidation)
                    if flag_vol < pole_vol * 0.8:
                        volume_confirmed = True
                
                return {'detected': True, 'volume_confirmed': volume_confirmed}

            return {'detected': False}
        except Exception as e:
            logger.error(f"Error in bull_flag detection: {e}")
            return {'detected': False}

    def _detect_bear_flag(self, closes: np.ndarray, volumes: np.ndarray = None) -> Dict:
        """Detect bear flag pattern (bearish continuation) with improved validation
        FIXED: Volume confirmation"""
        if len(closes) < 30:
            return {'detected': False}

        try:
            pole = closes[-30:-10]
            flag = closes[-10:]

            # Pole should be strongly downward
            pole_trend = np.polyfit(range(len(pole)), pole, 1)[0]
            # Flag should be slightly upward or flat (consolidation)
            flag_trend = np.polyfit(range(len(flag)), flag, 1)[0]

            # Bear flag: strong down move followed by slight bounce/consolidation
            if pole_trend < 0 and flag_trend >= 0 and abs(flag_trend) < abs(pole_trend) * 0.25:
                # Check volume if available
                volume_confirmed = False
                if volumes is not None and len(volumes) >= 30:
                    pole_vol = np.mean(volumes[-30:-10])
                    flag_vol = np.mean(volumes[-10:])
                    # Volume should decrease during flag (consolidation)
                    if flag_vol < pole_vol * 0.8:
                        volume_confirmed = True
                
                return {'detected': True, 'volume_confirmed': volume_confirmed}

            return {'detected': False}
        except Exception as e:
            logger.error(f"Error in bear_flag detection: {e}")
            return {'detected': False}

    # ------------------------------------------------------------------ #
    #  ADAPTIVE STRATEGY SELECTION (IMPROVED)
    # ------------------------------------------------------------------ #

    def record_strategy_result(self, symbol: str, strategy: str, profit_pips: float):
        """Record the result of a strategy with time decay and limited history.
        FIXED: Better cooldown logic with partial recovery"""
        try:
            if symbol not in self.strategy_performance:
                self.strategy_performance[symbol] = {}

            if strategy not in self.strategy_performance[symbol]:
                self.strategy_performance[symbol][strategy] = {
                    'wins': 0,
                    'losses': 0,
                    'total_pips': 0,
                    'win_rate': 0,
                    'consecutive_losses': 0,
                    'consecutive_wins': 0,  # NEW: Track wins too
                    'last_trade_time': None
                }

            perf = self.strategy_performance[symbol][strategy]

            if profit_pips > 0:
                perf['wins'] += 1
                perf['consecutive_losses'] = 0
                perf['consecutive_wins'] += 1  # NEW
                
                # FIXED: Remove cooldown after 2 wins
                if perf['consecutive_wins'] >= 2:
                    cooldown_key = f"{symbol}_{strategy}"
                    if cooldown_key in self.strategy_cooldowns:
                        del self.strategy_cooldowns[cooldown_key]
                        logger.info(f"Strategy {strategy} for {symbol} cooldown removed after recovery")
            else:
                perf['losses'] += 1
                perf['consecutive_losses'] += 1
                perf['consecutive_wins'] = 0  # NEW

            perf['total_pips'] += profit_pips
            perf['last_trade_time'] = datetime.now()

            total_trades = perf['wins'] + perf['losses']
            perf['win_rate'] = (perf['wins'] / total_trades * 100) if total_trades > 0 else 0

            # Apply cooldown if consecutive losses
            if perf['consecutive_losses'] >= 3:
                cooldown_key = f"{symbol}_{strategy}"
                self.strategy_cooldowns[cooldown_key] = datetime.now()
                logger.warning(f"Strategy {strategy} for {symbol} on cooldown after {perf['consecutive_losses']} losses")

            # Memory management - keep only top 50 symbols by trade count
            if len(self.strategy_performance) > 50:
                symbol_trades = {}
                for sym, strategies in self.strategy_performance.items():
                    total = sum(s['wins'] + s['losses'] for s in strategies.values())
                    symbol_trades[sym] = total

                top_symbols = sorted(symbol_trades.items(), key=lambda x: x[1], reverse=True)[:50]
                top_symbol_names = {sym for sym, _ in top_symbols}

                symbols_to_remove = [sym for sym in self.strategy_performance if sym not in top_symbol_names]
                for sym in symbols_to_remove:
                    del self.strategy_performance[sym]
                    logger.debug(f"Removed performance data for {sym} (memory management)")
                    
        except Exception as e:
            logger.error(f"Error recording strategy result for {symbol}/{strategy}: {e}")

    def is_strategy_on_cooldown(self, symbol: str, strategy: str) -> bool:
        """Check if a strategy is on cooldown for a symbol."""
        cooldown_key = f"{symbol}_{strategy}"
        if cooldown_key not in self.strategy_cooldowns:
            return False
            
        cooldown_time = self.strategy_cooldowns[cooldown_key]
        elapsed_minutes = (datetime.now() - cooldown_time).total_seconds() / 60
        
        if elapsed_minutes >= STRATEGY_COOLDOWN_MINUTES:
            del self.strategy_cooldowns[cooldown_key]
            logger.info(f"Strategy {strategy} for {symbol} cooldown expired")
            return False
            
        return True

    def get_best_strategy_for_pair(self, symbol: str, min_trades: int = 5) -> Optional[str]:
        """Get the best performing strategy for a specific pair with cooldown check."""
        if symbol not in self.strategy_performance:
            return None

        strategies = self.strategy_performance[symbol]

        # Filter out strategies on cooldown and with insufficient trades
        valid_strategies = {}
        for strat, perf in strategies.items():
            if self.is_strategy_on_cooldown(symbol, strat):
                continue
            if (perf['wins'] + perf['losses']) >= min_trades:
                valid_strategies[strat] = perf

        if not valid_strategies:
            return None

        # Sort by win rate, then by total pips
        best_strategy = max(
            valid_strategies.items(),
            key=lambda x: (x[1]['win_rate'], x[1]['total_pips'])
        )
        
        return best_strategy[0]

    def get_worst_strategy_for_pair(self, symbol: str, min_trades: int = 5) -> Optional[str]:
        """Get the worst performing strategy for a specific pair."""
        if symbol not in self.strategy_performance:
            return None

        strategies = self.strategy_performance[symbol]

        valid_strategies = {
            strat: perf for strat, perf in strategies.items()
            if (perf['wins'] + perf['losses']) >= min_trades
        }

        if not valid_strategies:
            return None

        worst_strategy = min(valid_strategies.items(), key=lambda x: x[1]['win_rate'])

        # Only return if truly bad (< 40% win rate)
        if worst_strategy[1]['win_rate'] < 40:
            return worst_strategy[0]

        return None

    def print_strategy_performance(self):
        """Print strategy performance report with enhanced metrics"""
        if not self.strategy_performance:
            logger.info("\n📊 No strategy performance data yet")
            return

        print("\n" + "="*80)
        print("📊 ADAPTIVE STRATEGY PERFORMANCE")
        print("="*80)

        for symbol in sorted(self.strategy_performance.keys())[:10]:
            strategies = self.strategy_performance[symbol]
            print(f"\n{symbol}:")

            for strategy, perf in sorted(strategies.items(), key=lambda x: x[1]['win_rate'], reverse=True):
                total = perf['wins'] + perf['losses']
                if total >= 3:
                    cooldown_status = " [COOLDOWN]" if self.is_strategy_on_cooldown(symbol, strategy) else ""
                    consecutive = f" (L{perf['consecutive_losses']})" if perf['consecutive_losses'] > 0 else ""
                    
                    print(f"   {strategy:12} | Win Rate: {perf['win_rate']:5.1f}% | "
                          f"Trades: {total:3} | Pips: {perf['total_pips']:+7.1f}{consecutive}{cooldown_status}")

    # ------------------------------------------------------------------ #
    #  DEEP SCAN METHODS (IMPROVED)
    # ------------------------------------------------------------------ #

    def analyze_volume_profile(self, df: pd.DataFrame) -> Dict:
        """Analyze volume with comprehensive validation and error handling"""
        if df is None or len(df) < 20:
            return {'valid': False, 'reason': 'insufficient_data'}

        try:
            vol = df['tick_volume'].values if 'tick_volume' in df.columns else df.get('real_volume', pd.Series()).values
            if len(vol) == 0:
                return {'valid': False, 'reason': 'no_volume_data'}
                
            closes = df['close'].values
            opens = df['open'].values

            # Calculate buyer/seller volume
            buyer_vol = sum(vol[i] for i in range(len(vol)) if closes[i] >= opens[i])
            seller_vol = sum(vol[i] for i in range(len(vol)) if closes[i] < opens[i])
            total_vol = buyer_vol + seller_vol

            if total_vol == 0:
                return {'valid': False, 'reason': 'zero_volume'}

            # Volume trend analysis
            recent_avg_vol = np.mean(vol[-10:])
            older_avg_vol = np.mean(vol[-20:-10])
            
            if older_avg_vol == 0:
                vol_trend = 'unknown'
            elif recent_avg_vol > older_avg_vol * 1.1:
                vol_trend = 'increasing'
            elif recent_avg_vol < older_avg_vol * 0.9:
                vol_trend = 'decreasing'
            else:
                vol_trend = 'stable'

            avg_vol = np.mean(vol)
            spike_count = sum(1 for v in vol[-20:] if v > avg_vol * 2)

            # Detect volume drying up (weak moves)
            recent_5_vol = np.mean(vol[-5:])
            is_volume_drying = recent_5_vol < avg_vol * 0.4 if avg_vol > 0 else False

            return {
                'valid': True,
                'buyer_pct': round(buyer_vol / total_vol * 100, 1),
                'seller_pct': round(seller_vol / total_vol * 100, 1),
                'dominance': 'buyers' if buyer_vol > seller_vol * 1.15 else (
                    'sellers' if seller_vol > buyer_vol * 1.15 else 'neutral'
                ),
                'avg_volume': round(avg_vol, 0),
                'vol_trend': vol_trend,
                'spike_count': spike_count,
                'is_liquid': avg_vol > 50,
                'is_volume_drying': is_volume_drying
            }
            
        except Exception as e:
            logger.error(f"Error in volume profile analysis: {e}")
            return {'valid': False, 'reason': f'error: {str(e)}'}

    def analyze_spread_health(self, symbol: str) -> Dict:
        """Check spread conditions with retry logic and validation"""
        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                tick = mt5.symbol_info_tick(symbol)
                info = mt5.symbol_info(symbol)

                if tick is None or info is None:
                    if attempt < MAX_RETRY_ATTEMPTS - 1:
                        time.sleep(RETRY_DELAY_SECONDS)
                        continue
                    return {'valid': False, 'reason': 'no_tick_data'}

                spread_points = info.spread
                point = info.point
                ask = tick.ask
                bid = tick.bid

                if bid == 0:
                    return {'valid': False, 'reason': 'zero_bid'}

                spread_pct = (ask - bid) / bid * 100

                # Use configurable thresholds
                if spread_pct > Thresholds.SPREAD_HIGH_PCT:
                    spread_risk = 'high'
                elif spread_pct > Thresholds.SPREAD_MEDIUM_PCT:
                    spread_risk = 'medium'
                else:
                    spread_risk = 'low'

                return {
                    'valid': True,
                    'spread_points': spread_points,
                    'spread_pct': round(spread_pct, 4),
                    'spread_risk': spread_risk,
                    'bid': bid,
                    'ask': ask
                }
                
            except Exception as e:
                logger.error(f"Error analyzing spread for {symbol}: {e}")
                if attempt < MAX_RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue
                return {'valid': False, 'reason': f'error: {str(e)}'}
        
        return {'valid': False, 'reason': 'max_retries_exceeded'}

    def analyze_volatility_risk(self, df: pd.DataFrame) -> Dict:
        """Assess volatility and risk level with comprehensive validation"""
        if df is None or len(df) < 30:
            return {'valid': False, 'reason': 'insufficient_data'}

        try:
            atr = talib.ATR(df['high'].values, df['low'].values, df['close'].values, timeperiod=14)
            
            # Remove NaN values
            atr = atr[~np.isnan(atr)]
            if len(atr) < 5:
                return {'valid': False, 'reason': 'insufficient_atr_data'}
                
            current_atr = atr[-1]
            avg_atr = np.mean(atr[-min(20, len(atr)):])
            price = df['close'].iloc[-1]

            if price == 0:
                return {'valid': False, 'reason': 'zero_price'}

            atr_pct = (current_atr / price * 100)

            # Use configurable thresholds
            if atr_pct > Thresholds.VOLATILITY_EXTREME_PCT:
                vol_class = 'extreme'
            elif atr_pct > Thresholds.VOLATILITY_HIGH_PCT:
                vol_class = 'high'
            elif atr_pct > Thresholds.VOLATILITY_NORMAL_PCT:
                vol_class = 'normal'
            else:
                vol_class = 'low'

            recent_atr = np.mean(atr[-min(5, len(atr)):])
            
            if avg_atr == 0:
                vol_direction = 'unknown'
            elif recent_atr > avg_atr * 1.15:
                vol_direction = 'expanding'
            elif recent_atr < avg_atr * 0.85:
                vol_direction = 'contracting'
            else:
                vol_direction = 'stable'

            return {
                'valid': True,
                'atr': round(current_atr, 5),
                'atr_pct': round(atr_pct, 3),
                'volatility': vol_class,
                'vol_direction': vol_direction
            }
            
        except Exception as e:
            logger.error(f"Error in volatility analysis: {e}")
            return {'valid': False, 'reason': f'error: {str(e)}'}

    def quick_scan_pair(self, symbol: str) -> Dict:
        """Quick scan for re-scanning during trading with improved error handling."""
        report = {
            'symbol': symbol,
            'scan_time': datetime.now(),
            'status': 'quick_scan',
            'verdict': 'APPROVED',
            'risk_score': 0,
            'recommended_strategy': None,
            'multi_timeframe': {},
            'reasons': []
        }

        try:
            # Early spread check to avoid expensive calculations
            spread = self.analyze_spread_health(symbol)
            if spread.get('valid') and spread['spread_risk'] == 'high':
                report['verdict'] = 'SKIP'
                report['reasons'].append(f"High spread ({spread.get('spread_pct', 0):.4f}%)")
                return report

            mtf = self.analyze_multi_timeframe_trends(symbol)
            report['multi_timeframe'] = mtf

            df = self.get_price_data(symbol, bars=50)
            if df is None:
                report['verdict'] = 'SKIP'
                report['reasons'].append('No price data')
                return report

            # Validate ATR calculation
            atr = talib.ATR(df['high'].values, df['low'].values, df['close'].values, timeperiod=14)
            atr = atr[~np.isnan(atr)]
            
            if len(atr) < 5:
                report['verdict'] = 'SKIP'
                report['reasons'].append('Insufficient ATR data')
                return report
                
            current_atr = atr[-1]
            avg_atr = np.mean(atr[-min(20, len(atr)):])

            # Check for abnormally low volatility
            if avg_atr > 0 and current_atr < avg_atr * 0.3:
                report['verdict'] = 'CAUTION'
                report['risk_score'] = 30
                report['reasons'].append('Very low volatility')

            strongest = self.get_strongest_strategy_for_pair(symbol)
            report['recommended_strategy'] = strongest['strategy']
            report['strategy_confidence'] = strongest['confidence']

            report['status'] = 'scanned'

        except Exception as e:
            logger.error(f"Error in quick_scan_pair for {symbol}: {e}")
            report['status'] = 'error'
            report['verdict'] = 'SKIP'
            report['reasons'].append(f'Error: {str(e)}')

        return report

    def deep_scan_pair(self, symbol: str) -> Dict:
        """Full deep scan of a single pair with comprehensive validation."""
        report = {
            'symbol': symbol,
            'scan_time': datetime.now(),
            'status': 'error',
            'risk_level': 'unknown',
            'verdict': 'SKIP',
            'recommended_strategy': None,
            'reasons': []
        }

        try:
            # Early spread check (fail fast)
            spread = self.analyze_spread_health(symbol)
            if not spread.get('valid'):
                report['reasons'].append(f"Cannot read spread: {spread.get('reason', 'unknown')}")
                return report

            df = self.get_price_data(symbol, bars=100)
            if df is None:
                report['reasons'].append('No price data available')
                return report

            # Perform all analyses
            volume = self.analyze_volume_profile(df)
            mtf = self.analyze_multi_timeframe_trends(symbol)
            volatility = self.analyze_volatility_risk(df)

            report['volume'] = volume
            report['spread'] = spread
            report['multi_timeframe'] = mtf
            report['volatility'] = volatility

            # Risk scoring (0-100, higher = riskier)
            risk_score = 0
            reasons_approve = []
            reasons_reject = []

            # Spread risk (check first - most critical)
            if spread['spread_risk'] == 'high':
                risk_score += 35
                reasons_reject.append(f"High spread ({spread['spread_pct']:.4f}%)")
            elif spread['spread_risk'] == 'medium':
                risk_score += 15
                reasons_reject.append(f"Medium spread ({spread['spread_pct']:.4f}%)")

            # Volume/liquidity risk
            if volume.get('valid'):
                if not volume['is_liquid']:
                    risk_score += 30
                    reasons_reject.append(f"Low liquidity (avg vol: {volume['avg_volume']})")
                else:
                    reasons_approve.append(f"Liquid ({volume['dominance']} dominant, vol {volume['vol_trend']})")
                    
                if volume.get('is_volume_drying'):
                    risk_score += 15
                    reasons_reject.append('Volume drying up - weak/fake moves likely')
            else:
                risk_score += 15
                reasons_reject.append(f"Volume data issue: {volume.get('reason', 'unknown')}")

            # Multi-timeframe alignment
            if mtf.get('overall') == 'conflicting':
                risk_score += 20
                reasons_reject.append('Conflicting trends across timeframes')
            elif mtf.get('overall') == 'neutral':
                risk_score += 10
                reasons_reject.append('Neutral/unclear trend')
            elif mtf.get('overall') in ['strong_bullish', 'strong_bearish']:
                reasons_approve.append(f"Strong trend alignment: {mtf.get('overall')}")

            # Volatility risk
            if volatility.get('valid'):
                if volatility['volatility'] == 'extreme':
                    risk_score += 25
                    reasons_reject.append(f"Extreme volatility (ATR {volatility['atr_pct']:.3f}%)")
                elif volatility['volatility'] == 'low':
                    risk_score += 10
                    reasons_reject.append('Very low volatility - no movement')
                elif volatility['volatility'] == 'normal':
                    reasons_approve.append('Healthy volatility')
            else:
                risk_score += 10
                reasons_reject.append(f"Volatility data issue: {volatility.get('reason', 'unknown')}")

            # Strategy recommendation (check for cooldowns)
            best_adaptive = self.get_best_strategy_for_pair(symbol)

            if best_adaptive:
                report['recommended_strategy'] = best_adaptive
                report['strategy_confidence'] = 0.8
                reasons_approve.append(f"Proven strategy: {best_adaptive}")
            else:
                strongest = self.get_strongest_strategy_for_pair(symbol)
                report['recommended_strategy'] = strongest['strategy']
                report['strategy_confidence'] = strongest['confidence']

            # Final verdict
            report['risk_score'] = risk_score
            if risk_score >= 50:
                report['risk_level'] = 'HIGH'
                report['verdict'] = 'AVOID'
                report['reasons'] = reasons_reject
            elif risk_score >= 30:
                report['risk_level'] = 'MEDIUM'
                report['verdict'] = 'CAUTION'
                report['reasons'] = reasons_reject + reasons_approve
            else:
                report['risk_level'] = 'LOW'
                report['verdict'] = 'APPROVED'
                report['reasons'] = reasons_approve

            report['status'] = 'scanned'
            
        except Exception as e:
            logger.error(f"Error in deep_scan_pair for {symbol}: {e}")
            report['status'] = 'error'
            report['verdict'] = 'SKIP'
            report['reasons'].append(f'Scan error: {str(e)}')
            
        return report

    def get_strongest_strategy_for_pair(self, symbol: str, df: pd.DataFrame = None) -> Dict:
        """Determine the best strategy for a pair based on its characteristics with validation."""
        try:
            if df is None:
                df = self.get_price_data(symbol)
            if df is None or len(df) < MIN_BARS_FOR_ANALYSIS:
                return {'strategy': 'scalp', 'confidence': 0.5, 'signal': 'NONE', 'reason': 'insufficient_data'}

            # Calculate indicators with NaN checks
            atr = talib.ATR(df['high'].values, df['low'].values, df['close'].values, timeperiod=14)
            atr = atr[~np.isnan(atr)]
            if len(atr) == 0:
                return {'strategy': 'scalp', 'confidence': 0.5, 'signal': 'NONE', 'reason': 'no_atr'}
                
            current_atr = atr[-1]
            price = df['close'].iloc[-1]
            
            if price == 0:
                return {'strategy': 'scalp', 'confidence': 0.5, 'signal': 'NONE', 'reason': 'zero_price'}
                
            atr_pct = (current_atr / price * 100)

            ema_20 = talib.EMA(df['close'].values, timeperiod=20)
            ema_50 = talib.EMA(df['close'].values, timeperiod=50)
            
            # Check for NaN
            if np.isnan(ema_20[-1]) or np.isnan(ema_50[-1]):
                return {'strategy': 'scalp', 'confidence': 0.5, 'signal': 'NONE', 'reason': 'nan_ema'}
                
            trend_strength = abs(ema_20[-1] - ema_50[-1]) / ema_50[-1] * 100 if ema_50[-1] != 0 else 0

            bb_upper, bb_middle, bb_lower = talib.BBANDS(df['close'].values, timeperiod=20)
            if np.isnan(bb_upper[-1]) or np.isnan(bb_middle[-1]) or bb_middle[-1] == 0:
                bb_width = 0
            else:
                bb_width = (bb_upper[-1] - bb_lower[-1]) / bb_middle[-1] * 100

            volume = df['tick_volume'] if 'tick_volume' in df.columns else df.get('real_volume', pd.Series([0]))
            avg_volume = np.mean(volume[-20:]) if len(volume) >= 20 else 0

            # Strategy assignment based on characteristics
            strategy_scores = {}

            # Scalping: high volume + moderate volatility
            if avg_volume > 100 and 0.1 < atr_pct < 0.3:
                strategy_scores['scalp'] = 0.8
            else:
                strategy_scores['scalp'] = 0.4

            # Range: low volatility + narrow bands
            if atr_pct < 0.15 and bb_width < 2.0:
                strategy_scores['range'] = 0.8
            else:
                strategy_scores['range'] = 0.3

            # Momentum: strong trend + good volatility
            if trend_strength > 0.5 and atr_pct > 0.2:
                strategy_scores['momentum'] = 0.85
            else:
                strategy_scores['momentum'] = 0.4

            # Breakout: moderate volatility + expanding bands
            if 0.15 < atr_pct < 0.4 and bb_width > 1.5:
                strategy_scores['breakout'] = 0.75
            else:
                strategy_scores['breakout'] = 0.4

            # Reversal: RSI extremes
            rsi = talib.RSI(df['close'].values, timeperiod=14)
            if not np.isnan(rsi[-1]) and (rsi[-1] < Thresholds.RSI_OVERSOLD or rsi[-1] > Thresholds.RSI_OVERBOUGHT):
                strategy_scores['reversal'] = 0.7
            else:
                strategy_scores['reversal'] = 0.3

            # Support/Resistance bounce: established range
            high_50 = df['high'].rolling(50).max().iloc[-1] if len(df) >= 50 else df['high'].max()
            low_50 = df['low'].rolling(50).min().iloc[-1] if len(df) >= 50 else df['low'].min()
            range_size = (high_50 - low_50) / price * 100 if price > 0 else 0
            
            if 1.0 < range_size < 5.0:
                strategy_scores['sr_bounce'] = 0.7
            else:
                strategy_scores['sr_bounce'] = 0.3

            best_strategy = max(strategy_scores.items(), key=lambda x: x[1])

            return {
                'strategy': best_strategy[0],
                'confidence': best_strategy[1],
                'signal': 'NONE',
                'characteristics': {
                    'atr_pct': round(atr_pct, 3),
                    'trend_strength': round(trend_strength, 3),
                    'bb_width': round(bb_width, 2),
                    'avg_volume': round(avg_volume, 0)
                }
            }
            
        except Exception as e:
            logger.error(f"Error in get_strongest_strategy_for_pair for {symbol}: {e}")
            return {'strategy': 'scalp', 'confidence': 0.5, 'signal': 'NONE', 'reason': f'error: {str(e)}'}

    # ------------------------------------------------------------------ #
    #  FIXED STRATEGY METHODS
    # ------------------------------------------------------------------ #

    def analyze_trend_reversal(self, df: pd.DataFrame) -> Dict:
        """
        OPTIMIZED Strategy 1: Trend reversal for 5-MINUTE charts
        
        Lookback periods optimized for quick reversals (15-30 minutes):
        - RSI: 7 periods = 35 minutes (faster response)
        - EMA 20: 20 periods = 100 minutes (short-term trend)
        - EMA 50: 50 periods = 250 minutes (medium-term filter)
        - 5-bar lookback = 25 minutes (recent structure)
        
        Detects reversals that complete in 15-45 minutes, not hours.
        """
        if df is None or len(df) < 50:
            return {'signal': 'NONE', 'confidence': 0, 'indicators': {}, 'type': 'reversal'}

        try:
            # Ensure proper data types
            close = df['close'].astype(np.float64).values
            high = df['high'].astype(np.float64).values
            low = df['low'].astype(np.float64).values
            
            # OPTIMIZED: Faster indicators for 5-min charts
            rsi = talib.RSI(close, timeperiod=7)  # Faster RSI (35 min vs 70 min)
            macd, macd_signal, macd_hist = talib.MACD(close, fastperiod=8, slowperiod=17, signalperiod=6)  # Faster MACD
            ema_20 = talib.EMA(close, timeperiod=20)  # Short-term trend (100 min)
            ema_50 = talib.EMA(close, timeperiod=50)  # Medium-term filter (250 min)

            # Check for NaN values
            if (np.isnan(rsi[-1]) or np.isnan(macd[-1]) or np.isnan(macd_signal[-1]) or 
                np.isnan(macd_hist[-1]) or np.isnan(ema_20[-1]) or np.isnan(ema_50[-1])):
                logger.warning("NaN values in reversal indicators")
                return {'signal': 'NONE', 'confidence': 0, 'indicators': {}, 'type': 'reversal'}

            # Current values
            current_price = df['close'].iloc[-1]
            current_rsi = rsi[-1]
            current_macd_hist = macd_hist[-1]
            current_ema_20 = ema_20[-1]
            current_ema_50 = ema_50[-1]

            # Previous values for divergence detection
            if len(rsi) < 5 or len(macd_hist) < 5:
                return {'signal': 'NONE', 'confidence': 0, 'indicators': {}, 'type': 'reversal'}
                
            prev_rsi = rsi[-2]
            prev_macd_hist = macd_hist[-2]

            # OPTIMIZED: Shorter lookback for 5-min charts (5 bars = 25 minutes)
            price_high_5 = df['high'].values[-5:].max()
            price_low_5 = df['low'].values[-5:].min()
            rsi_high_5 = rsi[-5:].max()
            rsi_low_5 = rsi[-5:].min()

            indicators = {
                'rsi': round(current_rsi, 2),
                'macd_hist': round(current_macd_hist, 5),
                'ema_20': round(current_ema_20, 5),
                'ema_50': round(current_ema_50, 5),
                'price': round(current_price, 5)
            }

            # ========== OPTIMIZED REVERSAL CONDITIONS FOR 5-MIN ==========
            
            # BUY Reversal: Quick oversold recovery (15-30 min reversal)
            oversold_extreme = current_rsi < 25  # More extreme for 5-min (was 30)
            rsi_recovering = current_rsi > prev_rsi and current_rsi > rsi[-3]
            rsi_above_low = current_rsi > rsi_low_5 + 3  # Smaller bounce needed (was 5)
            macd_hist_turning_up = current_macd_hist > prev_macd_hist
            near_lows = current_price <= price_low_5 * 1.001  # Within 0.1% of 5-bar low
            
            # Structure break: price breaking above recent 3-bar resistance (15 min)
            recent_highs = df['high'].values[-3:]
            resistance_3bar = np.max(recent_highs[:-1])
            structure_break_up = current_price > resistance_3bar

            # SELL Reversal: Quick overbought decline
            overbought_extreme = current_rsi > 75  # More extreme for 5-min (was 70)
            rsi_declining = current_rsi < prev_rsi and current_rsi < rsi[-3]
            rsi_below_high = current_rsi < rsi_high_5 - 3
            macd_hist_turning_down = current_macd_hist < prev_macd_hist
            near_highs = current_price >= price_high_5 * 0.999
            
            # Structure break: price breaking below recent 3-bar support
            recent_lows = df['low'].values[-3:]
            support_3bar = np.min(recent_lows[:-1])
            structure_break_down = current_price < support_3bar

            # Bullish reversal: Quick oversold recovery
            if oversold_extreme and rsi_recovering and rsi_above_low and structure_break_up:
                confidence = 0.68  # Slightly lower for faster signals
                if macd_hist_turning_up:
                    confidence += 0.05
                if near_lows:
                    confidence += 0.05
                # Boost if aligned with short-term trend
                if current_price > current_ema_50:
                    confidence += 0.08
                
                confidence = min(Thresholds.MAX_CONFIDENCE, confidence)
                indicators['reversal_type'] = 'quick_oversold_recovery'
                return {'signal': 'BUY', 'confidence': confidence, 'type': 'reversal', 'indicators': indicators}

            # Bearish reversal: Quick overbought decline
            if overbought_extreme and rsi_declining and rsi_below_high and structure_break_down:
                confidence = 0.68
                if macd_hist_turning_down:
                    confidence += 0.05
                if near_highs:
                    confidence += 0.05
                if current_price < current_ema_50:
                    confidence += 0.08
                
                confidence = min(Thresholds.MAX_CONFIDENCE, confidence)
                indicators['reversal_type'] = 'quick_overbought_decline'
                return {'signal': 'SELL', 'confidence': confidence, 'type': 'reversal', 'indicators': indicators}

            return {'signal': 'NONE', 'confidence': 0, 'type': 'reversal', 'indicators': indicators}
            
        except Exception as e:
            logger.error(f"Error in analyze_trend_reversal: {e}")
            return {'signal': 'NONE', 'confidence': 0, 'indicators': {}, 'type': 'reversal'}

    def analyze_breakout(self, df: pd.DataFrame) -> Dict:
        """
        OPTIMIZED Strategy 4: Breakout trading for 5-MINUTE charts
        
        Lookback periods optimized for quick breakouts (30-60 minutes):
        - 10-period high/low = 50 minutes (short-term breakout)
        - 20-period high/low = 100 minutes (medium-term breakout)
        - Consolidation detection: 6 bars = 30 minutes
        
        Detects breakouts that complete in 30-90 minutes, not hours.
        
        Signal Types:
        1. Quick breakout (10-period)
        2. Medium breakout (20-period)
        3. Bollinger Band breakout
        4. Consolidation breakout (30-min range)
        5. Retest after breakout
        """
        if len(df) < 50:
            return {'signal': 'NONE', 'confidence': 0, 'indicators': {}, 'type': 'breakout'}

        try:
            # Ensure proper data types
            close = df['close'].astype(np.float64).values
            high = df['high'].astype(np.float64).values
            low = df['low'].astype(np.float64).values
            
            # OPTIMIZED: Shorter lookback periods for 5-min charts
            high_10 = df['high'].rolling(10).max()  # 50 minutes
            low_10 = df['low'].rolling(10).min()
            high_20 = df['high'].rolling(20).max()  # 100 minutes
            low_20 = df['low'].rolling(20).min()

            upper, middle, lower_bb = talib.BBANDS(close, timeperiod=20)
            atr = talib.ATR(high, low, close, timeperiod=14)
            rsi = talib.RSI(close, timeperiod=7)  # Faster RSI for 5-min

            volume = df['tick_volume'] if 'tick_volume' in df.columns else df['real_volume']
            avg_volume = volume.rolling(10).mean()  # Shorter volume average

            current_price = df['close'].iloc[-1]
            current_high = df['high'].iloc[-1]
            current_low = df['low'].iloc[-1]
            prev_close = df['close'].iloc[-2]
            
            resistance_10 = high_10.iloc[-2]
            support_10 = low_10.iloc[-2]
            resistance_20 = high_20.iloc[-2]
            support_20 = low_20.iloc[-2]
            
            current_volume = volume.iloc[-1]
            avg_vol = avg_volume.iloc[-1]
            current_atr = atr[-1]
            current_rsi = rsi[-1]

            indicators = {
                'resistance_10': round(resistance_10, 5),
                'support_10': round(support_10, 5),
                'resistance_20': round(resistance_20, 5),
                'support_20': round(support_20, 5),
                'volume_ratio': round(current_volume / avg_vol, 2) if avg_vol > 0 else 0,
                'atr': round(current_atr, 5)
            }

            signals = []
            
            # --- METHOD 1: QUICK BREAKOUT (10-period = 50 minutes) ---
            volume_surge = current_volume > avg_vol * 1.3  # Lower threshold for 5-min
            strong_volume = current_volume > avg_vol * 1.8
            
            # Bullish quick breakout
            if current_high > resistance_10 and current_price > resistance_10:
                breakout_strength = (current_price - resistance_10) / current_atr if current_atr > 0 else 0
                if volume_surge:
                    conf = 0.72 if strong_volume else 0.68
                    conf += 0.04 if breakout_strength > 0.2 else 0
                    signals.append(('BUY', conf, 'quick_breakout_up'))
            
            # Bearish quick breakout
            if current_low < support_10 and current_price < support_10:
                breakout_strength = (support_10 - current_price) / current_atr if current_atr > 0 else 0
                if volume_surge:
                    conf = 0.72 if strong_volume else 0.68
                    conf += 0.04 if breakout_strength > 0.2 else 0
                    signals.append(('SELL', conf, 'quick_breakout_down'))
            
            # --- METHOD 2: MEDIUM BREAKOUT (20-period = 100 minutes) ---
            # Breaking 20-period high/low with strong volume
            if current_price > resistance_20 and prev_close <= resistance_20:
                if volume_surge and current_rsi > 50:
                    signals.append(('BUY', 0.75, 'medium_breakout_up'))
            
            if current_price < support_20 and prev_close >= support_20:
                if volume_surge and current_rsi < 50:
                    signals.append(('SELL', 0.75, 'medium_breakout_down'))
            
            # --- METHOD 2: BOLLINGER BAND BREAKOUT ---
            if current_price > upper[-1] and prev_close <= upper[-2]:
                if current_rsi > 50 and volume_surge:
                    signals.append(('BUY', 0.68, 'bb_breakout_up'))
            
            if current_price < lower_bb[-1] and prev_close >= lower_bb[-2]:
                if current_rsi < 50 and volume_surge:
                    signals.append(('SELL', 0.68, 'bb_breakout_down'))
            
            # --- METHOD 3: CONSOLIDATION BREAKOUT (6 bars = 30 minutes) ---
            # Detect tight consolidation
            if len(df) >= 6:
                recent_high = df['high'].iloc[-6:].max()
                recent_low = df['low'].iloc[-6:].min()
                consolidation_range = recent_high - recent_low
                avg_range = current_atr * 6
                
                is_consolidating = consolidation_range < avg_range * 0.6  # Tighter for 5-min
                
                if is_consolidating:
                    # Breaking out of 30-min consolidation upward
                    if current_price > recent_high and volume_surge:
                        signals.append(('BUY', 0.70, 'consolidation_breakout_up'))
                    
                    # Breaking out of 30-min consolidation downward
                    if current_price < recent_low and volume_surge:
                        signals.append(('SELL', 0.70, 'consolidation_breakout_down'))
            
            # --- METHOD 4: RETEST AFTER BREAKOUT (3 bars = 15 minutes) ---
            # Price broke resistance, pulled back, now retesting
            if len(df) >= 3:
                was_above_resistance = any(df['close'].iloc[-3:-1] > resistance_10)
                near_resistance = abs(current_price - resistance_10) / current_atr < 0.2 if current_atr > 0 else False
                
                if was_above_resistance and near_resistance and current_price > resistance_10:
                    if current_rsi > 45:
                        signals.append(('BUY', 0.63, 'retest_resistance_as_support'))
                
                # Price broke support, rallied, now retesting
                was_below_support = any(df['close'].iloc[-3:-1] < support_10)
                near_support = abs(current_price - support_10) / current_atr < 0.2 if current_atr > 0 else False
                
                if was_below_support and near_support and current_price < support_10:
                    if current_rsi < 55:
                        signals.append(('SELL', 0.63, 'retest_support_as_resistance'))
            
            # Select best signal
            if not signals:
                return {'signal': 'NONE', 'confidence': 0, 'type': 'breakout', 'indicators': indicators}
            
            signals.sort(key=lambda x: x[1], reverse=True)
            best_signal = signals[0]
            
            indicators['signal_type'] = best_signal[2]
            indicators['num_signals'] = len([s for s in signals if s[0] == best_signal[0]])
            
            return {
                'signal': best_signal[0],
                'confidence': best_signal[1],
                'type': 'breakout',
                'indicators': indicators
            }
            
        except Exception as e:
            logger.error(f"Error in analyze_breakout: {e}")
            return {'signal': 'NONE', 'confidence': 0, 'type': 'breakout', 'indicators': {}}

    def analyze_momentum(self, df: pd.DataFrame) -> Dict:
        """
        ENHANCED Strategy 5: Momentum trading with multiple detection methods
        
        Signal Types:
        1. ADX + RSI momentum
        2. MACD momentum surge
        3. ROC acceleration
        4. Trend strength continuation
        5. Multi-indicator confluence
        """
        if df is None or len(df) < 30:
            return {'signal': 'NONE', 'confidence': 0, 'indicators': {}, 'type': 'momentum'}

        try:
            # Ensure proper data types
            close = df['close'].astype(np.float64).values
            high = df['high'].astype(np.float64).values
            low = df['low'].astype(np.float64).values
            
            rsi = talib.RSI(close, timeperiod=14)
            macd, macd_signal, macd_hist = talib.MACD(close)
            adx = talib.ADX(high, low, close, timeperiod=14)
            plus_di = talib.PLUS_DI(high, low, close, timeperiod=14)
            minus_di = talib.MINUS_DI(high, low, close, timeperiod=14)
            roc = talib.ROC(close, timeperiod=10)
            ema_20 = talib.EMA(close, timeperiod=20)
            ema_50 = talib.EMA(close, timeperiod=50)

            # Validate data
            adx = adx[~np.isnan(adx)]
            if len(adx) == 0 or np.isnan(rsi[-1]) or np.isnan(macd_hist[-1]):
                return {'signal': 'NONE', 'confidence': 0, 'indicators': {}, 'type': 'momentum'}

            current_price = df['close'].iloc[-1]
            current_rsi = rsi[-1]
            current_macd_hist = macd_hist[-1]
            prev_macd_hist = macd_hist[-2] if len(macd_hist) >= 2 else 0
            current_adx = adx[-1]
            current_plus_di = plus_di[-1]
            current_minus_di = minus_di[-1]
            current_roc = roc[-1]
            current_ema_20 = ema_20[-1]
            current_ema_50 = ema_50[-1]

            indicators = {
                'rsi': round(current_rsi, 2),
                'macd_hist': round(current_macd_hist, 5),
                'adx': round(current_adx, 2),
                'roc': round(current_roc, 3)
            }

            signals = []
            
            # Trend context
            uptrend = current_price > current_ema_20 > current_ema_50
            downtrend = current_price < current_ema_20 < current_ema_50
            has_trend = current_adx > Thresholds.ADX_TREND_MIN
            strong_trend = current_adx > Thresholds.ADX_STRONG_TREND
            
            # --- METHOD 1: CLASSIC ADX + RSI MOMENTUM ---
            if 50 < current_rsi < 72 and current_macd_hist > 0 and has_trend:
                conf = 0.70 if strong_trend else 0.62
                conf += 0.05 if uptrend else 0
                signals.append(('BUY', conf, 'adx_rsi_momentum'))

            if 28 < current_rsi < 50 and current_macd_hist < 0 and has_trend:
                conf = 0.70 if strong_trend else 0.62
                conf += 0.05 if downtrend else 0
                signals.append(('SELL', conf, 'adx_rsi_momentum'))
            
            # --- METHOD 2: MACD HISTOGRAM SURGE ---
            if len(macd_hist) >= 3:
                hist_accelerating_up = (macd_hist[-1] > macd_hist[-2] > macd_hist[-3] and 
                                       macd_hist[-1] > 0)
                hist_accelerating_down = (macd_hist[-1] < macd_hist[-2] < macd_hist[-3] and 
                                         macd_hist[-1] < 0)
                
                if hist_accelerating_up and current_rsi > 50 and uptrend:
                    signals.append(('BUY', 0.68, 'macd_surge_up'))
                
                if hist_accelerating_down and current_rsi < 50 and downtrend:
                    signals.append(('SELL', 0.68, 'macd_surge_down'))
            
            # --- METHOD 3: ROC ACCELERATION ---
            if len(roc) >= 3:
                roc_accelerating_up = roc[-1] > roc[-2] > roc[-3] and roc[-1] > 1.0
                roc_accelerating_down = roc[-1] < roc[-2] < roc[-3] and roc[-1] < -1.0
                
                if roc_accelerating_up and current_rsi > 45 and uptrend:
                    signals.append(('BUY', 0.65, 'roc_acceleration_up'))
                
                if roc_accelerating_down and current_rsi < 55 and downtrend:
                    signals.append(('SELL', 0.65, 'roc_acceleration_down'))
            
            # --- METHOD 4: DIRECTIONAL INDICATOR STRENGTH ---
            # +DI significantly above -DI indicates strong bullish momentum
            di_spread = current_plus_di - current_minus_di
            
            if di_spread > 15 and current_plus_di > 25 and current_rsi > 50:
                conf = 0.72 if strong_trend else 0.64
                signals.append(('BUY', conf, 'di_strength_bullish'))
            
            if di_spread < -15 and current_minus_di > 25 and current_rsi < 50:
                conf = 0.72 if strong_trend else 0.64
                signals.append(('SELL', conf, 'di_strength_bearish'))
            
            # --- METHOD 5: MULTI-INDICATOR CONFLUENCE ---
            # All momentum indicators aligned
            bullish_confluence = (current_rsi > 55 and 
                                 current_macd_hist > 0 and 
                                 current_roc > 0.5 and
                                 current_plus_di > current_minus_di and
                                 uptrend)
            
            bearish_confluence = (current_rsi < 45 and 
                                 current_macd_hist < 0 and 
                                 current_roc < -0.5 and
                                 current_minus_di > current_plus_di and
                                 downtrend)
            
            if bullish_confluence:
                conf = 0.75 if strong_trend else 0.68
                signals.append(('BUY', conf, 'multi_indicator_confluence'))
            
            if bearish_confluence:
                conf = 0.75 if strong_trend else 0.68
                signals.append(('SELL', conf, 'multi_indicator_confluence'))
            
            # --- METHOD 6: MOMENTUM CONTINUATION ---
            # Already in momentum, continuing
            if len(df) >= 5:
                last_5_closes = df['close'].iloc[-5:].values
                consistent_uptrend = all(last_5_closes[i] < last_5_closes[i+1] for i in range(4))
                consistent_downtrend = all(last_5_closes[i] > last_5_closes[i+1] for i in range(4))
                
                if consistent_uptrend and current_rsi > 50 and current_rsi < 70 and has_trend:
                    signals.append(('BUY', 0.60, 'momentum_continuation_up'))
                
                if consistent_downtrend and current_rsi < 50 and current_rsi > 30 and has_trend:
                    signals.append(('SELL', 0.60, 'momentum_continuation_down'))
            
            # Select best signal
            if not signals:
                return {'signal': 'NONE', 'confidence': 0, 'type': 'momentum', 'indicators': indicators}
            
            signals.sort(key=lambda x: x[1], reverse=True)
            best_signal = signals[0]
            
            # Boost if multiple signals agree
            confirming_signals = [s for s in signals if s[0] == best_signal[0]]
            if len(confirming_signals) >= 3:
                best_signal = (best_signal[0], min(best_signal[1] + 0.05, 0.95), best_signal[2])
            
            indicators['signal_type'] = best_signal[2]
            indicators['num_signals'] = len(confirming_signals)
            
            return {
                'signal': best_signal[0],
                'confidence': best_signal[1],
                'type': 'momentum',
                'indicators': indicators
            }
            
        except Exception as e:
            logger.error(f"Error in analyze_momentum: {e}")
            return {'signal': 'NONE', 'confidence': 0, 'indicators': {}, 'type': 'momentum'}

    def analyze_support_resistance(self, df: pd.DataFrame) -> Dict:
        """
        ENHANCED Strategy 6: Support/Resistance bounce with multiple detection methods
        
        Signal Types:
        1. Classic S/R bounce
        2. Multiple touch confirmation
        3. Fibonacci retracement levels
        4. Pivot point bounces
        5. Dynamic S/R (moving averages)
        """
        if len(df) < 50:
            return {'signal': 'NONE', 'confidence': 0, 'indicators': {}, 'type': 'sr_bounce'}

        try:
            # Ensure proper data types
            close = df['close'].astype(np.float64).values
            high = df['high'].astype(np.float64).values
            low = df['low'].astype(np.float64).values
            
            # Calculate S/R levels
            high_50 = df['high'].rolling(50).max().iloc[-1]
            low_50 = df['low'].rolling(50).min().iloc[-1]
            high_20 = df['high'].rolling(20).max().iloc[-1]
            low_20 = df['low'].rolling(20).min().iloc[-1]

            rsi = talib.RSI(close, timeperiod=14)
            atr = talib.ATR(high, low, close, timeperiod=14)
            slowk, slowd = talib.STOCH(high, low, close)
            
            # Dynamic S/R (EMAs)
            ema_50 = talib.EMA(close, timeperiod=50)
            ema_200 = talib.EMA(close, timeperiod=200) if len(df) >= 200 else ema_50

            current_price = df['close'].iloc[-1]
            current_high = df['high'].iloc[-1]
            current_low = df['low'].iloc[-1]
            prev_close = df['close'].iloc[-2]
            prev_low = df['low'].iloc[-2]
            prev_high = df['high'].iloc[-2]
            
            current_rsi = rsi[-1]
            current_atr = atr[-1]
            current_slowk = slowk[-1]
            current_ema_50 = ema_50[-1]
            current_ema_200 = ema_200[-1]

            dist_to_support = (current_price - low_50) / low_50 * 100 if low_50 > 0 else 100
            dist_to_resistance = (high_50 - current_price) / high_50 * 100 if high_50 > 0 else 100

            indicators = {
                'support_50': round(low_50, 5),
                'resistance_50': round(high_50, 5),
                'support_20': round(low_20, 5),
                'resistance_20': round(high_20, 5),
                'dist_support': round(dist_to_support, 2),
                'dist_resistance': round(dist_to_resistance, 2),
                'rsi': round(current_rsi, 2)
            }

            signals = []
            
            # --- METHOD 1: CLASSIC S/R BOUNCE ---
            near_support = dist_to_support < 0.5
            near_resistance = dist_to_resistance < 0.5
            
            if near_support and current_price > prev_close and current_rsi < 40:
                signals.append(('BUY', 0.70, 'classic_support_bounce'))

            if near_resistance and current_price < prev_close and current_rsi > 60:
                signals.append(('SELL', 0.70, 'classic_resistance_bounce'))
            
            # --- METHOD 2: MULTIPLE TOUCH CONFIRMATION ---
            # Count how many times price touched support/resistance recently
            if len(df) >= 20:
                support_touches = 0
                resistance_touches = 0
                tolerance = current_atr * 0.5 if current_atr > 0 else low_50 * 0.001
                
                for i in range(-20, -1):
                    if abs(df['low'].iloc[i] - low_50) < tolerance:
                        support_touches += 1
                    if abs(df['high'].iloc[i] - high_50) < tolerance:
                        resistance_touches += 1
                
                # Strong support (touched 3+ times)
                if support_touches >= 3 and near_support and current_rsi < 45:
                    signals.append(('BUY', 0.75, 'strong_support_multiple_touch'))
                
                # Strong resistance (touched 3+ times)
                if resistance_touches >= 3 and near_resistance and current_rsi > 55:
                    signals.append(('SELL', 0.75, 'strong_resistance_multiple_touch'))
            
            # --- METHOD 3: FIBONACCI RETRACEMENT LEVELS ---
            if len(df) >= 50:
                swing_high = df['high'].iloc[-50:].max()
                swing_low = df['low'].iloc[-50:].min()
                fib_range = swing_high - swing_low
                
                # Key Fibonacci levels
                fib_618 = swing_high - (fib_range * 0.618)
                fib_50 = swing_high - (fib_range * 0.50)
                fib_382 = swing_high - (fib_range * 0.382)
                
                tolerance = current_atr * 0.3 if current_atr > 0 else fib_range * 0.01
                
                # Bouncing off 61.8% retracement (strong support in uptrend)
                if abs(current_price - fib_618) < tolerance and current_rsi < 45:
                    if current_price > current_ema_200:  # Overall uptrend
                        signals.append(('BUY', 0.72, 'fib_618_bounce'))
                
                # Bouncing off 50% retracement
                if abs(current_price - fib_50) < tolerance and current_rsi > 40 and current_rsi < 60:
                    if current_price > prev_close:
                        signals.append(('BUY', 0.65, 'fib_50_bounce'))
                
                # Rejecting at 38.2% retracement (resistance in downtrend)
                if abs(current_price - fib_382) < tolerance and current_rsi > 55:
                    if current_price < current_ema_200:  # Overall downtrend
                        signals.append(('SELL', 0.72, 'fib_382_rejection'))
            
            # --- METHOD 4: PIVOT POINT BOUNCES ---
            if len(df) >= 2:
                # Yesterday's high, low, close (or previous period)
                prev_period_high = df['high'].iloc[-2]
                prev_period_low = df['low'].iloc[-2]
                prev_period_close = df['close'].iloc[-2]
                
                # Calculate pivot points
                pivot = (prev_period_high + prev_period_low + prev_period_close) / 3
                r1 = 2 * pivot - prev_period_low
                s1 = 2 * pivot - prev_period_high
                
                tolerance = current_atr * 0.3 if current_atr > 0 else pivot * 0.001
                
                # Bouncing off S1
                if abs(current_price - s1) < tolerance and current_price > prev_close and current_rsi < 45:
                    signals.append(('BUY', 0.68, 'pivot_s1_bounce'))
                
                # Rejecting at R1
                if abs(current_price - r1) < tolerance and current_price < prev_close and current_rsi > 55:
                    signals.append(('SELL', 0.68, 'pivot_r1_rejection'))
                
                # Bouncing off pivot itself
                if abs(current_price - pivot) < tolerance:
                    if current_price > prev_close and current_rsi < 50:
                        signals.append(('BUY', 0.63, 'pivot_point_bounce'))
                    elif current_price < prev_close and current_rsi > 50:
                        signals.append(('SELL', 0.63, 'pivot_point_rejection'))
            
            # --- METHOD 5: DYNAMIC S/R (MOVING AVERAGES) ---
            # EMA 50 acting as support
            distance_to_ema50 = abs(current_price - current_ema_50) / current_price if current_price > 0 else 1
            distance_to_ema200 = abs(current_price - current_ema_200) / current_price if current_price > 0 else 1
            
            if distance_to_ema50 < 0.002 and current_price > current_ema_50:
                if current_price > prev_close and current_slowk < 30:
                    signals.append(('BUY', 0.66, 'ema50_support_bounce'))
            
            if distance_to_ema50 < 0.002 and current_price < current_ema_50:
                if current_price < prev_close and current_slowk > 70:
                    signals.append(('SELL', 0.66, 'ema50_resistance_rejection'))
            
            # EMA 200 acting as major support/resistance
            if distance_to_ema200 < 0.003 and current_price > current_ema_200:
                if current_price > prev_close and current_rsi < 40:
                    signals.append(('BUY', 0.73, 'ema200_support_bounce'))
            
            if distance_to_ema200 < 0.003 and current_price < current_ema_200:
                if current_price < prev_close and current_rsi > 60:
                    signals.append(('SELL', 0.73, 'ema200_resistance_rejection'))
            
            # --- METHOD 6: REJECTION WICKS ---
            # Long lower wick at support = buyers stepping in
            candle_body = abs(current_price - df['open'].iloc[-1])
            lower_wick = min(current_price, df['open'].iloc[-1]) - current_low
            upper_wick = current_high - max(current_price, df['open'].iloc[-1])
            candle_range = current_high - current_low
            
            if candle_range > 0:
                # Hammer at support
                if (lower_wick > candle_body * 2 and 
                    upper_wick < candle_body * 0.5 and
                    near_support and current_rsi < 45):
                    signals.append(('BUY', 0.71, 'hammer_at_support'))
                
                # Shooting star at resistance
                if (upper_wick > candle_body * 2 and 
                    lower_wick < candle_body * 0.5 and
                    near_resistance and current_rsi > 55):
                    signals.append(('SELL', 0.71, 'shooting_star_at_resistance'))
            
            # Select best signal
            if not signals:
                return {'signal': 'NONE', 'confidence': 0, 'type': 'sr_bounce', 'indicators': indicators}
            
            signals.sort(key=lambda x: x[1], reverse=True)
            best_signal = signals[0]
            
            # Boost if multiple signals agree
            confirming_signals = [s for s in signals if s[0] == best_signal[0]]
            if len(confirming_signals) >= 2:
                best_signal = (best_signal[0], min(best_signal[1] + 0.04, 0.95), best_signal[2])
            
            indicators['signal_type'] = best_signal[2]
            indicators['num_signals'] = len(confirming_signals)
            
            return {
                'signal': best_signal[0],
                'confidence': best_signal[1],
                'type': 'sr_bounce',
                'indicators': indicators
            }
            
        except Exception as e:
            logger.error(f"Error in analyze_support_resistance: {e}")
            return {'signal': 'NONE', 'confidence': 0, 'type': 'sr_bounce', 'indicators': {}}

    def analyze_ranging(self, df: pd.DataFrame) -> Dict:
        """
        Strategy 3: Range trading with breakout detection
        ENHANCED: 7 signal types for more opportunities in quiet markets.
        
        FIXED: Previous version required ALL 3 indicators (BB + RSI + Stoch) at extremes
        simultaneously, which rarely happens in stable/decreasing volatility.
        Now uses signal collection with types that need only 2-of-3 indicator agreement.
        """
        if len(df) < 30:
            return {'signal': 'NONE', 'confidence': 0, 'indicators': {}, 'type': 'range'}

        # Ensure proper data types
        close = df['close'].astype(np.float64).values
        high = df['high'].astype(np.float64).values
        low = df['low'].astype(np.float64).values

        upper, middle, lower = talib.BBANDS(close, timeperiod=20, nbdevup=2, nbdevdn=2)
        rsi = talib.RSI(close, timeperiod=14)
        slowk, slowd = talib.STOCH(high, low, close)

        current_price = df['close'].iloc[-1]
        current_upper = upper[-1]
        current_lower = lower[-1]
        current_middle = middle[-1]
        current_rsi = rsi[-1]
        current_slowk = slowk[-1]
        current_slowd = slowd[-1]
        prev_close = df['close'].iloc[-2]

        band_width = (current_upper - current_lower) / current_middle
        band_position = (current_price - current_lower) / (current_upper - current_lower)

        indicators = {
            'bb_upper': round(current_upper, 5),
            'bb_lower': round(current_lower, 5),
            'bb_position': round(band_position, 2),
            'rsi': round(current_rsi, 2),
            'stoch': round(current_slowk, 2),
            'band_width': round(band_width, 4)
        }

        # FIXED: Detect breakout conditions
        if len(upper) >= 5:
            prev_band_width = (upper[-5] - lower[-5]) / middle[-5]
            band_expansion = (band_width - prev_band_width) / prev_band_width if prev_band_width > 0 else 0
            
            # Band expanding > 20% = breakout likely
            if band_expansion > 0.20:
                return {
                    'signal': 'NONE',
                    'confidence': 0,
                    'type': 'range',
                    'indicators': {**indicators, 'breakout_detected': True, 'band_expansion': round(band_expansion, 2)}
                }

        # Collect all signals instead of early-returning (find the best one)
        signals = []

        # Only trade in established ranges (bands not too wide or too narrow)
        # RELAXED: Wider acceptable range for 5-min charts
        if 0.003 < band_width < 0.15:  # Was 0.005-0.10, now 0.003-0.15 (more flexible)
            
            # SIGNAL TYPE 1: Strong extremes - all 3 indicators agree (high confidence)
            if band_position < 0.25 and current_rsi < 40 and current_slowk < 30:
                signals.append(('BUY', 0.75, 'extreme_oversold'))

            if band_position > 0.75 and current_rsi > 60 and current_slowk > 70:
                signals.append(('SELL', 0.75, 'extreme_overbought'))
            
            # SIGNAL TYPE 2: Moderate extremes - all 3 indicators agree (medium confidence)
            if band_position < 0.35 and current_rsi < 45 and current_slowk < 35:
                signals.append(('BUY', 0.65, 'moderate_oversold'))
            
            if band_position > 0.65 and current_rsi > 55 and current_slowk > 65:
                signals.append(('SELL', 0.65, 'moderate_overbought'))
            
            # SIGNAL TYPE 3: Mean reversion from edges
            if len(df) >= 3:
                prev_band_position = (df['close'].iloc[-3] - lower[-3]) / (upper[-3] - lower[-3])
                
                if prev_band_position < 0.30 and band_position > 0.30 and current_slowk > current_slowd:
                    signals.append(('BUY', 0.60, 'mean_reversion_up'))
                
                if prev_band_position > 0.70 and band_position < 0.70 and current_slowk < current_slowd:
                    signals.append(('SELL', 0.60, 'mean_reversion_down'))
            
            # SIGNAL TYPE 4: Stochastic crossovers in range
            if len(slowk) >= 2 and len(slowd) >= 2:
                prev_slowk = slowk[-2]
                prev_slowd = slowd[-2]
                
                if band_position < 0.55 and current_slowk > current_slowd and prev_slowk <= prev_slowd:
                    signals.append(('BUY', 0.55, 'stoch_cross_up'))
                
                if band_position > 0.45 and current_slowk < current_slowd and prev_slowk >= prev_slowd:
                    signals.append(('SELL', 0.55, 'stoch_cross_down'))
            
            # ===== NEW SIGNAL TYPES (2-of-3 indicator agreement) =====
            
            # SIGNAL TYPE 5: RSI + BB position (no stoch required)
            # In quiet markets, RSI and BB often agree while stoch stays neutral
            if band_position < 0.30 and current_rsi < 42:
                signals.append(('BUY', 0.58, 'rsi_bb_oversold'))
            
            if band_position > 0.70 and current_rsi > 58:
                signals.append(('SELL', 0.58, 'rsi_bb_overbought'))
            
            # SIGNAL TYPE 6: BB touch/pierce with directional candle confirmation
            # Price touching lower band + bullish candle = bounce signal
            if band_position < 0.15 and current_price > prev_close:
                conf = 0.60 if current_rsi < 45 else 0.52
                signals.append(('BUY', conf, 'bb_lower_bounce'))
            
            if band_position > 0.85 and current_price < prev_close:
                conf = 0.60 if current_rsi > 55 else 0.52
                signals.append(('SELL', conf, 'bb_upper_bounce'))
            
            # SIGNAL TYPE 7: Stoch + BB position (no RSI required)
            # Stoch oversold in lower half of bands
            if band_position < 0.40 and current_slowk < 25 and current_slowk > current_slowd:
                signals.append(('BUY', 0.56, 'stoch_bb_oversold'))
            
            if band_position > 0.60 and current_slowk > 75 and current_slowk < current_slowd:
                signals.append(('SELL', 0.56, 'stoch_bb_overbought'))

        # Select best signal
        if not signals:
            return {'signal': 'NONE', 'confidence': 0, 'type': 'range', 'indicators': indicators}
        
        signals.sort(key=lambda x: x[1], reverse=True)
        best_signal = signals[0]
        
        # Boost if multiple signals agree on direction
        confirming = [s for s in signals if s[0] == best_signal[0]]
        if len(confirming) >= 3:
            best_signal = (best_signal[0], min(best_signal[1] + 0.06, 0.85), best_signal[2])
        elif len(confirming) >= 2:
            best_signal = (best_signal[0], min(best_signal[1] + 0.03, 0.80), best_signal[2])
        
        return {
            'signal': best_signal[0],
            'confidence': best_signal[1],
            'type': 'range',
            'indicators': {**indicators, 'signal_type': best_signal[2], 'confirming_signals': len(confirming)}
        }

    def analyze_scalping(self, df: pd.DataFrame, spread_info: Dict = None) -> Dict:
        """
        ULTRA-SMART Strategy 2: Advanced scalping with 10+ signal detection methods
        
        Signal Types:
        1. EMA Crossover (classic)
        2. Momentum Continuation
        3. Pullback to EMA
        4. Price Action Patterns (pin bars, engulfing)
        5. RSI Divergence
        6. MACD Histogram Momentum
        7. Bollinger Squeeze Breakout
        8. Volume Spike Confirmation
        9. Multi-timeframe Alignment
        10. Micro Trend Following
        """
        if df is None or len(df) < 50:
            return {'signal': 'NONE', 'confidence': 0, 'indicators': {}, 'type': 'scalp'}

        try:
            # === ENSURE PROPER DATA TYPES ===
            close = df['close'].astype(np.float64).values
            high = df['high'].astype(np.float64).values
            low = df['low'].astype(np.float64).values
            open_price = df['open'].astype(np.float64).values if 'open' in df.columns else close
            
            # === INDICATORS ===
            ema_fast = talib.EMA(close, timeperiod=5)
            ema_slow = talib.EMA(close, timeperiod=10)
            ema_trend = talib.EMA(close, timeperiod=20)
            ema_filter = talib.EMA(close, timeperiod=50)
            
            rsi = talib.RSI(close, timeperiod=14)
            roc = talib.ROC(close, timeperiod=3)
            atr = talib.ATR(high, low, close, timeperiod=14)
            
            macd, macd_signal, macd_hist = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)
            
            upper, middle, lower = talib.BBANDS(close, timeperiod=20, nbdevup=2, nbdevdn=2)
            
            # Volume indicators
            volume_sma = None
            if 'tick_volume' in df.columns:
                volume_sma = talib.SMA(df['tick_volume'].astype(np.float64).values, timeperiod=20)

            # Validate indicators
            if (np.isnan(ema_fast[-1]) or np.isnan(ema_slow[-1]) or np.isnan(ema_trend[-1]) or
                np.isnan(rsi[-1]) or np.isnan(roc[-1]) or np.isnan(atr[-1]) or
                np.isnan(macd_hist[-1]) or np.isnan(upper[-1])):
                return {'signal': 'NONE', 'confidence': 0, 'indicators': {}, 'type': 'scalp'}

            current_price = df['close'].iloc[-1]
            current_high = df['high'].iloc[-1]
            current_low = df['low'].iloc[-1]
            prev_close = df['close'].iloc[-2]
            prev_high = df['high'].iloc[-2]
            prev_low = df['low'].iloc[-2]
            
            current_ema_fast = ema_fast[-1]
            current_ema_slow = ema_slow[-1]
            current_ema_trend = ema_trend[-1]
            current_ema_filter = ema_filter[-1]
            current_rsi = rsi[-1]
            current_momentum = roc[-1]
            current_atr = atr[-1]
            current_macd_hist = macd_hist[-1]
            prev_macd_hist = macd_hist[-2] if len(macd_hist) >= 2 else 0

            # FIXED: Check spread cost
            if spread_info and spread_info.get('valid'):
                spread_pct = spread_info.get('spread_pct', 0)
                spread_points = current_price * spread_pct
                expected_profit = current_atr * 1.5
                
                if expected_profit < spread_points * Thresholds.SCALP_MIN_PROFIT_SPREAD_RATIO:
                    return {
                        'signal': 'NONE',
                        'confidence': 0,
                        'type': 'scalp',
                        'indicators': {
                            'filtered': 'spread_too_high',
                            'spread_cost': round(spread_points, 5),
                            'expected_profit': round(expected_profit, 5)
                        }
                    }

            indicators = {
                'ema_5': round(current_ema_fast, 5),
                'ema_10': round(current_ema_slow, 5),
                'ema_20': round(current_ema_trend, 5),
                'rsi': round(current_rsi, 2),
                'momentum': round(current_momentum, 3),
                'atr': round(current_atr, 5),
                'macd_hist': round(current_macd_hist, 5)
            }

            # === TREND CONTEXT ===
            uptrend = current_price > current_ema_trend > current_ema_filter
            downtrend = current_price < current_ema_trend < current_ema_filter
            strong_uptrend = current_ema_fast > current_ema_slow > current_ema_trend > current_ema_filter
            strong_downtrend = current_ema_fast < current_ema_slow < current_ema_trend < current_ema_filter

            # === VOLATILITY CHECK ===
            atr_clean = atr[~np.isnan(atr)]
            if len(atr_clean) < 10:
                return {'signal': 'NONE', 'confidence': 0, 'type': 'scalp', 'indicators': indicators}
            
            avg_atr = np.mean(atr_clean[-10:])
            has_volatility = current_atr > avg_atr * 0.7
            high_volatility = current_atr > avg_atr * 1.3
            
            # Bollinger Band squeeze detection
            band_width = (upper[-1] - lower[-1]) / middle[-1]
            avg_band_width = np.mean([(upper[i] - lower[i]) / middle[i] for i in range(-20, -1)])
            is_squeeze = band_width < avg_band_width * 0.7
            
            indicators['volatility'] = 'high' if high_volatility else 'normal' if has_volatility else 'low'
            indicators['bb_squeeze'] = is_squeeze

            # === VOLUME ANALYSIS ===
            volume_spike = False
            if volume_sma is not None and not np.isnan(volume_sma[-1]):
                current_volume = df['tick_volume'].iloc[-1]
                volume_spike = current_volume > volume_sma[-1] * 1.5
                indicators['volume_spike'] = volume_spike

            # ================================================================
            # SIGNAL DETECTION - 10 METHODS
            # ================================================================
            
            signals = []  # Collect all potential signals
            
            # --- METHOD 1: CLASSIC EMA CROSSOVER ---
            if len(ema_fast) >= 2 and len(ema_slow) >= 2:
                prev_ema_fast = ema_fast[-2]
                prev_ema_slow = ema_slow[-2]
                
                bullish_cross = (current_ema_fast > current_ema_slow and 
                               prev_ema_fast <= prev_ema_slow and
                               uptrend and current_momentum > 0)
                
                bearish_cross = (current_ema_fast < current_ema_slow and 
                               prev_ema_fast >= prev_ema_slow and
                               downtrend and current_momentum < 0)
                
                if bullish_cross:
                    conf = 0.78 if has_volatility else 0.68
                    conf += 0.05 if volume_spike else 0
                    signals.append(('BUY', conf, 'ema_crossover'))
                elif bearish_cross:
                    conf = 0.78 if has_volatility else 0.68
                    conf += 0.05 if volume_spike else 0
                    signals.append(('SELL', conf, 'ema_crossover'))
            
            # --- METHOD 2: MOMENTUM CONTINUATION ---
            if strong_uptrend and current_momentum > 0.5 and current_price > current_ema_fast:
                conf = 0.65
                conf += 0.05 if current_macd_hist > prev_macd_hist else 0
                conf += 0.05 if volume_spike else 0
                signals.append(('BUY', conf, 'momentum_continuation'))
            
            if strong_downtrend and current_momentum < -0.5 and current_price < current_ema_fast:
                conf = 0.65
                conf += 0.05 if current_macd_hist < prev_macd_hist else 0
                conf += 0.05 if volume_spike else 0
                signals.append(('SELL', conf, 'momentum_continuation'))
            
            # --- METHOD 3: PULLBACK TO EMA (BUY THE DIP) ---
            if uptrend and current_ema_fast > current_ema_slow:
                distance_to_ema = abs(current_price - current_ema_fast) / current_price
                if distance_to_ema < 0.0015 and current_momentum > -0.5:
                    conf = 0.62
                    conf += 0.05 if current_rsi > 45 and current_rsi < 55 else 0
                    signals.append(('BUY', conf, 'pullback_to_ema'))
            
            if downtrend and current_ema_fast < current_ema_slow:
                distance_to_ema = abs(current_price - current_ema_fast) / current_price
                if distance_to_ema < 0.0015 and current_momentum < 0.5:
                    conf = 0.62
                    conf += 0.05 if current_rsi > 45 and current_rsi < 55 else 0
                    signals.append(('SELL', conf, 'pullback_to_ema'))
            
            # --- METHOD 4: PRICE ACTION PATTERNS ---
            candle_body = abs(current_price - df['open'].iloc[-1])
            candle_range = current_high - current_low
            prev_candle_body = abs(prev_close - df['open'].iloc[-2])
            
            # Bullish Pin Bar (hammer)
            if (current_low < prev_low and 
                candle_body < candle_range * 0.3 and
                (current_high - max(current_price, df['open'].iloc[-1])) < candle_range * 0.1 and
                uptrend):
                signals.append(('BUY', 0.60, 'pin_bar_bullish'))
            
            # Bearish Pin Bar (shooting star)
            if (current_high > prev_high and
                candle_body < candle_range * 0.3 and
                (min(current_price, df['open'].iloc[-1]) - current_low) < candle_range * 0.1 and
                downtrend):
                signals.append(('SELL', 0.60, 'pin_bar_bearish'))
            
            # Bullish Engulfing
            if (current_price > df['open'].iloc[-1] and
                prev_close < df['open'].iloc[-2] and
                current_price > df['open'].iloc[-2] and
                df['open'].iloc[-1] < prev_close and
                candle_body > prev_candle_body * 1.2 and
                uptrend):
                signals.append(('BUY', 0.68, 'engulfing_bullish'))
            
            # Bearish Engulfing
            if (current_price < df['open'].iloc[-1] and
                prev_close > df['open'].iloc[-2] and
                current_price < df['open'].iloc[-2] and
                df['open'].iloc[-1] > prev_close and
                candle_body > prev_candle_body * 1.2 and
                downtrend):
                signals.append(('SELL', 0.68, 'engulfing_bearish'))
            
            # --- METHOD 5: RSI DIVERGENCE ---
            if len(rsi) >= 10 and len(df) >= 10:
                # Find recent swing highs/lows
                recent_high_idx = df['high'].iloc[-10:].idxmax()
                recent_low_idx = df['low'].iloc[-10:].idxmin()
                
                # Bullish divergence: price lower low, RSI higher low
                if recent_low_idx == len(df) - 1:
                    prev_low_idx = df['low'].iloc[-10:-1].idxmin()
                    if (df['low'].iloc[-1] < df['low'].iloc[prev_low_idx] and
                        rsi[-1] > rsi[prev_low_idx] and
                        current_rsi < 40 and uptrend):
                        signals.append(('BUY', 0.72, 'rsi_divergence_bullish'))
                
                # Bearish divergence: price higher high, RSI lower high
                if recent_high_idx == len(df) - 1:
                    prev_high_idx = df['high'].iloc[-10:-1].idxmax()
                    if (df['high'].iloc[-1] > df['high'].iloc[prev_high_idx] and
                        rsi[-1] < rsi[prev_high_idx] and
                        current_rsi > 60 and downtrend):
                        signals.append(('SELL', 0.72, 'rsi_divergence_bearish'))
            
            # --- METHOD 6: MACD HISTOGRAM MOMENTUM ---
            if len(macd_hist) >= 3:
                hist_increasing = macd_hist[-1] > macd_hist[-2] > macd_hist[-3]
                hist_decreasing = macd_hist[-1] < macd_hist[-2] < macd_hist[-3]
                
                if hist_increasing and macd_hist[-1] > 0 and uptrend:
                    signals.append(('BUY', 0.63, 'macd_momentum_up'))
                
                if hist_decreasing and macd_hist[-1] < 0 and downtrend:
                    signals.append(('SELL', 0.63, 'macd_momentum_down'))
            
            # --- METHOD 7: BOLLINGER SQUEEZE BREAKOUT ---
            if is_squeeze:
                # Price breaking above upper band with momentum
                if current_price > upper[-1] and current_momentum > 0.3 and uptrend:
                    signals.append(('BUY', 0.70, 'bb_squeeze_breakout_up'))
                
                # Price breaking below lower band with momentum
                if current_price < lower[-1] and current_momentum < -0.3 and downtrend:
                    signals.append(('SELL', 0.70, 'bb_squeeze_breakout_down'))
            
            # --- METHOD 8: VOLUME SPIKE CONFIRMATION ---
            if volume_spike and has_volatility:
                # Volume spike with price moving up and above EMA
                if current_price > prev_close and current_price > current_ema_fast and uptrend:
                    signals.append(('BUY', 0.58, 'volume_spike_up'))
                
                # Volume spike with price moving down and below EMA
                if current_price < prev_close and current_price < current_ema_fast and downtrend:
                    signals.append(('SELL', 0.58, 'volume_spike_down'))
            
            # --- METHOD 9: MICRO TREND FOLLOWING ---
            # Very short-term trend (last 5 candles)
            if len(df) >= 5:
                last_5_closes = df['close'].iloc[-5:].values
                micro_uptrend = all(last_5_closes[i] < last_5_closes[i+1] for i in range(4))
                micro_downtrend = all(last_5_closes[i] > last_5_closes[i+1] for i in range(4))
                
                if micro_uptrend and uptrend and current_momentum > 0:
                    signals.append(('BUY', 0.56, 'micro_trend_up'))
                
                if micro_downtrend and downtrend and current_momentum < 0:
                    signals.append(('SELL', 0.56, 'micro_trend_down'))
            
            # --- METHOD 10: RSI EXTREME REVERSAL ---
            if current_rsi < 25 and current_rsi > rsi[-2] and uptrend:
                signals.append(('BUY', 0.64, 'rsi_extreme_reversal_up'))
            
            if current_rsi > 75 and current_rsi < rsi[-2] and downtrend:
                signals.append(('SELL', 0.64, 'rsi_extreme_reversal_down'))
            
            # ================================================================
            # SELECT BEST SIGNAL
            # ================================================================
            
            if not signals:
                return {'signal': 'NONE', 'confidence': 0, 'type': 'scalp', 'indicators': indicators}
            
            # Sort by confidence (highest first)
            signals.sort(key=lambda x: x[1], reverse=True)
            best_signal = signals[0]
            
            # Count confirming signals (same direction)
            confirming_signals = [s for s in signals if s[0] == best_signal[0]]
            confirmation_boost = min(len(confirming_signals) - 1, 3) * 0.03  # Max +0.09
            
            final_confidence = min(best_signal[1] + confirmation_boost, 0.95)
            
            indicators['signal_type'] = best_signal[2]
            indicators['confirming_signals'] = len(confirming_signals)
            indicators['all_signals'] = [f"{s[2]}({s[1]:.2f})" for s in confirming_signals[:5]]
            
            return {
                'signal': best_signal[0],
                'confidence': final_confidence,
                'type': 'scalp',
                'indicators': indicators
            }
            
        except Exception as e:
            logger.error(f"Error in analyze_scalping: {e}")
            return {'signal': 'NONE', 'confidence': 0, 'indicators': {}, 'type': 'scalp'}

    def get_market_signals(self, symbol: str, allowed_strategy: str = None, enabled_strategies: List[str] = None, debug: bool = False) -> Dict:
        """
        IMPROVED: Each pair uses exactly ONE strategy with signal caching and expiry.
        FIXED: Includes SL/TP calculation, correlation check, daily loss limit, spread cost validation
        """
        # Check signal cache first
        cache_key = f"{symbol}_{allowed_strategy}_{','.join(enabled_strategies) if enabled_strategies else 'all'}"
        if cache_key in self.signal_cache:
            cached_signal, cache_time = self.signal_cache[cache_key]
            age_minutes = (datetime.now() - cache_time).total_seconds() / 60
            if age_minutes < SIGNAL_EXPIRY_MINUTES:
                if debug:
                    logger.debug(f"Using cached signal for {symbol} (age: {age_minutes:.1f}m)")
                return cached_signal

        try:
            df = self.get_price_data(symbol)
            if df is None:
                if debug:
                    logger.info(f"   🔍 {symbol}: No price data available")
                return {'symbol': symbol, 'signal': 'NONE', 'confidence': 0, 'indicators': {}}

            # Early spread filter - don't trade if spread is too high
            spread_info = self.analyze_spread_health(symbol)
            if spread_info.get('valid') and spread_info['spread_risk'] == 'high':
                if debug:
                    logger.info(f"   🔍 {symbol}: Filtered - High spread ({spread_info.get('spread_pct', 0):.4f}%)")
                return {
                    'symbol': symbol,
                    'signal': 'NONE',
                    'confidence': 0,
                    'indicators': {'filtered': 'high_spread'},
                    'type': 'filtered'
                }

            # NEW: Check daily loss limit
            daily_loss_check = self.check_daily_loss_limit()
            if not daily_loss_check['allowed']:
                if debug:
                    logger.info(f"   🔍 {symbol}: Filtered - Daily loss limit reached")
                return {
                    'symbol': symbol,
                    'signal': 'NONE',
                    'confidence': 0,
                    'indicators': {'filtered': 'daily_loss_limit', **daily_loss_check},
                    'type': 'filtered'
                }

            # Get multi-timeframe analysis
            mtf = self.analyze_multi_timeframe_trends(symbol)
            overall_trend = mtf.get('overall', 'neutral')
            
            # NEW: Detect volatility regime
            vol_regime = self.detect_volatility_regime(symbol, df)

            # Pattern recognition
            patterns = self.detect_chart_patterns(df)

            all_strategies = {
                'reversal': self.analyze_trend_reversal,
                'scalp': lambda df: self.analyze_scalping(df, spread_info),  # Pass spread info
                'range': self.analyze_ranging,
                'breakout': self.analyze_breakout,
                'momentum': self.analyze_momentum,
                'sr_bounce': self.analyze_support_resistance
            }

            # Filter enabled strategies
            if enabled_strategies:
                available_strategies = {k: v for k, v in all_strategies.items() if k in enabled_strategies}
                if debug and not available_strategies:
                    logger.info(f"   🔍 {symbol}: No enabled strategies match")
            else:
                available_strategies = all_strategies

            # STEP 1: Determine which ONE strategy to use
            chosen_strategy = None
            fallback_to_best = False

            if allowed_strategy and allowed_strategy in available_strategies:
                # Check if strategy is on cooldown
                if not self.is_strategy_on_cooldown(symbol, allowed_strategy):
                    chosen_strategy = allowed_strategy
                elif debug:
                    logger.info(f"   🔍 {symbol}: Recommended strategy '{allowed_strategy}' is on cooldown")
                    fallback_to_best = True
            elif allowed_strategy and debug:
                logger.info(f"   🔍 {symbol}: Recommended strategy '{allowed_strategy}' not in enabled list")
                fallback_to_best = True

            if not chosen_strategy or fallback_to_best:
                best_adaptive = self.get_best_strategy_for_pair(symbol)
                if best_adaptive and best_adaptive in available_strategies:
                    chosen_strategy = best_adaptive

            if not chosen_strategy:
                strongest = self.get_strongest_strategy_for_pair(symbol)
                if strongest['strategy'] in available_strategies:
                    chosen_strategy = strongest['strategy']
                else:
                    chosen_strategy = list(available_strategies.keys())[0] if available_strategies else 'scalp'

            if debug:
                logger.info(f"   🔍 {symbol}: Using strategy '{chosen_strategy}'")

            # STEP 2: Run the chosen strategy
            if chosen_strategy not in available_strategies:
                logger.warning(f"Strategy {chosen_strategy} not available for {symbol}")
                return {'symbol': symbol, 'signal': 'NONE', 'confidence': 0, 'indicators': {}}
                
            signal_result = available_strategies[chosen_strategy](df)

            # STEP 2.5: FALLBACK - If chosen strategy returns NONE, try other strategies
            # Always try fallback if primary strategy fails
            if signal_result['signal'] == 'NONE':
                if debug:
                    logger.info(f"   🔍 {symbol}: Strategy '{chosen_strategy}' returned NONE, trying fallback strategies...")
                
                # Try all other strategies and pick the best signal
                # CRITICAL FIX: Only consider signals in the SAME direction to avoid conflicts
                best_fallback = None
                best_confidence = 0
                best_strat_name = None
                first_signal_direction = None  # Track first valid signal direction
                
                for strat_name, strat_func in available_strategies.items():
                    if strat_name == chosen_strategy:
                        continue  # Skip the one we already tried
                    
                    try:
                        # Call the strategy function properly
                        result = strat_func(df)
                        
                        if result and result.get('signal') != 'NONE':
                            conf = result.get('confidence', 0)
                            signal_dir = result['signal']
                            
                            # CRITICAL: If we already found a signal, only consider same direction
                            if first_signal_direction is None:
                                first_signal_direction = signal_dir
                            elif signal_dir != first_signal_direction:
                                if debug:
                                    logger.info(f"   🔍 {symbol}: {strat_name} found {signal_dir} signal (conf={conf:.2f}) - IGNORED (conflicts with {first_signal_direction})")
                                continue  # Skip conflicting signals
                            
                            if debug:
                                logger.info(f"   🔍 {symbol}: {strat_name} found {signal_dir} signal (conf={conf:.2f})")
                            
                            if conf > best_confidence:
                                best_fallback = result
                                best_confidence = conf
                                best_strat_name = strat_name
                    except Exception as e:
                        if debug:
                            logger.error(f"   Error trying fallback strategy {strat_name}: {e}")
                
                if best_fallback:
                    if 'indicators' not in best_fallback:
                        best_fallback['indicators'] = {}
                    best_fallback['indicators']['fallback_from'] = chosen_strategy
                    best_fallback['indicators']['chosen_strategy'] = best_strat_name
                    
                    signal_result = best_fallback
                    chosen_strategy = best_strat_name
                    if debug:
                        logger.info(f"   ✓ {symbol}: Using fallback strategy '{chosen_strategy}' with confidence {best_confidence:.2f}")

            if debug and signal_result['signal'] == 'NONE':
                logger.info(f"   🔍 {symbol}: All strategies returned NONE")

            # Ensure indicators dict exists
            if 'indicators' not in signal_result or signal_result['indicators'] is None:
                signal_result['indicators'] = {}

            # STEP 3: Apply pattern boost (capped at MAX_CONFIDENCE)
            if patterns['signal'] != 'NONE' and patterns['signal'] == signal_result['signal']:
                # FIXED: Dynamic pattern boost based on pattern quality
                pattern_quality = patterns.get('bullish_score' if patterns['signal'] == 'BUY' else 'bearish_score', 0)
                boost_multiplier = 1.10 + (pattern_quality * 0.05)  # 1.10 to 1.15 based on quality
                signal_result['confidence'] = min(
                    Thresholds.MAX_CONFIDENCE,
                    signal_result['confidence'] * boost_multiplier
                )
                if patterns['patterns']:
                    signal_result['indicators']['pattern'] = patterns['patterns'][0]['name']
                    signal_result['indicators']['pattern_confidence'] = patterns['patterns'][0]['confidence']

            # STEP 4: Multi-timeframe filter (strategy-aware) - REDUCED PENALTIES
            if signal_result['signal'] != 'NONE':
                if signal_result['signal'] == 'BUY' and overall_trend == 'strong_bearish':
                    if chosen_strategy != 'reversal':
                        signal_result['confidence'] *= 0.85  # Was 0.5, now 0.85 (less aggressive)
                elif signal_result['signal'] == 'SELL' and overall_trend == 'strong_bullish':
                    if chosen_strategy != 'reversal':
                        signal_result['confidence'] *= 0.85  # Was 0.5, now 0.85 (less aggressive)
                elif overall_trend == 'conflicting':
                    signal_result['confidence'] *= 0.90  # Was 0.7, now 0.90 (less aggressive)

            # STEP 5: Enforce minimum confidence (SMART: adaptive based on volatility)
            # REDUCED THRESHOLDS - In stable/decreasing volatility, we can be more aggressive
            # FIXED: Skip this check if signal is already NONE (avoids log spam)
            if signal_result['signal'] != 'NONE':
                min_conf_threshold = Thresholds.MIN_CONFIDENCE
                if vol_regime.get('regime') == 'decreasing':
                    min_conf_threshold *= 0.85  # 15% lower in calm markets
                elif vol_regime.get('regime') == 'expanding':
                    min_conf_threshold *= 1.05  # 5% higher in volatile markets
                
                if signal_result['confidence'] < min_conf_threshold:
                    if debug:
                        logger.info(f"   🔍 {symbol}: Signal rejected - confidence {signal_result['confidence']:.2f} below minimum {min_conf_threshold:.2f} (vol: {vol_regime.get('regime')})")
                    signal_result['signal'] = 'NONE'

            # STEP 6: NEW - Check correlation risk
            if signal_result['signal'] != 'NONE':
                corr_risk = self.check_correlation_risk(symbol, signal_result['signal'])
                if not corr_risk['allowed']:
                    if debug:
                        logger.info(f"   🔍 {symbol}: Signal rejected - {corr_risk['reason']}")
                    signal_result['signal'] = 'NONE'
                    signal_result['indicators']['correlation_risk'] = corr_risk
                else:
                    signal_result['indicators']['correlation_risk'] = corr_risk['risk']

            # STEP 7: NEW - Calculate SL/TP if signal is valid
            sl_tp_info = {}
            if signal_result['signal'] != 'NONE':
                sl_tp_info = self.calculate_stop_loss_take_profit(
                    symbol, signal_result['signal'], df, chosen_strategy, spread_info
                )
                
                if not sl_tp_info.get('valid'):
                    if debug:
                        logger.info(f"   🔍 {symbol}: Signal rejected - {sl_tp_info.get('reason', 'invalid SL/TP')}")
                    signal_result['signal'] = 'NONE'
                    signal_result['indicators']['sl_tp_issue'] = sl_tp_info.get('reason')

            # Add metadata to indicators
            signal_result['indicators']['mtf_trend'] = overall_trend
            signal_result['indicators']['chosen_strategy'] = chosen_strategy
            signal_result['indicators']['vol_regime'] = vol_regime.get('regime', 'unknown')

            # Store analysis
            self.last_analysis[symbol] = {
                'chosen': signal_result,
                'mtf': mtf,
                'patterns': patterns,
                'strategy_used': chosen_strategy,
                'vol_regime': vol_regime
            }

            result = {
                'symbol': symbol,
                'signal': signal_result['signal'],
                'confidence': signal_result['confidence'],
                'type': chosen_strategy,
                'price': df['close'].iloc[-1],
                'timestamp': datetime.now(),
                'indicators': signal_result['indicators'],
                'mtf_trend': overall_trend,
                'patterns': patterns['pattern_count'],
                'vol_regime': vol_regime.get('regime', 'unknown'),
                # NEW: Include SL/TP in result
                'stop_loss': sl_tp_info.get('stop_loss'),
                'take_profit': sl_tp_info.get('take_profit'),
                'entry': sl_tp_info.get('entry'),
                'risk_reward': sl_tp_info.get('risk_reward'),
                'risk_pips': sl_tp_info.get('risk_pips'),
                'reward_pips': sl_tp_info.get('reward_pips')
            }

            # Cache the signal
            self.signal_cache[cache_key] = (result, datetime.now())
            
            # Clean old cache entries (keep last 100)
            if len(self.signal_cache) > 100:
                oldest_keys = sorted(
                    self.signal_cache.items(),
                    key=lambda x: x[1][1]
                )[:50]
                for key, _ in oldest_keys:
                    del self.signal_cache[key]

            return result
            
        except Exception as e:
            logger.error(f"Error in get_market_signals for {symbol}: {e}")
            return {
                'symbol': symbol,
                'signal': 'NONE',
                'confidence': 0,
                'indicators': {'error': str(e)},
                'type': 'error'
            }