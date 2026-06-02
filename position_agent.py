"""
Position Agent - Smart Exit Management
Each open position gets its own agent that monitors and decides when to exit
position_agent.py
ENHANCED: ATR-based thresholds, partial exits, breakeven logic, trailing stops
"""

import MetaTrader5 as mt5
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional, List
import talib

# Constants
WEEKEND_CLOSE_HOUR = 21  # Friday 9 PM
WEEKEND_CLOSE_DAY = 4  # Friday (0=Monday)
SESSION_TRANSITIONS = {
    'asian_close': (8, 0),    # 8 AM
    'london_open': (8, 0),    # 8 AM
    'ny_open': (13, 0),       # 1 PM
    'london_close': (16, 30)  # 4:30 PM
}


class PositionAgent:
    """
    Individual agent that monitors a single position and makes exit decisions.
    Implements "feelings" - intuition about when to exit.
    ENHANCED: Integrates with MarketAnalyzer for sophisticated analysis.
    FIXED: ATR-based thresholds, partial exits, breakeven, trailing stops, time-based exits
    """
    
    def __init__(self, symbol: str, action: str, entry_price: float, lot_size: float, 
                 strategy: str, analyzer=None, initial_sl: float = None, initial_tp: float = None,
                 atr: float = None, spread: float = None, custom_sl_usd: float = None, custom_tp_usd: float = None):
        self.symbol = symbol
        self.action = action  # BUY or SELL
        self.entry_price = entry_price
        self.lot_size = lot_size
        self.strategy = strategy
        self.entry_time = datetime.now()
        self.analyzer = analyzer  # Reference to MarketAnalyzer for advanced analysis
        
        # NEW: Store initial SL/TP from analyzer
        self.initial_sl = initial_sl
        self.initial_tp = initial_tp
        self.current_sl = initial_sl
        self.current_tp = initial_tp
        
        # NEW: Custom SL/TP in USD (if user set custom levels)
        self.custom_sl_usd = custom_sl_usd
        self.custom_tp_usd = custom_tp_usd
        self.use_custom_levels = (custom_sl_usd is not None or custom_tp_usd is not None)
        
        # DETAILED LOGGING FOR CUSTOM TP/SL INITIALIZATION
        if self.use_custom_levels:
            import logging
            logger = logging.getLogger(__name__)
            logger.critical(f"[AGENT_CREATED_WITH_CUSTOM] {symbol} | "
                          f"Custom_TP=${custom_tp_usd} Custom_SL=${custom_sl_usd} | "
                          f"Entry={entry_price:.5f} | Strategy={strategy}")
            print(f"   🤖 Agent created with custom TP/SL: TP=${custom_tp_usd} SL=${custom_sl_usd}")
        
        # NEW: Store ATR and spread for dynamic calculations
        self.entry_atr = atr if atr else 0.0001  # Fallback to small value
        self.spread = spread if spread else 0.0
        
        # Tracking
        self.peak_profit = 0.0
        self.peak_profit_time = datetime.now()
        self.lowest_profit = 0.0
        self.momentum_history = []
        self.profit_history = []
        self.decision_history = []
        
        # NEW: Exit tracking
        self.breakeven_moved = False
        self.partial_exit_done = False
        self.remaining_lot_size = lot_size
        
        # NEW: Correlation tracking
        self.correlated_positions = []
        
        # Strategy-specific thresholds (FIXED: Now ATR-based)
        self._set_strategy_thresholds()
        
    def _set_strategy_thresholds(self):
        """
        Set thresholds based on strategy type - each strategy has different characteristics
        FIXED: Now uses ATR multipliers instead of fixed pips
        """
        strategy_configs = {
            'scalp': {
                'enough_profit_atr': 1.5,      # FIXED: 1.5x ATR instead of 8 pips
                'reversal_drawdown': 0.30,     # FIXED: Tighter for scalps
                'strong_momentum': 0.70,
                'weak_momentum': 0.35,
                'max_time_minutes': 30,        # NEW: Max 30 min for scalps
                'breakeven_atr': 0.8,          # NEW: Move to BE after 0.8 ATR profit
                'partial_exit_atr': 1.2,       # NEW: Take 50% at 1.2 ATR
                'trail_start_atr': 1.5,        # NEW: Start trailing at 1.5 ATR
                'trail_distance_atr': 0.5      # NEW: Trail 0.5 ATR behind
            },
            'momentum': {
                'enough_profit_atr': 2.5,      # FIXED: 2.5x ATR
                'reversal_drawdown': 0.35,
                'strong_momentum': 0.80,
                'weak_momentum': 0.25,
                'max_time_minutes': 120,       # NEW: Max 2 hours
                'breakeven_atr': 1.0,
                'partial_exit_atr': 2.0,
                'trail_start_atr': 2.5,
                'trail_distance_atr': 1.0
            },
            'reversal': {
                'enough_profit_atr': 3.0,      # FIXED: 3.0x ATR
                'reversal_drawdown': 0.30,
                'strong_momentum': 0.75,
                'weak_momentum': 0.30,
                'max_time_minutes': 180,       # NEW: Max 3 hours
                'breakeven_atr': 1.2,
                'partial_exit_atr': 2.5,
                'trail_start_atr': 3.0,
                'trail_distance_atr': 1.2
            },
            'breakout': {
                'enough_profit_atr': 3.5,      # FIXED: 3.5x ATR
                'reversal_drawdown': 0.30,
                'strong_momentum': 0.78,
                'weak_momentum': 0.28,
                'max_time_minutes': 150,       # NEW: Max 2.5 hours
                'breakeven_atr': 1.0,
                'partial_exit_atr': 2.5,
                'trail_start_atr': 3.0,
                'trail_distance_atr': 1.0
            },
            'range': {
                'enough_profit_atr': 1.8,      # FIXED: 1.8x ATR
                'reversal_drawdown': 0.25,
                'strong_momentum': 0.72,
                'weak_momentum': 0.32,
                'max_time_minutes': 60,        # NEW: Max 1 hour
                'breakeven_atr': 0.8,
                'partial_exit_atr': 1.5,
                'trail_start_atr': 2.0,
                'trail_distance_atr': 0.6
            },
            'sr_bounce': {
                'enough_profit_atr': 2.0,      # FIXED: 2.0x ATR
                'reversal_drawdown': 0.28,
                'strong_momentum': 0.75,
                'weak_momentum': 0.30,
                'max_time_minutes': 90,        # NEW: Max 1.5 hours
                'breakeven_atr': 1.0,
                'partial_exit_atr': 1.8,
                'trail_start_atr': 2.5,
                'trail_distance_atr': 0.8
            }
        }
        
        config = strategy_configs.get(self.strategy, strategy_configs['scalp'])
        
        # Store ATR-based thresholds
        self.enough_profit_atr = config['enough_profit_atr']
        self.reversal_drawdown_threshold = config['reversal_drawdown']
        self.strong_momentum_threshold = config['strong_momentum']
        self.weak_momentum_threshold = config['weak_momentum']
        self.max_time_minutes = config['max_time_minutes']
        self.breakeven_atr = config['breakeven_atr']
        self.partial_exit_atr = config['partial_exit_atr']
        self.trail_start_atr = config['trail_start_atr']
        self.trail_distance_atr = config['trail_distance_atr']
        
    def update(self, current_price: float, market_data: Dict, other_positions: List[Dict] = None, current_profit_usd: float = None) -> Dict:
        """
        Update agent state and make exit decision.
        ENHANCED: Uses MarketAnalyzer for sophisticated trend/momentum analysis.
        FIXED: Returns complete exit instructions with SL/TP levels
        
        Returns:
            {
                'decision': 'EXIT_FULL' | 'EXIT_PARTIAL' | 'MODIFY_SL' | 'MODIFY_TP' | 'HOLD',
                'reason': str,
                'confidence': float (0.0-1.0),
                'new_sl': float (if MODIFY_SL),
                'new_tp': float (if MODIFY_TP),
                'exit_lot_size': float (if EXIT_PARTIAL),
                'remaining_rr': float (risk/reward remaining)
            }
        """
        # Calculate current profit in pips (FIXED: Now accounts for spread)
        profit_pips = self._calculate_profit_pips(current_price)
        
        # Update tracking
        self._update_tracking(profit_pips, market_data)
        
        # NEW: Update correlation awareness
        if other_positions:
            self._update_correlation_awareness(other_positions)
        
        # Calculate momentum (enhanced with analyzer if available)
        momentum = self._calculate_momentum(market_data)
        
        # Get trend strength from analyzer if available
        trend_strength = self._get_trend_strength(market_data)
        
        # NEW: Get current ATR (for dynamic thresholds)
        current_atr = self._get_current_atr(market_data)
        
        # NEW: Calculate remaining risk/reward
        remaining_rr = self._calculate_remaining_risk_reward(current_price, profit_pips)
        
        # NEW: Check time-based exit
        time_exit = self._check_time_based_exit()
        
        # NEW: Check weekend/session exit
        session_exit = self._check_session_exit()
        
        # Check all "feelings" (enhanced with analyzer data)
        feeling_enough = self._feeling_enough_profit(profit_pips, momentum, trend_strength, current_atr)
        feeling_reversal = self._feeling_reversal(profit_pips, market_data, trend_strength)
        feeling_strong = self._feeling_strong_momentum(momentum, market_data, trend_strength)
        
        # NEW: Check breakeven opportunity
        breakeven_action = self._check_breakeven(profit_pips, current_atr)
        
        # NEW: Check partial exit opportunity
        partial_exit_action = self._check_partial_exit(profit_pips, current_atr)
        
        # NEW: Check trailing stop opportunity
        trailing_action = self._check_trailing_stop(profit_pips, current_price, current_atr)
        
        # Make decision based on all factors (FIXED: Returns complete instructions)
        decision = self._make_decision(
            feeling_enough, feeling_reversal, feeling_strong,
            profit_pips, momentum, remaining_rr, time_exit, session_exit,
            breakeven_action, partial_exit_action, trailing_action,
            current_price, current_atr, current_profit_usd
        )
        
        # Record decision
        self.decision_history.append({
            'time': datetime.now(),
            'profit': profit_pips,
            'momentum': momentum,
            'trend_strength': trend_strength,
            'remaining_rr': remaining_rr,
            'decision': decision['decision'],
            'reason': decision['reason']
        })
        
        # NEW: Learn from decision history (simple pattern detection)
        self._analyze_decision_patterns()
        
        return decision
    
    def _calculate_profit_pips(self, current_price: float) -> float:
        """
        Calculate profit in pips
        FIXED: Now uses proper pip calculation for all symbol types AND accounts for spread
        """
        # Determine pip multiplier based on symbol type
        if 'XAU' in self.symbol or 'XAG' in self.symbol or 'XPT' in self.symbol or 'XPD' in self.symbol:
            # Precious metals: 1 pip = 0.1
            pip_multiplier = 10
        elif 'JPY' in self.symbol:
            # JPY pairs: 1 pip = 0.01
            pip_multiplier = 1000
        elif any(idx in self.symbol for idx in ['US30', 'US100', 'US500', 'UK100', 'GER40', 'FRA40', 'ESP35', 'ITA40', 'AUS200', 'JPN225', 'HK50', 'CHN50', 'IND50', 'SWI20', 'NED25']):
            # Indices: 1 pip = 1 point
            pip_multiplier = 1
        elif any(crypto in self.symbol for crypto in ['BTC', 'ETH', 'LTC', 'XRP', 'BCH']):
            # Crypto: 1 pip = 1
            pip_multiplier = 1
        elif any(oil in self.symbol for oil in ['OIL', 'WTI', 'BRENT']):
            # Oil: 1 pip = 0.01
            pip_multiplier = 100
        else:
            # Standard forex: 1 pip = 0.0001
            pip_multiplier = 100000
        
        if self.action == 'BUY':
            profit_pips = (current_price - self.entry_price) * pip_multiplier
        else:
            profit_pips = (self.entry_price - current_price) * pip_multiplier
        
        # FIXED: Subtract spread cost from profit
        spread_pips = self.spread * pip_multiplier if self.spread else 0
        profit_pips -= spread_pips
        
        return profit_pips
    
    def _update_tracking(self, profit_pips: float, market_data: Dict):
        """Update tracking variables"""
        # Track peak profit
        if profit_pips > self.peak_profit:
            self.peak_profit = profit_pips
            self.peak_profit_time = datetime.now()
        
        # Track lowest profit
        if profit_pips < self.lowest_profit:
            self.lowest_profit = profit_pips
        
        # Track profit history
        self.profit_history.append({
            'time': datetime.now(),
            'profit': profit_pips
        })
        
        # Keep only last 20 records
        if len(self.profit_history) > 20:
            self.profit_history = self.profit_history[-20:]
    
    def _calculate_momentum(self, market_data: Dict) -> float:
        """
        Calculate momentum strength (0.0 to 1.0)
        Higher = stronger momentum in position direction
        ENHANCED: Uses analyzer's momentum indicators if available
        """
        if 'close_prices' not in market_data or len(market_data['close_prices']) < 10:
            return 0.5  # Neutral if no data
        
        closes = np.array(market_data['close_prices'][-10:])
        
        # If analyzer is available, use its sophisticated momentum calculation
        if self.analyzer and 'indicators' in market_data:
            indicators = market_data['indicators']
            
            # Use MACD histogram for momentum
            if 'macd_hist' in indicators and indicators['macd_hist'] is not None:
                macd_hist = indicators['macd_hist']
                # Normalize MACD histogram to 0-1 range
                if self.action == 'BUY':
                    momentum = (macd_hist + 0.001) / 0.002  # Map -0.001 to +0.001 → 0 to 1
                else:
                    momentum = (-macd_hist + 0.001) / 0.002  # Invert for SELL
                
                momentum = max(0.0, min(1.0, momentum))
                return momentum
            
            # Use RSI for momentum strength
            if 'rsi' in indicators and indicators['rsi'] is not None:
                rsi = indicators['rsi']
                if self.action == 'BUY':
                    # For BUY: RSI > 50 is good, normalize to 0-1
                    momentum = (rsi - 30) / 40  # Map 30-70 → 0-1
                else:
                    # For SELL: RSI < 50 is good
                    momentum = (70 - rsi) / 40  # Map 70-30 → 0-1
                
                momentum = max(0.0, min(1.0, momentum))
                return momentum
        
        # Fallback: Calculate rate of change
        roc = talib.ROC(closes, timeperiod=3)
        
        if len(roc) < 3:
            return 0.5
        
        # Get recent momentum
        recent_roc = roc[-3:]
        avg_roc = np.mean(recent_roc)
        
        # Normalize to 0-1 range
        # Positive ROC for BUY, negative for SELL
        if self.action == 'BUY':
            momentum = (avg_roc + 2) / 4  # Map -2 to +2 → 0 to 1
        else:
            momentum = (-avg_roc + 2) / 4  # Invert for SELL
        
        # Clamp to 0-1
        momentum = max(0.0, min(1.0, momentum))
        
        return momentum
    
    def _get_trend_strength(self, market_data: Dict) -> float:
        """
        Get trend strength from analyzer (0.0 to 1.0)
        Uses ADX and EMA alignment from analyzer if available
        FIXED: Better fallback calculation
        """
        if not self.analyzer or 'indicators' not in market_data:
            return 0.5  # Neutral if no analyzer
        
        indicators = market_data['indicators']
        
        # Use ADX for trend strength
        if 'adx' in indicators and indicators['adx'] is not None:
            adx = indicators['adx']
            # ADX: 0-25 weak, 25-50 strong, 50+ very strong
            trend_strength = min(adx / 50, 1.0)
            return trend_strength
        
        # Fallback: Use EMA alignment (FIXED: Better calculation)
        if 'ema_fast' in indicators and 'ema_slow' in indicators:
            ema_fast = indicators['ema_fast']
            ema_slow = indicators['ema_slow']
            
            if ema_fast and ema_slow and ema_slow != 0:
                # Calculate separation between EMAs as trend strength
                separation = abs(ema_fast - ema_slow) / ema_slow
                # Normalize: 0.1% separation = weak, 1% = strong
                trend_strength = min(separation * 100, 1.0)
                return trend_strength
        
        return 0.5
    
    def _get_current_atr(self, market_data: Dict) -> float:
        """
        NEW: Get current ATR from market data
        Falls back to entry ATR if unavailable
        """
        if 'close_prices' in market_data and len(market_data['close_prices']) >= 14:
            try:
                highs = np.array(market_data.get('high_prices', market_data['close_prices']))
                lows = np.array(market_data.get('low_prices', market_data['close_prices']))
                closes = np.array(market_data['close_prices'])
                
                atr = talib.ATR(highs, lows, closes, timeperiod=14)
                if len(atr) > 0 and not np.isnan(atr[-1]):
                    return atr[-1]
            except:
                pass
        
        # Fallback to entry ATR
        return self.entry_atr
    
    def _calculate_remaining_risk_reward(self, current_price: float, profit_pips: float) -> float:
        """
        NEW: Calculate remaining risk/reward ratio
        If R:R < 1.0, position has poor remaining potential
        """
        if not self.initial_tp or not self.current_sl:
            return 1.0  # Unknown, assume neutral
        
        # Calculate remaining reward (distance to TP)
        if self.action == 'BUY':
            remaining_reward = self.initial_tp - current_price
            remaining_risk = current_price - self.current_sl
        else:
            remaining_reward = current_price - self.initial_tp
            remaining_risk = self.current_sl - current_price
        
        if remaining_risk <= 0:
            return 999.0  # Risk-free (SL at breakeven or better)
        
        if remaining_reward <= 0:
            return 0.0  # Already past TP
        
        return remaining_reward / remaining_risk
    
    def _check_time_based_exit(self) -> Tuple[bool, str]:
        """
        NEW: Check if position should exit based on time
        Different strategies have different time limits
        """
        time_in_trade = (datetime.now() - self.entry_time).total_seconds() / 60  # minutes
        
        if time_in_trade > self.max_time_minutes:
            return True, f"Max time exceeded ({time_in_trade:.0f}m > {self.max_time_minutes}m)"
        
        return False, None
    
    def _check_session_exit(self) -> Tuple[bool, str]:
        """
        NEW: Check if position should exit due to session/weekend
        """
        now = datetime.now()
        
        # Check for Friday close (weekend risk)
        if now.weekday() == WEEKEND_CLOSE_DAY and now.hour >= WEEKEND_CLOSE_HOUR:
            return True, "Friday close - weekend gap risk"
        
        # Check for major session transitions (optional - can cause whipsaws)
        # Disabled by default, but code is here if needed
        # for session_name, (hour, minute) in SESSION_TRANSITIONS.items():
        #     if now.hour == hour and now.minute >= minute:
        #         return True, f"Session transition: {session_name}"
        
        return False, None
    
    def _check_breakeven(self, profit_pips: float, current_atr: float) -> Optional[Dict]:
        """
        NEW: Check if we should move SL to breakeven
        Returns action dict if breakeven should be moved
        """
        if self.breakeven_moved:
            return None  # Already moved
        
        # Convert ATR to pips for comparison
        atr_pips = self._atr_to_pips(current_atr)
        breakeven_threshold = atr_pips * self.breakeven_atr
        
        if profit_pips >= breakeven_threshold:
            # Move SL to breakeven (entry price + small buffer for spread)
            if self.action == 'BUY':
                new_sl = self.entry_price + (self.spread * 0.5)  # Half spread buffer
            else:
                new_sl = self.entry_price - (self.spread * 0.5)
            
            self.breakeven_moved = True
            self.current_sl = new_sl
            
            return {
                'decision': 'MODIFY_SL',
                'new_sl': new_sl,
                'reason': f'Breakeven: Profit {profit_pips:.1f}p >= {breakeven_threshold:.1f}p',
                'confidence': 0.95
            }
        
        return None
    
    def _check_partial_exit(self, profit_pips: float, current_atr: float) -> Optional[Dict]:
        """
        NEW: Check if we should take partial profits
        Returns action dict if partial exit should be taken
        """
        if self.partial_exit_done:
            return None  # Already taken partial
        
        if self.remaining_lot_size <= self.lot_size * 0.5:
            return None  # Already reduced position
        
        # Convert ATR to pips
        atr_pips = self._atr_to_pips(current_atr)
        partial_threshold = atr_pips * self.partial_exit_atr
        
        if profit_pips >= partial_threshold:
            # Exit 50% of position
            exit_lot = self.remaining_lot_size * 0.5
            self.remaining_lot_size -= exit_lot
            self.partial_exit_done = True
            
            return {
                'decision': 'EXIT_PARTIAL',
                'exit_lot_size': exit_lot,
                'reason': f'Partial exit: Profit {profit_pips:.1f}p >= {partial_threshold:.1f}p (50% out)',
                'confidence': 0.90
            }
        
        return None
    
    def _check_trailing_stop(self, profit_pips: float, current_price: float, current_atr: float) -> Optional[Dict]:
        """
        NEW: Check if we should trail the stop loss
        Returns action dict with new SL level
        """
        # Convert ATR to pips
        atr_pips = self._atr_to_pips(current_atr)
        trail_start_threshold = atr_pips * self.trail_start_atr
        trail_distance = current_atr * self.trail_distance_atr
        
        # Only trail if profit exceeds threshold
        if profit_pips < trail_start_threshold:
            return None
        
        # Calculate new trailing SL
        if self.action == 'BUY':
            new_sl = current_price - trail_distance
            # Only move SL up, never down
            if self.current_sl and new_sl <= self.current_sl:
                return None
        else:
            new_sl = current_price + trail_distance
            # Only move SL down, never up
            if self.current_sl and new_sl >= self.current_sl:
                return None
        
        self.current_sl = new_sl
        
        return {
            'decision': 'MODIFY_SL',
            'new_sl': new_sl,
            'reason': f'Trailing stop: {trail_distance*10000:.1f}p behind price',
            'confidence': 0.85
        }
    
    def _update_correlation_awareness(self, other_positions: List[Dict]):
        """
        NEW: Update awareness of correlated positions
        """
        self.correlated_positions = []
        
        for pos in other_positions:
            # Simple correlation check based on currency pairs
            # e.g., EURUSD and GBPUSD are correlated (both vs USD)
            if pos['symbol'] != self.symbol:
                # Extract currencies
                my_base = self.symbol[:3]
                my_quote = self.symbol[3:6]
                other_base = pos['symbol'][:3]
                other_quote = pos['symbol'][3:6]
                
                # Check for correlation
                if (my_base == other_base or my_quote == other_quote or
                    my_base == other_quote or my_quote == other_base):
                    self.correlated_positions.append(pos)
    
    def _analyze_decision_patterns(self):
        """
        NEW: Analyze decision history for patterns
        Simple learning from past decisions
        """
        if len(self.decision_history) < 5:
            return  # Not enough data
        
        # Check if we're flip-flopping (indecisive)
        recent_decisions = [d['decision'] for d in self.decision_history[-5:]]
        unique_decisions = len(set(recent_decisions))
        
        if unique_decisions >= 4:
            # Too many different decisions = indecisive market
            # Could adjust thresholds here in future
            pass
        
        # Check if momentum predictions were accurate
        # (This is placeholder for future ML integration)
        pass
    
    def _atr_to_pips(self, atr: float) -> float:
        """
        NEW: Convert ATR value to pips for the symbol
        """
        if 'JPY' in self.symbol:
            return atr * 1000
        elif 'XAU' in self.symbol or 'XAG' in self.symbol:
            return atr * 10
        else:
            return atr * 100000
    
    def _feeling_enough_profit(self, profit_pips: float, momentum: float, trend_strength: float, current_atr: float) -> Tuple[bool, str]:
        """
        FEELING 1: "Enough is enough"
        Good profit + weak momentum + weakening trend = time to exit
        ENHANCED: Uses trend strength from analyzer
        FIXED: Now uses ATR-based threshold
        """
        # FIXED: Dynamic threshold based on ATR
        atr_pips = self._atr_to_pips(current_atr)
        threshold = atr_pips * self.enough_profit_atr
        
        # If trend is weakening, lower the threshold (exit sooner)
        if trend_strength < 0.4:
            threshold *= 0.8
        
        if profit_pips > threshold and momentum < self.weak_momentum_threshold:
            return True, f"Good profit ({profit_pips:.1f}p > {threshold:.1f}p), momentum dying ({momentum:.2f}), trend weak ({trend_strength:.2f})"
        
        return False, None
    
    def _feeling_reversal(self, profit_pips: float, market_data: Dict, trend_strength: float = 0.5) -> Tuple[bool, str]:
        """
        FEELING 2: "It's reversing!"
        Profit dropping from peak + trend weakening = reversal
        ENHANCED: Uses trend strength from analyzer
        FIXED: Balanced reversal detection (not too aggressive, not too slow)
        """
        # Check drawdown from peak (BALANCED: Not too aggressive)
        if self.peak_profit > 4:  # Was 3, now 4 (middle ground)
            drawdown_pct = (self.peak_profit - profit_pips) / self.peak_profit if self.peak_profit > 0 else 0
            drawdown_abs = self.peak_profit - profit_pips
            
            # If trend is weakening, be more sensitive to reversals
            reversal_threshold = self.reversal_drawdown_threshold * 0.90  # Was 0.85, now 0.90 (less aggressive)
            if trend_strength < 0.4:
                reversal_threshold *= 0.80  # Was 0.75, now 0.80 (less aggressive)
            
            # BALANCED: Absolute drawdown (was 7 pips, now 8 pips)
            if drawdown_pct > reversal_threshold or drawdown_abs > 8:
                return True, f"Reversal: Lost {drawdown_pct*100:.0f}% ({drawdown_abs:.1f}p) from peak ({self.peak_profit:.1f}p → {profit_pips:.1f}p), trend: {trend_strength:.2f}"
        
        # Check for reversal candles (FIXED: More patterns)
        # reversal_pattern = self._detect_reversal_candles(market_data)
        # if reversal_pattern:
        #     return True, f"Reversal pattern detected: {reversal_pattern}"
        
        # Check if analyzer detects opposite signal
        if self.analyzer and 'signal' in market_data:
            current_signal = market_data['signal']
            # If we're in BUY but analyzer says SELL (or vice versa), consider exiting
            if (self.action == 'BUY' and current_signal == 'SELL') or \
               (self.action == 'SELL' and current_signal == 'BUY'):
                return True, f"Analyzer detects opposite signal: {current_signal}"
        
        return False, None
    
    def _feeling_strong_momentum(self, momentum: float, market_data: Dict, trend_strength: float = 0.5) -> Tuple[bool, str]:
        """
        FEELING 3: "Let it run!"
        Strong momentum + strong trend = keep position open
        ENHANCED: Uses trend strength from analyzer
        """
        # Check volume surge
        volume_surge = False
        if 'volumes' in market_data and len(market_data['volumes']) >= 10:
            volumes = market_data['volumes']
            avg_volume = np.mean(volumes[-10:])
            current_volume = volumes[-1]
            volume_surge = current_volume > avg_volume * 1.3
        
        # Strong momentum + strong trend = definitely let it run
        if momentum > self.strong_momentum_threshold and trend_strength > 0.6:
            reason = f"Strong momentum ({momentum:.2f}) + strong trend ({trend_strength:.2f})"
            if volume_surge:
                reason += " + volume surge"
            return True, reason
        
        # Moderate momentum but very strong trend = still let it run
        if momentum > 0.6 and trend_strength > 0.75:
            return True, f"Moderate momentum ({momentum:.2f}) but very strong trend ({trend_strength:.2f})"
        
        return False, None
    
    def _detect_reversal_candles(self, market_data: Dict) -> Optional[str]:
        """
        Detect reversal candlestick patterns
        FIXED: Now detects more patterns and returns pattern name
        """
        if 'open_prices' not in market_data or 'close_prices' not in market_data:
            return None
        
        if len(market_data['close_prices']) < 3:
            return None
        
        opens = market_data['open_prices'][-3:]
        closes = market_data['close_prices'][-3:]
        highs = market_data.get('high_prices', closes)[-3:]
        lows = market_data.get('low_prices', closes)[-3:]
        
        # For BUY positions, look for bearish reversal
        if self.action == 'BUY':
            # Bearish engulfing
            if closes[-2] > opens[-2] and closes[-1] < opens[-1]:
                if opens[-1] > closes[-2] and closes[-1] < opens[-2]:
                    return "Bearish Engulfing"
            
            # Shooting star
            body = abs(closes[-1] - opens[-1])
            upper_shadow = highs[-1] - max(opens[-1], closes[-1])
            lower_shadow = min(opens[-1], closes[-1]) - lows[-1]
            if upper_shadow > body * 2 and lower_shadow < body * 0.3:
                return "Shooting Star"
            
            # Evening star (3-candle pattern)
            if (closes[-3] > opens[-3] and  # First candle bullish
                abs(closes[-2] - opens[-2]) < body * 0.3 and  # Second candle small
                closes[-1] < opens[-1] and  # Third candle bearish
                closes[-1] < (opens[-3] + closes[-3]) / 2):  # Closes below midpoint
                return "Evening Star"
            
            # Dark cloud cover
            if (closes[-2] > opens[-2] and  # Previous bullish
                opens[-1] > closes[-2] and  # Gap up
                closes[-1] < opens[-1] and  # Current bearish
                closes[-1] < (opens[-2] + closes[-2]) / 2):  # Closes below midpoint
                return "Dark Cloud Cover"
        
        # For SELL positions, look for bullish reversal
        else:
            # Bullish engulfing
            if closes[-2] < opens[-2] and closes[-1] > opens[-1]:
                if opens[-1] < closes[-2] and closes[-1] > opens[-2]:
                    return "Bullish Engulfing"
            
            # Hammer
            body = abs(closes[-1] - opens[-1])
            upper_shadow = highs[-1] - max(opens[-1], closes[-1])
            lower_shadow = min(opens[-1], closes[-1]) - lows[-1]
            if lower_shadow > body * 2 and upper_shadow < body * 0.3:
                return "Hammer"
            
            # Morning star (3-candle pattern)
            if (closes[-3] < opens[-3] and  # First candle bearish
                abs(closes[-2] - opens[-2]) < body * 0.3 and  # Second candle small
                closes[-1] > opens[-1] and  # Third candle bullish
                closes[-1] > (opens[-3] + closes[-3]) / 2):  # Closes above midpoint
                return "Morning Star"
            
            # Piercing pattern
            if (closes[-2] < opens[-2] and  # Previous bearish
                opens[-1] < closes[-2] and  # Gap down
                closes[-1] > opens[-1] and  # Current bullish
                closes[-1] > (opens[-2] + closes[-2]) / 2):  # Closes above midpoint
                return "Piercing Pattern"
        
        return None
    
    def _make_decision(self, feeling_enough, feeling_reversal, feeling_strong, 
                       profit_pips: float, momentum: float, remaining_rr: float,
                       time_exit, session_exit, breakeven_action, partial_exit_action,
                       trailing_action, current_price: float, current_atr: float,
                       current_profit_usd: float = None) -> Dict:
        """
        Make final exit decision based on all feelings and conditions.
        FIXED: Respects custom SL/TP levels when set by user
        FIXED: Returns complete action dict with SL/TP levels
        
        Priority (UPDATED for custom levels):
        0. Custom SL/TP hit (HIGHEST - user's explicit levels)
        1. Session/Weekend exit (protect from gaps)
        2. Stop loss hit (protect capital)
        3. Reversal detected (protect profit)
        4. Time-based exit (strategy-specific)
        5. Partial exit opportunity (only if not using custom levels)
        6. Breakeven opportunity (only if not using custom levels)
        7. Trailing stop opportunity (only if not using custom levels)
        8. Enough profit + weak momentum
        9. Poor remaining R:R
        10. Strong momentum (hold)
        11. Hold (default)
        """
        # ============================================================================
        # PRIORITY 0: Custom SL/TP hit (HIGHEST PRIORITY - respect user's levels)
        # ============================================================================
        # FIXED: Use actual MT5 profit instead of calculating from pips
        if self.use_custom_levels and current_profit_usd is not None:
            # Use the ACTUAL profit from MT5 position (most accurate)
            profit_usd = current_profit_usd
            
            # DETAILED LOGGING FOR CUSTOM TP/SL MONITORING
            import logging
            logger = logging.getLogger(__name__)
            
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
                    return {
                        'decision': 'EXIT_FULL',
                        'reason': f"Custom TP hit: ${profit_usd:.2f} >= ${self.custom_tp_usd}",
                        'confidence': 0.99,
                        'remaining_rr': remaining_rr
                    }
            
            # Check custom SL
            if self.custom_sl_usd:
                distance_to_sl = profit_usd + self.custom_sl_usd
                sl_hit = profit_usd <= -self.custom_sl_usd
                
                logger.critical(f"[AGENT_CUSTOM_SL] {self.symbol} | "
                              f"Limit=-${self.custom_sl_usd} | Current=${profit_usd:.2f} | "
                              f"Distance=${distance_to_sl:.2f} | Hit={sl_hit}")
                
                if sl_hit:
                    logger.critical(f"🛑 [AGENT_CUSTOM_SL_TRIGGERED] {self.symbol}: ${profit_usd:.2f} <= -${self.custom_sl_usd}")
                    print(f"   🛑 AGENT CUSTOM SL HIT: ${profit_usd:.2f} <= -${self.custom_sl_usd}")
                    return {
                        'decision': 'EXIT_FULL',
                        'reason': f"Custom SL hit: ${profit_usd:.2f} <= -${self.custom_sl_usd}",
                        'confidence': 0.99,
                        'remaining_rr': 0.0
                    }
        
        # PRIORITY 1: Session/Weekend exit
        if session_exit[0]:
            return {
                'decision': 'EXIT_FULL',
                'reason': session_exit[1],
                'confidence': 0.95,
                'remaining_rr': remaining_rr
            }
        
        # PRIORITY 2: Stop loss hit (FIXED: Uses initial SL, not hardcoded -15)
        if self.initial_sl and not self.use_custom_levels:  # Only use bot SL if not using custom
            sl_hit = False
            if self.action == 'BUY':
                sl_hit = current_price <= self.current_sl
            else:
                sl_hit = current_price >= self.current_sl
            
            if sl_hit:
                return {
                    'decision': 'EXIT_FULL',
                    'reason': f"Stop loss hit at {self.current_sl:.5f}",
                    'confidence': 0.99,
                    'remaining_rr': 0.0
                }
        elif not self.use_custom_levels:
            # Fallback: Use ATR-based stop
            atr_pips = self._atr_to_pips(current_atr)
            max_loss = atr_pips * 2.0  # 2x ATR max loss
            if profit_pips < -max_loss:
                return {
                    'decision': 'EXIT_FULL',
                    'reason': f"Max loss exceeded ({profit_pips:.1f}p < -{max_loss:.1f}p)",
                    'confidence': 0.99,
                    'remaining_rr': 0.0
                }
        
        # If the user specified custom TP/SL, do NOT let the agent's feelings interfere.
        # Just hold and wait for the custom TP/SL to be hit.
        if self.use_custom_levels:
            return {
                'decision': 'HOLD',
                'reason': f"Waiting for custom levels (P/L: {profit_pips:.1f}p, ${current_profit_usd:.2f})",
                'confidence': 0.50,
                'remaining_rr': remaining_rr
            }
        
        # PRIORITY 3: Reversal detected - EXIT NOW (slightly more aggressive now)
        if feeling_reversal[0]:
            return {
                'decision': 'EXIT_FULL',
                'reason': feeling_reversal[1],
                'confidence': 0.90,
                'remaining_rr': remaining_rr
            }
        
        # PRIORITY 4: Time-based exit
        if time_exit[0]:
            return {
                'decision': 'EXIT_FULL',
                'reason': time_exit[1],
                'confidence': 0.85,
                'remaining_rr': remaining_rr
            }
        
        # PRIORITY 5-7: Advanced features (ONLY if not using custom levels)
        if not self.use_custom_levels:
            # PRIORITY 5: Partial exit opportunity
            if partial_exit_action:
                return partial_exit_action
            
            # PRIORITY 6: Breakeven opportunity
            if breakeven_action:
                return breakeven_action
            
            # PRIORITY 7: Trailing stop opportunity
            if trailing_action:
                return trailing_action
        
        # PRIORITY 8: Good profit + weak momentum - EXIT NOW
        if feeling_enough[0] and not feeling_strong[0]:
            return {
                'decision': 'EXIT_FULL',
                'reason': feeling_enough[1],
                'confidence': 0.80,
                'remaining_rr': remaining_rr
            }
        
        # PRIORITY 9: Poor remaining R:R - EXIT NOW
        if remaining_rr < 0.5 and profit_pips > 0:
            return {
                'decision': 'EXIT_FULL',
                'reason': f"Poor remaining R:R ({remaining_rr:.2f}), lock in profit ({profit_pips:.1f}p)",
                'confidence': 0.75,
                'remaining_rr': remaining_rr
            }
        
        # PRIORITY 10: Strong momentum - HOLD (but with trailing stop)
        if feeling_strong[0]:
            return {
                'decision': 'HOLD',
                'reason': feeling_strong[1],
                'confidence': 0.85,
                'remaining_rr': remaining_rr
            }
        
        # PRIORITY 11: Correlation risk check
        if len(self.correlated_positions) >= 3:
            # Too many correlated positions, consider exiting if profit is decent
            if profit_pips > 5:
                return {
                    'decision': 'EXIT_FULL',
                    'reason': f"High correlation risk ({len(self.correlated_positions)} correlated positions), take profit",
                    'confidence': 0.70,
                    'remaining_rr': remaining_rr
                }
        
        # DEFAULT: Hold and monitor
        return {
            'decision': 'HOLD',
            'reason': f"Monitoring (P/L: {profit_pips:.1f}p, momentum: {momentum:.2f}, R:R: {remaining_rr:.2f})",
            'confidence': 0.50,
            'remaining_rr': remaining_rr
        }
    
    def get_stats(self) -> Dict:
        """Get agent statistics for logging"""
        time_in_trade = (datetime.now() - self.entry_time).total_seconds() / 60  # minutes
        
        return {
            'symbol': self.symbol,
            'strategy': self.strategy,
            'time_in_trade': round(time_in_trade, 1),
            'peak_profit': round(self.peak_profit, 1),
            'lowest_profit': round(self.lowest_profit, 1),
            'decisions_made': len(self.decision_history),
            'breakeven_moved': self.breakeven_moved,
            'partial_exit_done': self.partial_exit_done,
            'remaining_lot_size': self.remaining_lot_size,
            'correlated_positions': len(self.correlated_positions)
        }


class AgentManager:
    """
    Manages all position agents.
    Creates agents for new positions, updates them, and removes them when positions close.
    ENHANCED: Integrates with MarketAnalyzer for sophisticated analysis.
    FIXED: Uses ticket as key to support pyramiding (multiple positions per symbol).
    FIXED: Passes SL/TP and ATR to agents
    """
    
    def __init__(self, analyzer=None):
        self.agents = {}  # ticket -> PositionAgent
        self.analyzer = analyzer  # Reference to MarketAnalyzer
    
    def create_agent(self, ticket: int, symbol: str, action: str, entry_price: float, 
                     lot_size: float, strategy: str, initial_sl: float = None, 
                     initial_tp: float = None, atr: float = None, spread: float = None,
                     custom_sl_usd: float = None, custom_tp_usd: float = None) -> PositionAgent:
        """
        Create a new agent for a position with analyzer integration
        FIXED: Now accepts SL/TP and ATR from trade execution
        FIXED: Now accepts custom SL/TP in USD for user-defined levels
        """
        agent = PositionAgent(
            symbol, action, entry_price, lot_size, strategy, 
            self.analyzer, initial_sl, initial_tp, atr, spread,
            custom_sl_usd, custom_tp_usd
        )
        self.agents[ticket] = agent
        
        # Show custom levels if set
        if custom_sl_usd or custom_tp_usd:
            sl_info = f"${custom_sl_usd}" if custom_sl_usd else "None"
            tp_info = f"${custom_tp_usd}" if custom_tp_usd else "None"
            print(f"   🤖 Agent created for {symbol} {action} position #{ticket} | Strategy: {strategy} | Custom SL: {sl_info} | Custom TP: {tp_info}")
        else:
            print(f"   🤖 Agent created for {symbol} {action} position #{ticket} | Strategy: {strategy} | SL: {initial_sl} | TP: {initial_tp}")
        return agent
    
    def get_agent(self, ticket: int) -> Optional[PositionAgent]:
        """Get agent for a ticket"""
        return self.agents.get(ticket)
    
    def remove_agent(self, ticket: int):
        """Remove agent when position closes"""
        if ticket in self.agents:
            agent = self.agents[ticket]
            stats = agent.get_stats()
            print(f"   🤖 Agent retired: {agent.symbol} #{ticket} | Time: {stats['time_in_trade']}m | Peak: {stats['peak_profit']}p | BE: {stats['breakeven_moved']} | Partial: {stats['partial_exit_done']}")
            del self.agents[ticket]
    
    def update_all_agents(self) -> Dict[int, Dict]:
        """
        Update all agents and get their decisions.
        FIXED: Returns complete action dicts instead of tuples
        
        Returns:
            {ticket: decision_dict}
        """
        decisions = {}
        
        # Get all positions for correlation awareness
        all_positions = self._get_all_positions()
        
        for ticket, agent in list(self.agents.items()):
            try:
                # Get current price
                tick = mt5.symbol_info_tick(agent.symbol)
                if tick is None:
                    continue
                
                current_price = tick.bid if agent.action == 'BUY' else tick.ask
                
                # Get market data for analysis
                market_data = self._get_market_data(agent.symbol)
                
                # Get other positions for correlation check
                other_positions = [p for p in all_positions if p['ticket'] != ticket]
                
                # Get exact profit for this position
                this_pos = next((p for p in all_positions if p['ticket'] == ticket), None)
                current_profit_usd = this_pos['profit'] if this_pos else None
                
                # Update agent and get decision (FIXED: Now returns dict)
                decision = agent.update(current_price, market_data, other_positions, current_profit_usd)
                
                decisions[ticket] = decision
            except Exception as e:
                # If agent update fails, continue with other agents
                print(f"   ⚠ Agent update failed for ticket #{ticket}: {e}")
                continue
        
        return decisions
    
    def _get_all_positions(self) -> List[Dict]:
        """
        NEW: Get all open positions for correlation analysis
        """
        positions = mt5.positions_get()
        if positions is None:
            return []
        
        return [
            {
                'ticket': pos.ticket,
                'symbol': pos.symbol,
                'type': 'BUY' if pos.type == mt5.ORDER_TYPE_BUY else 'SELL',
                'volume': pos.volume,
                'profit': pos.profit
            }
            for pos in positions
        ]
    
    def _get_market_data(self, symbol: str) -> Dict:
        """
        Get market data for agent analysis
        ENHANCED: Uses MarketAnalyzer for sophisticated indicators
        """
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 20)
        
        if rates is None or len(rates) == 0:
            return {}
        
        market_data = {
            'close_prices': [r['close'] for r in rates],
            'open_prices': [r['open'] for r in rates],
            'high_prices': [r['high'] for r in rates],
            'low_prices': [r['low'] for r in rates],
            'volumes': [r['tick_volume'] for r in rates]
        }
        
        # If analyzer is available, get sophisticated indicators
        if self.analyzer:
            try:
                # Get current signal from analyzer (includes all indicators)
                signal = self.analyzer.get_market_signals(symbol)
                
                if signal and 'indicators' in signal:
                    market_data['indicators'] = signal['indicators']
                    market_data['signal'] = signal.get('signal', 'NONE')
                    market_data['confidence'] = signal.get('confidence', 0.5)
                    
            except Exception as e:
                # If analyzer fails, continue with basic data
                pass
        
        return market_data
    
    def get_all_stats(self) -> Dict:
        """Get statistics for all agents"""
        return {ticket: agent.get_stats() for ticket, agent in self.agents.items()}
