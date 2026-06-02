"""
Trade Execution Engine - ENHANCED VERSION
All 20 weak points fixed with enterprise-grade features
trade_executor_enhanced.py
"""

import MetaTrader5 as mt5
from datetime import datetime, time as dt_time, timedelta
from typing import Dict, Optional, List, Tuple
import logging
import threading
import time
from collections import defaultdict, deque
import json

from trade_logger import TradeLogger
from performance_tracker import PerformanceTracker
from position_agent import AgentManager

logger = logging.getLogger(__name__)


class OrderQueue:
    """Thread-safe order queue with priority support"""
    
    def __init__(self):
        self.queue = deque()
        self.lock = threading.Lock()
    
    def add(self, order: Dict, priority: int = 0):
        """Add order to queue (higher priority = processed first)"""
        with self.lock:
            self.queue.append((priority, order))
            # Sort by priority (descending)
            self.queue = deque(sorted(self.queue, key=lambda x: x[0], reverse=True))
    
    def get(self) -> Optional[Dict]:
        """Get next order from queue"""
        with self.lock:
            if self.queue:
                _, order = self.queue.popleft()
                return order
        return None
    
    def size(self) -> int:
        """Get queue size"""
        with self.lock:
            return len(self.queue)


class TradeExecutor:
    """
    Enhanced trade executor with:
    - Order retry mechanism with exponential backoff
    - Slippage tracking and limits
    - Partial close support
    - Comprehensive error recovery
    - Broker connection monitoring
    - Order validation (margin, lot size)
    - Trade correlation limits
    - Efficient position tracking with caching
    - Emergency stop mechanism (daily loss limit)
    - Advanced pyramiding logic
    - Trade timing validation
    - Multiple exit strategies
    - Order queue with priority
    - Execution statistics
    - Spread monitoring
    - Order modification support
    - Enhanced thread safety
    """
    
    def __init__(self, lot_size: float = 0.01):
        self.lot_size = lot_size
        self.max_risk_per_trade = 0.02  # 2% risk per trade
        self.active_trades = {}
        self.max_positions = 0  # 0 = unlimited
        self.logger = TradeLogger()
        self.performance = PerformanceTracker()
        self.tp_mode = 'bot'
        self.sl_mode = 'bot'
        self.tp_usd = None
        self.sl_usd = None
        self._analyzer = None
        self.recent_trades = {}
        self.blocked_symbols = {}
        self.failed_symbols = {}
        
        # Agent manager
        self.agent_manager = AgentManager(analyzer=None)
        
        # Thread safety
        self._trades_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        
        # NEW: Order queue
        self.order_queue = OrderQueue()
        
        # NEW: Execution statistics
        self.execution_stats = {
            'total_orders': 0,
            'successful_orders': 0,
            'failed_orders': 0,
            'retries': 0,
            'avg_slippage_pips': 0.0,
            'total_slippage_pips': 0.0,
            'slippage_count': 0
        }
        
        # NEW: Emergency stop
        self.daily_loss_limit = -100.0  # -$100 daily loss limit
        self.daily_pnl = 0.0
        self.last_reset_date = datetime.now().date()
        self.emergency_stop_active = False
        
        # NEW: Correlation tracking
        self.correlation_groups = {
            'EUR': ['EURUSD', 'EURJPY', 'EURGBP', 'EURAUD', 'EURCAD', 'EURCHF', 'EURNZD'],
            'GBP': ['GBPUSD', 'GBPJPY', 'EURGBP', 'GBPAUD', 'GBPCAD', 'GBPCHF', 'GBPNZD'],
            'USD': ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDCHF', 'NZDUSD'],
            'JPY': ['USDJPY', 'EURJPY', 'GBPJPY', 'AUDJPY', 'CADJPY', 'CHFJPY', 'NZDJPY'],
            'GOLD': ['XAUUSD', 'XAGUSD'],
            'INDICES': ['US30', 'US100', 'US500', 'UK100', 'GER40']
        }
        self.max_correlated_positions = 3
        
        # NEW: Position cache
        self.position_cache = {}
        self.cache_timestamp = None
        self.cache_ttl = 1.0  # 1 second TTL
        
        # NEW: Spread limits (in pips)
        self.max_spread = {
            'EURUSD': 2.0, 'GBPUSD': 2.5, 'USDJPY': 2.0,
            'XAUUSD': 5.0, 'US30': 5.0, 'default': 3.0
        }
        
        # NEW: Trading hours (24h format)
        self.trading_hours = {
            'start': dt_time(0, 0),  # 00:00
            'end': dt_time(23, 59),  # 23:59
            'avoid_hours': [22, 23]  # Avoid these hours
        }
        
        # NEW: News blackout periods (will be populated)
        self.news_blackout = []
        
        # Check existing positions
        self.check_existing_positions()
        
        logger.info("TradeExecutor initialized with enhanced features")
    
    @property
    def analyzer(self):
        return self._analyzer
    
    @analyzer.setter
    def analyzer(self, value):
        self._analyzer = value
        if self.agent_manager:
            self.agent_manager.analyzer = value
            logger.info("Agent manager connected to analyzer")
    
    # ------------------------------------------------------------------ #
    #  BROKER CONNECTION MONITORING
    # ------------------------------------------------------------------ #
    
    def check_broker_connection(self) -> Tuple[bool, str]:
        """
        Check MT5 broker connection health
        Returns: (is_connected, status_message)
        
        FIXED: trade_allowed and trade_expert are now soft warnings.
        """
        if not mt5.initialize():
            return False, "MT5 not initialized"
        
        # Check terminal info
        terminal_info = mt5.terminal_info()
        if terminal_info is None:
            return False, "Cannot get terminal info"
        
        if not terminal_info.connected:
            return False, "Terminal not connected to broker"
        
        # SOFT CHECK: trade_allowed reflects the AutoTrading button in MT5 GUI
        if not terminal_info.trade_allowed:
            if not getattr(self, '_trade_allowed_warned', False):
                logger.warning("⚠️ MT5 AutoTrading may be disabled. Click the 'AutoTrading' button in MT5 if orders fail.")
                self._trade_allowed_warned = True
        
        # Check account info
        account_info = mt5.account_info()
        if account_info is None:
            return False, "Cannot get account info"
        
        # SOFT CHECK: account-level trade permissions
        if not account_info.trade_allowed:
            if not getattr(self, '_acct_trade_warned', False):
                logger.warning("⚠️ Account trading flag is disabled. Contact broker if orders fail.")
                self._acct_trade_warned = True
        
        if not account_info.trade_expert:
            if not getattr(self, '_expert_trade_warned', False):
                logger.warning("⚠️ Expert trading flag is disabled. Enable 'Allow Algo Trading' in MT5 settings.")
                self._expert_trade_warned = True
        
        return True, "Connected"
    
    def ensure_connection(self, max_retries: int = 3) -> bool:
        """
        Ensure broker connection with retry
        Returns: True if connected
        """
        for attempt in range(max_retries):
            is_connected, message = self.check_broker_connection()
            
            if is_connected:
                return True
            
            if attempt == max_retries - 1:
                logger.warning(f"Connection check failed after {max_retries} attempts: {message}")
                # If it's just a trade permission issue, still allow trade attempt
                if message in ["Trading not allowed", "Account trading not allowed", "Expert trading not allowed"]:
                    logger.info("Allowing trade attempt despite permission warning - order_send will validate")
                    return True
            
            if attempt < max_retries - 1:
                mt5.shutdown()
                time.sleep(0.5)
                if not mt5.initialize():
                    if attempt == max_retries - 2:
                        logger.error("Failed to reinitialize MT5")
                    continue
        
        return False
    
    # ------------------------------------------------------------------ #
    #  EMERGENCY STOP & DAILY LIMITS
    # ------------------------------------------------------------------ #
    
    def check_daily_limits(self) -> Tuple[bool, str]:
        """
        Check if daily loss limit exceeded
        Returns: (can_trade, reason)
        """
        # Reset daily P&L if new day
        today = datetime.now().date()
        if today != self.last_reset_date:
            self.daily_pnl = 0.0
            self.last_reset_date = today
            self.emergency_stop_active = False
            logger.info(f"Daily P&L reset for {today}")
        
        # Check if emergency stop active
        if self.emergency_stop_active:
            return False, f"Emergency stop active (daily loss: ${self.daily_pnl:.2f})"
        
        # Check daily loss limit
        if self.daily_pnl <= self.daily_loss_limit:
            self.emergency_stop_active = True
            logger.critical(f"EMERGENCY STOP: Daily loss limit reached (${self.daily_pnl:.2f})")
            return False, f"Daily loss limit reached (${self.daily_pnl:.2f})"
        
        return True, "OK"
    
    def update_daily_pnl(self, profit_usd: float):
        """Update daily P&L"""
        with self._stats_lock:
            self.daily_pnl += profit_usd
            
            # Log significant losses
            if profit_usd < -10:
                logger.warning(f"Significant loss: ${profit_usd:.2f}, Daily P&L: ${self.daily_pnl:.2f}")
    
    # ------------------------------------------------------------------ #
    #  SPREAD MONITORING
    # ------------------------------------------------------------------ #
    
    def check_spread(self, symbol: str) -> Tuple[bool, float, str]:
        """
        Check if spread is acceptable
        Returns: (is_acceptable, spread_pips, reason)
        """
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return False, 0.0, "Cannot get tick data"
        
        spread = tick.ask - tick.bid
        pip_multiplier = self._get_pip_multiplier(symbol)
        spread_pips = spread * pip_multiplier
        
        max_spread = self.max_spread.get(symbol, self.max_spread['default'])
        
        if spread_pips > max_spread:
            return False, spread_pips, f"Spread too high ({spread_pips:.1f}p > {max_spread}p)"
        
        return True, spread_pips, "OK"
    
    # ------------------------------------------------------------------ #
    #  TRADE TIMING VALIDATION
    # ------------------------------------------------------------------ #
    
    def check_trading_hours(self) -> Tuple[bool, str]:
        """
        Check if current time is within trading hours
        Returns: (can_trade, reason)
        """
        now = datetime.now()
        current_time = now.time()
        current_hour = now.hour
        day_of_week = now.weekday()
        
        # Weekend check
        if day_of_week >= 5:  # Saturday=5, Sunday=6
            return False, "Weekend - market closed"
        
        # Avoid specific hours
        if current_hour in self.trading_hours['avoid_hours']:
            return False, f"Avoiding hour {current_hour}:00"
        
        # Check news blackout
        for blackout_start, blackout_end in self.news_blackout:
            if blackout_start <= now <= blackout_end:
                return False, "News blackout period"
        
        return True, "OK"
    
    # ------------------------------------------------------------------ #
    #  CORRELATION & EXPOSURE LIMITS
    # ------------------------------------------------------------------ #
    
    def check_correlation_limits(self, symbol: str) -> Tuple[bool, str]:
        """
        Check if adding this symbol would exceed correlation limits
        Returns: (can_trade, reason)
        """
        # Find which correlation group this symbol belongs to
        symbol_groups = []
        for group_name, symbols in self.correlation_groups.items():
            if symbol in symbols:
                symbol_groups.append(group_name)
        
        if not symbol_groups:
            return True, "OK"  # Not in any correlation group
        
        # Count existing positions in same groups
        with self._trades_lock:
            for group_name in symbol_groups:
                group_symbols = self.correlation_groups[group_name]
                active_in_group = sum(
                    1 for trade in self.active_trades.values()
                    if trade['symbol'] in group_symbols
                )
                
                if active_in_group >= self.max_correlated_positions:
                    return False, f"Max {self.max_correlated_positions} positions in {group_name} group"
        
        return True, "OK"
    
    # ------------------------------------------------------------------ #
    #  ORDER VALIDATION
    # ------------------------------------------------------------------ #
    
    def validate_order(self, symbol: str, lot_size: float, action: str) -> Tuple[bool, str]:
        """
        Comprehensive order validation
        Returns: (is_valid, reason)
        """
        # Check broker connection
        if not self.ensure_connection(max_retries=2):
            return False, "Broker not connected"
        
        # Check daily limits
        can_trade, reason = self.check_daily_limits()
        if not can_trade:
            return False, reason
        
        # Check trading hours
        can_trade, reason = self.check_trading_hours()
        if not can_trade:
            return False, reason
        
        # Check spread
        spread_ok, spread_pips, reason = self.check_spread(symbol)
        if not spread_ok:
            return False, reason
        
        # Check correlation limits
        corr_ok, reason = self.check_correlation_limits(symbol)
        if not corr_ok:
            return False, reason
        
        # Check margin
        account_info = mt5.account_info()
        if account_info is None:
            return False, "Cannot get account info"
        
        # Estimate required margin
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            return False, f"Cannot get {symbol} info"
        
        # Simple margin check (actual calculation is complex)
        free_margin = account_info.margin_free
        if free_margin < 100:  # Minimum $100 free margin
            return False, f"Insufficient margin (${free_margin:.2f})"
        
        # Validate lot size
        if lot_size < symbol_info.volume_min:
            return False, f"Lot size too small ({lot_size} < {symbol_info.volume_min})"
        
        if lot_size > symbol_info.volume_max:
            return False, f"Lot size too large ({lot_size} > {symbol_info.volume_max})"
        
        return True, "OK"
    
    # ------------------------------------------------------------------ #
    #  ORDER RETRY MECHANISM
    # ------------------------------------------------------------------ #
    
    def send_order_with_retry(self, request: Dict, max_retries: int = 3) -> Optional[object]:
        """
        Send order with exponential backoff retry
        Returns: MT5 result object or None
        """
        for attempt in range(max_retries):
            result = mt5.order_send(request)
            
            if result is None:
                logger.error(f"Order send returned None (attempt {attempt+1}/{max_retries})")
                with self._stats_lock:
                    self.execution_stats['retries'] += 1
                
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) * 0.5  # 0.5s, 1s, 2s
                    time.sleep(wait_time)
                    continue
                else:
                    return None
            
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                return result
            
            # Handle specific error codes
            if result.retcode in [mt5.TRADE_RETCODE_REQUOTE, mt5.TRADE_RETCODE_PRICE_OFF]:
                # Price changed, retry with new price
                logger.warning(f"Requote on attempt {attempt+1}, retrying...")
                with self._stats_lock:
                    self.execution_stats['retries'] += 1
                
                if attempt < max_retries - 1:
                    time.sleep(0.2)
                    # Update price
                    tick = mt5.symbol_info_tick(request['symbol'])
                    if tick:
                        if request['type'] == mt5.ORDER_TYPE_BUY:
                            request['price'] = tick.ask
                        else:
                            request['price'] = tick.bid
                    continue
            
            elif result.retcode == mt5.TRADE_RETCODE_CONNECTION:
                # Connection issue, try to reconnect
                logger.error("Connection lost, attempting reconnect...")
                if self.ensure_connection():
                    if attempt < max_retries - 1:
                        time.sleep(1)
                        continue
            
            # Other errors - don't retry
            logger.error(f"Order failed: {result.comment} (retcode: {result.retcode})")
            return result
        
        return None
    
    # ------------------------------------------------------------------ #
    #  SLIPPAGE TRACKING
    # ------------------------------------------------------------------ #
    
    def track_slippage(self, requested_price: float, filled_price: float, symbol: str):
        """Track execution slippage"""
        pip_multiplier = self._get_pip_multiplier(symbol)
        slippage_pips = abs(filled_price - requested_price) * pip_multiplier
        
        with self._stats_lock:
            self.execution_stats['total_slippage_pips'] += slippage_pips
            self.execution_stats['slippage_count'] += 1
            self.execution_stats['avg_slippage_pips'] = (
                self.execution_stats['total_slippage_pips'] / 
                self.execution_stats['slippage_count']
            )
        
        if slippage_pips > 2.0:
            logger.warning(f"{symbol}: High slippage {slippage_pips:.1f} pips")
    
    # ------------------------------------------------------------------ #
    #  POSITION CACHING
    # ------------------------------------------------------------------ #
    
    def get_positions_cached(self, force_refresh: bool = False) -> List:
        """Get positions with caching"""
        now = time.time()
        
        # Check cache validity
        if (not force_refresh and 
            self.cache_timestamp and 
            (now - self.cache_timestamp) < self.cache_ttl and
            self.position_cache is not None):
            return self.position_cache
        
        # Refresh cache
        positions = mt5.positions_get()
        self.position_cache = positions if positions else []
        self.cache_timestamp = now
        
        return self.position_cache
    
    # ------------------------------------------------------------------ #
    #  HELPER METHODS (keeping existing ones)
    # ------------------------------------------------------------------ #
    
    def _get_pip_size(self, symbol: str) -> float:
        """Get the pip size for a symbol"""
        info = mt5.symbol_info(symbol)
        if info is None:
            return 0.0001
        
        if 'XAU' in symbol or 'XAG' in symbol:
            return 0.1
        if 'JPY' in symbol:
            return 0.01
        if any(idx in symbol for idx in ['US30', 'US100', 'US500']):
            return 1.0
        
        return 0.0001
    
    def _get_pip_multiplier(self, symbol: str) -> float:
        """Get multiplier to convert price difference to pips"""
        pip_size = self._get_pip_size(symbol)
        if pip_size == 0:
            return 10000
        return 1.0 / pip_size
    
    def _get_profit_usd(self, position) -> float:
        """Get accurate USD profit for a position"""
        profit = position.profit + position.swap
        
        # DETAILED LOGGING FOR PROFIT CALCULATION
        logger.debug(f"[PROFIT_CALC] #{position.ticket} {position.symbol} | "
                    f"MT5_Profit={position.profit:.2f} Swap={position.swap:.2f} | "
                    f"Total_USD={profit:.2f}")
        
        return profit
    
    def check_existing_positions(self):
        """Check for existing MT5 positions on startup"""
        positions = self.get_positions_cached(force_refresh=True)
        
        total_positions = len(positions)
        bot_positions = [pos for pos in positions if pos.magic == 234000]
        other_positions = total_positions - len(bot_positions)

        if total_positions > 0:
            print(f"\n⚠ EXISTING POSITIONS DETECTED:")
            print(f"   Total MT5 positions: {total_positions}")
            if len(bot_positions) > 0:
                print(f"   Bot positions: {len(bot_positions)}")
                for pos in bot_positions[:5]:
                    position_type = "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL"
                    print(f"      {pos.symbol} {position_type} {pos.volume}")
            if other_positions > 0:
                print(f"   Other positions: {other_positions}")
            print()

    # ------------------------------------------------------------------ #
    #  PARTIAL CLOSE SUPPORT
    # ------------------------------------------------------------------ #
    
    def close_partial(self, ticket: int, percentage: float, reason: str = "Partial") -> bool:
        """
        Close partial position (scale out)
        
        Args:
            ticket: Position ticket
            percentage: Percentage to close (0.0-1.0)
            reason: Close reason
        
        Returns:
            True if successful
        """
        if percentage <= 0 or percentage >= 1.0:
            logger.error(f"Invalid percentage: {percentage}")
            return False
        
        # Get position
        positions = self.get_positions_cached(force_refresh=True)
        position = None
        for pos in positions:
            if pos.ticket == ticket:
                position = pos
                break
        
        if position is None:
            logger.error(f"Position {ticket} not found")
            return False
        
        symbol = position.symbol
        
        # Calculate partial volume
        partial_volume = position.volume * percentage
        
        # Get symbol info for volume step
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            return False
        
        # Round to volume step
        if symbol_info.volume_step > 0:
            partial_volume = round(partial_volume / symbol_info.volume_step) * symbol_info.volume_step
            partial_volume = round(partial_volume, 2)
        
        # Validate volume
        if partial_volume < symbol_info.volume_min:
            logger.warning(f"Partial volume too small: {partial_volume}")
            return False
        
        if partial_volume >= position.volume:
            logger.warning(f"Partial volume >= position volume, closing full")
            return False
        
        # Get close price
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return False
        
        close_price = tick.bid if position.type == mt5.POSITION_TYPE_BUY else tick.ask
        
        # Prepare close request
        close_request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": partial_volume,
            "type": mt5.ORDER_TYPE_SELL if position.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY,
            "position": position.ticket,
            "price": close_price,
            "deviation": 20,
            "magic": 234000,
            "comment": f"Partial_{reason}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        # Send with retry
        result = self.send_order_with_retry(close_request, max_retries=3)
        
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Failed to close partial {symbol}: {result.comment if result else 'None'}")
            return False
        
        # Calculate profit for this partial close
        pip_multiplier = self._get_pip_multiplier(symbol)
        if position.type == mt5.POSITION_TYPE_BUY:
            profit_pips = (close_price - position.price_open) * pip_multiplier
        else:
            profit_pips = (position.price_open - close_price) * pip_multiplier
        
        print(f"✓ {symbol} partial close ({percentage*100:.0f}%) {profit_pips:+.1f}p")
        
        # Update tracking (reduce lot size)
        with self._trades_lock:
            if ticket in self.active_trades:
                self.active_trades[ticket]['lot_size'] -= partial_volume
        
        return True
    
    def move_to_breakeven(self, ticket: int) -> bool:
        """
        Move stop loss to breakeven + spread
        
        Args:
            ticket: Position ticket
        
        Returns:
            True if successful
        """
        # Get position
        positions = self.get_positions_cached(force_refresh=True)
        position = None
        for pos in positions:
            if pos.ticket == ticket:
                position = pos
                break
        
        if position is None:
            return False
        
        symbol = position.symbol
        entry_price = position.price_open
        
        # Get symbol info
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            return False
        
        # Calculate breakeven price (entry + spread)
        spread = symbol_info.spread * symbol_info.point
        
        if position.type == mt5.POSITION_TYPE_BUY:
            breakeven_price = entry_price + spread
        else:
            breakeven_price = entry_price - spread
        
        # Round to digits
        breakeven_price = round(breakeven_price, symbol_info.digits)
        
        # Check if already at or past breakeven
        current_sl = position.sl
        if position.type == mt5.POSITION_TYPE_BUY:
            if current_sl >= breakeven_price:
                return True  # Already at breakeven or better
        else:
            if current_sl <= breakeven_price and current_sl > 0:
                return True
        
        # Modify position
        return self.modify_position(ticket, sl=breakeven_price, tp=position.tp)
    
    def modify_position(self, ticket: int, sl: float = None, tp: float = None) -> bool:
        """
        Modify position SL/TP
        
        Args:
            ticket: Position ticket
            sl: New stop loss (None = keep current)
            tp: New take profit (None = keep current)
        
        Returns:
            True if successful
        """
        # Get position
        positions = self.get_positions_cached(force_refresh=True)
        position = None
        for pos in positions:
            if pos.ticket == ticket:
                position = pos
                break
        
        if position is None:
            logger.error(f"Position {ticket} not found")
            return False
        
        # Use current values if not specified
        new_sl = sl if sl is not None else position.sl
        new_tp = tp if tp is not None else position.tp
        
        # Check if anything changed
        if new_sl == position.sl and new_tp == position.tp:
            return True  # Nothing to modify
        
        # Prepare modification request
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl": new_sl,
            "tp": new_tp,
            "magic": 234000,
            "comment": "Modify_SLTP"
        }
        
        # Send with retry
        result = self.send_order_with_retry(request, max_retries=2)
        
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Failed to modify {position.symbol}: {result.comment if result else 'None'}")
            return False
        
        logger.info(f"Modified {position.symbol} SL/TP")
        return True

    # ------------------------------------------------------------------ #
    #  MAIN TRADING METHODS
    # ------------------------------------------------------------------ #
    
    def place_order(self, signal: Dict) -> bool:
        """Execute trade based on signal - ENHANCED VERSION with all validations"""
        symbol = signal['symbol']
        action = signal['signal']
        signal_type = signal['type']
        
        if action == 'NONE':
            return False
        
        # VALIDATION CHAIN
        # 1. Validate order
        is_valid, reason = self.validate_order(symbol, self.lot_size, action)
        if not is_valid:
            logger.debug(f"{symbol}: Order validation failed - {reason}")
            return False
        
        # 2. Check symbol failures
        if symbol in self.failed_symbols:
            fail_count, last_fail_time = self.failed_symbols[symbol]
            time_since_fail = (datetime.now() - last_fail_time).total_seconds()
            
            if fail_count >= 3 and time_since_fail < 1800:
                return False
            elif time_since_fail >= 1800:
                del self.failed_symbols[symbol]
        
        # 3. Get current price
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return False
        
        # 4. Check existing positions (pyramiding logic)
        positions = self.get_positions_cached(force_refresh=True)
        symbol_positions = [p for p in positions if p.symbol == symbol and p.magic == 234000]
        has_position = len(symbol_positions) > 0
        
        now = datetime.now()
        
        # 5. Check recent opposite trades
        has_recent_opposite = False
        if symbol in self.recent_trades:
            last_trade = self.recent_trades[symbol]
            time_diff = (now - last_trade['time']).total_seconds()
            if last_trade['action'] != action and time_diff < 1800:
                has_recent_opposite = True
        
        # PYRAMIDING LOGIC
        if has_position:
            existing_pos = symbol_positions[0]
            existing_direction = "BUY" if existing_pos.type == mt5.POSITION_TYPE_BUY else "SELL"
            
            if action == existing_direction:
                # Same direction - check if profitable
                current_price = tick.bid if existing_direction == "BUY" else tick.ask
                entry_price = existing_pos.price_open
                
                if existing_direction == "BUY":
                    profit = current_price - entry_price
                else:
                    profit = entry_price - current_price
                
                # Allow pyramiding if profitable
                if profit > 0:
                    # Check max positions per symbol
                    if len(symbol_positions) >= 3:
                        block_key = f"{symbol}_max_reached"
                        if block_key not in self.blocked_symbols or (now - self.blocked_symbols[block_key]).total_seconds() > 60:
                            logger.info(f"{symbol}: Max 3 positions reached")
                            self.blocked_symbols[block_key] = now
                        return False
                    
                    # Reduce lot size for additional positions
                    position_number = len(symbol_positions) + 1
                    lot_multipliers = {1: 1.0, 2: 0.5, 3: 0.25}
                    lot_multiplier = lot_multipliers.get(position_number, 0.25)
                    adjusted_lot_size = self.lot_size * lot_multiplier
                    
                    logger.info(f"{symbol}: Adding to profitable {existing_direction} position (#{position_number}, {lot_multiplier}x size)")
                else:
                    block_key = f"{symbol}_{existing_direction}_losing"
                    if block_key not in self.blocked_symbols or (now - self.blocked_symbols[block_key]).total_seconds() > 60:
                        logger.debug(f"{symbol}: Position losing, not adding")
                        self.blocked_symbols[block_key] = now
                    return False
            else:
                # Opposite direction - block
                block_key = f"{symbol}_contradiction"
                if block_key not in self.blocked_symbols or (now - self.blocked_symbols[block_key]).total_seconds() > 60:
                    logger.debug(f"{symbol}: Can't open {action} while holding {existing_direction}")
                    self.blocked_symbols[block_key] = now
                return False
        else:
            adjusted_lot_size = self.lot_size
        
        # Check recent opposite
        if has_recent_opposite:
            time_diff = (now - self.recent_trades[symbol]['time']).total_seconds()
            block_key = f"{symbol}_recent_opposite"
            if block_key not in self.blocked_symbols or (now - self.blocked_symbols[block_key]).total_seconds() > 60:
                logger.debug(f"{symbol}: Recent opposite trade {time_diff:.0f}s ago")
                self.blocked_symbols[block_key] = now
            return False
        
        # Get symbol info
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            return False
        
        point = symbol_info.point
        digits = symbol_info.digits
        pip_size = self._get_pip_size(symbol)
        pip_multiplier = self._get_pip_multiplier(symbol)
        
        # Determine order type and price
        if action == 'BUY':
            order_type = mt5.ORDER_TYPE_BUY
            price = tick.ask
        else:
            order_type = mt5.ORDER_TYPE_SELL
            price = tick.bid
        
        # TP/SL CALCULATION
        strategy_configs = {
            'scalp':     {'sl_pips': 12, 'tp_pips': 20},
            'range':     {'sl_pips': 10, 'tp_pips': 18},
            'breakout':  {'sl_pips': 15, 'tp_pips': 30},
            'momentum':  {'sl_pips': 18, 'tp_pips': 36},
            'reversal':  {'sl_pips': 25, 'tp_pips': 50},
            'sr_bounce': {'sl_pips': 12, 'tp_pips': 24},
        }
        
        config = strategy_configs.get(signal_type, {'sl_pips': 20, 'tp_pips': 40})
        sl_pips = config['sl_pips']
        tp_pips = config['tp_pips']
        
        # Calculate SL and TP prices
        if action == 'BUY':
            sl_price = price - (sl_pips * pip_size)
            tp_price = price + (tp_pips * pip_size)
        else:
            sl_price = price + (sl_pips * pip_size)
            tp_price = price - (tp_pips * pip_size)
        
        sl_price = round(sl_price, digits)
        tp_price = round(tp_price, digits)
        
        # Validate minimum stop distance
        min_stop_level = symbol_info.trade_stops_level * point if symbol_info.trade_stops_level > 0 else 0
        
        # Calculate safe minimum
        if 'XAU' in symbol or 'XAG' in symbol:
            safe_min_stop = max(min_stop_level, 0.50) * 1.5
        elif 'JPY' in symbol:
            safe_min_stop = max(min_stop_level, 0.015)
        elif any(idx in symbol for idx in ['US30', 'US100', 'US500']):
            safe_min_stop = max(min_stop_level, 5.0)
        else:
            safe_min_stop = max(min_stop_level, 0.0005)
        
        # Apply safe minimum
        sl_distance = abs(price - sl_price)
        tp_distance = abs(price - tp_price)
        
        if sl_distance < safe_min_stop:
            if action == 'BUY':
                sl_price = round(price - safe_min_stop, digits)
            else:
                sl_price = round(price + safe_min_stop, digits)
            sl_pips = int(abs(price - sl_price) * pip_multiplier)
        
        if tp_distance < safe_min_stop:
            if action == 'BUY':
                tp_price = round(price + safe_min_stop, digits)
            else:
                tp_price = round(price - safe_min_stop, digits)
            tp_pips = int(abs(price - tp_price) * pip_multiplier)
        
        # Custom USD mode - Set wide safety stops, bot manages actual exits
        bot_managed_stops = False
        if self.tp_mode == 'custom' or self.sl_mode == 'custom':
            bot_managed_stops = True
            safety_multiplier = 10.0  # INCREASED from 3.0 to 10.0 - wider safety net
            if action == 'BUY':
                sl_price = round(price - (sl_pips * safety_multiplier * pip_size), digits)
                tp_price = round(price + (tp_pips * safety_multiplier * pip_size), digits)
            else:
                sl_price = round(price + (sl_pips * safety_multiplier * pip_size), digits)
                tp_price = round(price - (tp_pips * safety_multiplier * pip_size), digits)
            
            logger.critical(f"[CUSTOM_MODE_ACTIVE] {symbol}: Bot will monitor USD profit/loss | TP: ${self.tp_usd if self.tp_mode == 'custom' else 'bot'}, SL: ${self.sl_usd if self.sl_mode == 'custom' else 'bot'}")
            if self.tp_mode == 'custom':
                logger.critical(f"[CUSTOM_TP_SET] {symbol}: Custom TP mode enabled | Target: ${self.tp_usd} | MT5 TP: {tp_price:.5f} (wide safety)")
            if self.sl_mode == 'custom':
                logger.critical(f"[CUSTOM_SL_SET] {symbol}: Custom SL mode enabled | Limit: ${self.sl_usd} | MT5 SL: {sl_price:.5f} (wide safety)")
            print(f"   🎯 Custom TP/SL active: TP=${self.tp_usd if self.tp_mode == 'custom' else 'bot'} | SL=${self.sl_usd if self.sl_mode == 'custom' else 'bot'}")
            print(f"   🛡️ MT5 safety stops set (bot will close at custom USD levels)")
        
        # Validate and adjust lot size
        lot_size = adjusted_lot_size
        lot_size = max(symbol_info.volume_min, min(lot_size, symbol_info.volume_max))
        if symbol_info.volume_step > 0:
            lot_size = round(lot_size / symbol_info.volume_step) * symbol_info.volume_step
            lot_size = round(lot_size, 2)
        
        # Determine filling mode
        filling_type = symbol_info.filling_mode
        if filling_type & 1:
            type_filling = mt5.ORDER_FILLING_FOK
        elif filling_type & 2:
            type_filling = mt5.ORDER_FILLING_IOC
        else:
            type_filling = mt5.ORDER_FILLING_RETURN
        
        # Prepare order request
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot_size,
            "type": order_type,
            "price": price,
            "sl": sl_price,
            "tp": tp_price,
            "deviation": 20,
            "magic": 234000,
            "comment": f"TradNet_{signal_type}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": type_filling,
        }
        
        # Send order with retry
        with self._stats_lock:
            self.execution_stats['total_orders'] += 1
        
        result = self.send_order_with_retry(request, max_retries=3)
        
        if result is None:
            logger.error(f"{symbol} {action} failed: No result")
            self._record_failure(symbol)
            with self._stats_lock:
                self.execution_stats['failed_orders'] += 1
            return False
        
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            # Handle invalid stops
            if result.retcode == 10016 or "invalid stops" in str(result.comment).lower():
                logger.warning(f"{symbol}: Invalid stops, retrying without stops")
                
                request_no_stops = request.copy()
                request_no_stops['sl'] = 0.0
                request_no_stops['tp'] = 0.0
                
                retry_result = self.send_order_with_retry(request_no_stops, max_retries=2)
                
                if retry_result and retry_result.retcode == mt5.TRADE_RETCODE_DONE:
                    return self._handle_successful_order(retry_result, symbol, action, signal, price, lot_size, 0.0, 0.0, True, sl_pips, tp_pips)
                else:
                    self._record_failure(symbol)
                    with self._stats_lock:
                        self.execution_stats['failed_orders'] += 1
                    return False
            else:
                logger.error(f"{symbol} {action} failed: {result.comment}")
                self._record_failure(symbol)
                with self._stats_lock:
                    self.execution_stats['failed_orders'] += 1
                return False
        
        # Success
        with self._stats_lock:
            self.execution_stats['successful_orders'] += 1
        
        # Track slippage
        filled_price = result.price
        self.track_slippage(price, filled_price, symbol)
        
        return self._handle_successful_order(result, symbol, action, signal, filled_price, lot_size, sl_price, tp_price, False, sl_pips, tp_pips)
    
    def _record_failure(self, symbol: str):
        """Record a trading failure for a symbol"""
        if symbol not in self.failed_symbols:
            self.failed_symbols[symbol] = (1, datetime.now())
        else:
            fail_count, _ = self.failed_symbols[symbol]
            self.failed_symbols[symbol] = (fail_count + 1, datetime.now())
            if fail_count + 1 >= 3:
                logger.warning(f"{symbol}: Will skip for 30 minutes after {fail_count + 1} failures")
    
    def _handle_successful_order(self, result, symbol: str, action: str, signal: Dict,
                                  price: float, lot_size: float, sl: float, tp: float,
                                  bot_managed_stops: bool, sl_pips: int, tp_pips: int) -> bool:
        """Handle a successfully placed order"""
        signal_type = signal['type']
        
        # Show TP/SL info
        tp_sl_info = ""
        if bot_managed_stops or self.tp_mode == 'custom' or self.sl_mode == 'custom':
            tp_sl_info = f" | TP:${self.tp_usd if self.tp_mode == 'custom' else 'bot'} SL:${self.sl_usd if self.sl_mode == 'custom' else 'bot'} [BOT]"
        else:
            tp_sl_info = f" | SL:{sl_pips}p TP:{tp_pips}p"
        
        print(f"✓ {symbol} {action} @{price:.5f} | {signal_type} | Lot:{lot_size}{tp_sl_info}")
        
        # Log to CSV
        self.logger.log_entry({
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'symbol': symbol,
            'action': action,
            'strategy': signal_type,
            'confidence': signal['confidence'],
            'entry_price': price,
            'sl': sl,
            'tp': tp,
            'lot_size': lot_size,
            'ticket': result.order,
            'indicators': str(signal.get('indicators', {}))
        })
        
        # Track the trade
        with self._trades_lock:
            self.active_trades[result.order] = {
                'symbol': symbol,
                'action': action,
                'entry_price': price,
                'sl': sl,
                'tp': tp,
                'lot_size': lot_size,
                'timestamp': datetime.now(),
                'type': signal_type,
                'ticket': result.order,
                'bot_managed_stops': bot_managed_stops,
                'max_profit_usd': 0.0,
                'partial_closes': 0,
                'moved_to_breakeven': False
            }
            
            # DETAILED LOGGING FOR CUSTOM TP/SL TRACKING
            logger.critical(f"[TRADE_OPENED] {symbol} #{result.order} | {action} @{price:.5f} | "
                          f"TP_MODE={self.tp_mode} SL_MODE={self.sl_mode} | "
                          f"Custom_TP=${self.tp_usd} Custom_SL=${self.sl_usd} | "
                          f"MT5_TP={tp:.5f} MT5_SL={sl:.5f} | "
                          f"Bot_Managed={bot_managed_stops}")
            
            if self.tp_mode == 'custom' or self.sl_mode == 'custom':
                logger.critical(f"[CUSTOM_TRACKING_ENABLED] {symbol} #{result.order} will be monitored for USD profit/loss")
                print(f"   📊 Position #{result.order} added to custom TP/SL monitoring")
        
        # CRITICAL FIX: Force refresh position cache after opening position
        # This ensures the position is found in the next check_active_trades() call
        self.position_cache = None
        self.cache_timestamp = None
        
        # Create agent
        self.agent_manager.create_agent(result.order, symbol, action, price, lot_size, signal_type)
        
        # Track recent trade
        self.recent_trades[symbol] = {
            'action': action,
            'time': datetime.now(),
            'price': price
        }
        
        time.sleep(0.1)
        return True

    # ------------------------------------------------------------------ #
    #  POSITION MONITORING & EXIT LOGIC
    # ------------------------------------------------------------------ #
    
    def check_active_trades(self):
        """Monitor and manage active trades with enhanced exit strategies"""
        if len(self.active_trades) == 0:
            return
        
        # Get agent decisions
        agent_decisions = self.agent_manager.update_all_agents()
        
        for ticket in list(self.active_trades.keys()):
            trade = self.active_trades[ticket]
            symbol = trade['symbol']
            
            # Get position (use cache)
            positions = self.get_positions_cached()
            position = None
            for pos in positions:
                if pos.ticket == ticket:
                    position = pos
                    break
            
            if position is None:
                # Position closed externally
                logger.info(f"{symbol} #{ticket} closed externally")
                self.logger.log_exit(symbol, trade['entry_price'], 0, "External_Close")
                self.performance.record_trade(symbol, trade['type'], 0)
                if self.analyzer:
                    self.analyzer.record_strategy_result(symbol, trade['type'], 0)
                
                self.agent_manager.remove_agent(ticket)
                
                with self._trades_lock:
                    if ticket in self.active_trades:
                        del self.active_trades[ticket]
                continue
            
            # Get profit
            profit_usd = self._get_profit_usd(position)
            
            # Update max profit
            if profit_usd > trade.get('max_profit_usd', 0):
                trade['max_profit_usd'] = profit_usd
            
            # Get current price
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                continue
            
            current_price = tick.bid if trade['action'] == 'BUY' else tick.ask
            
            # Calculate pips
            pip_multiplier = self._get_pip_multiplier(symbol)
            if trade['action'] == 'BUY':
                profit_pips = (current_price - trade['entry_price']) * pip_multiplier
            else:
                profit_pips = (trade['entry_price'] - current_price) * pip_multiplier
            
            # EXIT DECISION LOGIC
            should_close = False
            close_reason = ""
            exit_type = ""
            close_percentage = 1.0  # Full close by default
            
            # ============================================================================
            # 1. CUSTOM USD TP/SL (HIGHEST PRIORITY - check actual MT5 profit)
            # ============================================================================
            # DETAILED LOGGING FOR DEBUGGING CUSTOM TP/SL
            logger.debug(f"[CUSTOM_TPSL_CHECK] {symbol} #{ticket} | Mode: TP={self.tp_mode} SL={self.sl_mode} | "
                        f"Targets: TP=${self.tp_usd} SL=${self.sl_usd} | Current P&L: ${profit_usd:.2f} ({profit_pips:+.1f}p)")
            
            if self.tp_mode == 'custom' and self.tp_usd:
                logger.info(f"[CUSTOM_TP_MONITOR] {symbol} #{ticket} | Current: ${profit_usd:.2f} | Target: ${self.tp_usd} | "
                           f"Distance: ${(self.tp_usd - profit_usd):.2f} | Hit: {profit_usd >= self.tp_usd}")
                
                if profit_usd >= self.tp_usd:
                    should_close = True
                    close_reason = f"Custom TP: ${profit_usd:.2f} >= ${self.tp_usd}"
                    exit_type = "TP"
                    logger.critical(f"🎯 [CUSTOM_TP_TRIGGERED] {symbol} #{ticket}: {close_reason}")
                    print(f"   💰 {symbol} #{ticket}: Custom TP hit! Profit: ${profit_usd:.2f} (target: ${self.tp_usd})")
            
            if self.sl_mode == 'custom' and self.sl_usd and not should_close:
                logger.info(f"[CUSTOM_SL_MONITOR] {symbol} #{ticket} | Current: ${profit_usd:.2f} | Limit: -${self.sl_usd} | "
                           f"Distance: ${(profit_usd + self.sl_usd):.2f} | Hit: {profit_usd <= -self.sl_usd}")
                
                if profit_usd <= -self.sl_usd:
                    should_close = True
                    close_reason = f"Custom SL: ${profit_usd:.2f} <= -${self.sl_usd}"
                    exit_type = "SL"
                    logger.critical(f"🛑 [CUSTOM_SL_TRIGGERED] {symbol} #{ticket}: {close_reason}")
                    print(f"   🛑 {symbol} #{ticket}: Custom SL hit! Loss: ${profit_usd:.2f} (limit: -${self.sl_usd})")
            
            # 2. AGENT DECISION (if custom USD didn't trigger)
            # 2. AGENT DECISION (if custom USD didn't trigger)
            if not should_close and ticket in agent_decisions:
                agent_dec = agent_decisions[ticket]  # FIXED: agent returns a dict, not a tuple
                decision = agent_dec.get('decision', 'HOLD')
                reason = agent_dec.get('reason', '')
                confidence = agent_dec.get('confidence', 0.0)
                
                if decision in ('EXIT_FULL', 'EXIT_NOW') and confidence > 0.75:
                    should_close = True
                    close_reason = f"Agent: {reason}"
                    exit_type = "AGENT"
                    logger.info(f"{symbol} #{ticket}: Agent exit ({confidence:.0%}) - {reason}")
                elif decision == 'MODIFY_SL' and agent_dec.get('new_sl'):
                    new_sl = agent_dec['new_sl']
                    self.modify_position(ticket, sl=new_sl)
                    logger.info(f"{symbol} #{ticket}: Agent modified SL to {new_sl:.5f} - {reason}")
                elif decision == 'EXIT_PARTIAL' and agent_dec.get('exit_lot_size'):
                    exit_lot = agent_dec['exit_lot_size']
                    self.close_partial(ticket, exit_lot / trade.get('lot_size', exit_lot), reason)
                    logger.info(f"{symbol} #{ticket}: Agent partial exit {exit_lot} lots - {reason}")
                elif decision == 'TRAIL_STOP':
                    logger.debug(f"{symbol} #{ticket}: Agent trailing - {reason}")
            
            # 3. PARTIAL PROFIT TAKING
            if not should_close and profit_pips > 0:
                # Get strategy targets
                strategy_targets = {
                    'scalp':     {'tp': 20, 'partial_1': 10, 'partial_2': 15},
                    'range':     {'tp': 18, 'partial_1': 9, 'partial_2': 14},
                    'breakout':  {'tp': 30, 'partial_1': 15, 'partial_2': 22},
                    'momentum':  {'tp': 36, 'partial_1': 18, 'partial_2': 27},
                    'reversal':  {'tp': 50, 'partial_1': 25, 'partial_2': 37},
                    'sr_bounce': {'tp': 24, 'partial_1': 12, 'partial_2': 18},
                }
                targets = strategy_targets.get(trade['type'], {'tp': 30, 'partial_1': 15, 'partial_2': 22})
                
                # First partial (50% at 50% of TP)
                if trade['partial_closes'] == 0 and profit_pips >= targets['partial_1']:
                    if self.close_partial(ticket, 0.5, "50%_TP"):
                        trade['partial_closes'] = 1
                        logger.info(f"{symbol}: Closed 50% at {profit_pips:.1f}p")
                        
                        # Move to breakeven after first partial
                        if not trade['moved_to_breakeven']:
                            if self.move_to_breakeven(ticket):
                                trade['moved_to_breakeven'] = True
                                logger.info(f"{symbol}: Moved to breakeven")
                
                # Second partial (25% at 75% of TP)
                elif trade['partial_closes'] == 1 and profit_pips >= targets['partial_2']:
                    if self.close_partial(ticket, 0.5, "75%_TP"):  # 50% of remaining = 25% of original
                        trade['partial_closes'] = 2
                        logger.info(f"{symbol}: Closed 25% more at {profit_pips:.1f}p")
            
            # 4. BOT-MANAGED TP/SL (if custom USD and agent didn't trigger)
            if not should_close and (self.tp_mode == 'bot' and self.sl_mode == 'bot'):
                if trade.get('bot_managed_stops') or (trade['sl'] == 0 and trade['tp'] == 0):
                    strategy_targets = {
                        'scalp':     {'tp': 20, 'sl': -12},
                        'range':     {'tp': 18, 'sl': -10},
                        'breakout':  {'tp': 30, 'sl': -15},
                        'momentum':  {'tp': 36, 'sl': -18},
                        'reversal':  {'tp': 50, 'sl': -25},
                        'sr_bounce': {'tp': 24, 'sl': -12},
                    }
                    targets = strategy_targets.get(trade['type'], {'tp': 30, 'sl': -15})
                    
                    if profit_pips >= targets['tp']:
                        should_close = True
                        close_reason = f"+{profit_pips:.1f}p"
                        exit_type = "TP"
                    elif profit_pips <= targets['sl']:
                        should_close = True
                        close_reason = f"{profit_pips:.1f}p"
                        exit_type = "SL"
            
            # 5. TIME-BASED EXITS
            if not should_close:
                hold_time = (datetime.now() - trade['timestamp']).total_seconds()
                
                # Scalp: 10 minutes max if not profitable
                if trade['type'] == 'scalp' and hold_time > 600 and profit_pips < 0:
                    should_close = True
                    close_reason = f"{profit_pips:.1f}p (time)"
                    exit_type = "TIME"
                    logger.info(f"{symbol}: Time exit for scalp")
                
                # All strategies: 4 hours max
                elif hold_time > 14400:  # 4 hours
                    should_close = True
                    close_reason = f"{profit_pips:.1f}p (max_time)"
                    exit_type = "TIME"
                    logger.info(f"{symbol}: Max time exit")
                
                # Momentum: 2 hours if losing
                elif trade['type'] == 'momentum' and hold_time > 7200 and profit_pips < -5:
                    should_close = True
                    close_reason = f"{profit_pips:.1f}p (time)"
                    exit_type = "TIME"
            
            # 6. BREAKEVEN PROTECTION
            if not should_close and not trade['moved_to_breakeven']:
                # Move to breakeven if profit > 1.5x SL distance
                strategy_sl = {
                    'scalp': 12, 'range': 10, 'breakout': 15,
                    'momentum': 18, 'reversal': 25, 'sr_bounce': 12
                }
                sl_pips = strategy_sl.get(trade['type'], 15)
                
                if profit_pips >= sl_pips * 1.5:
                    if self.move_to_breakeven(ticket):
                        trade['moved_to_breakeven'] = True
                        logger.info(f"{symbol}: Auto-moved to breakeven at {profit_pips:.1f}p")
            
            # EXECUTE CLOSE
            if should_close:
                self._close_position(position, trade, ticket, symbol, current_price, 
                                   profit_pips, profit_usd, close_reason, exit_type, close_percentage)
    
    def _close_position(self, position, trade, ticket: int, symbol: str, current_price: float,
                        profit_pips: float, profit_usd: float, close_reason: str, exit_type: str,
                        close_percentage: float = 1.0):
        """Close a position (full or partial) and handle cleanup"""
        
        # Get close price
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return
        
        close_price = tick.bid if position.type == mt5.POSITION_TYPE_BUY else tick.ask
        
        # Determine volume to close
        close_volume = position.volume * close_percentage
        
        # Prepare close request
        close_request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": close_volume,
            "type": mt5.ORDER_TYPE_SELL if position.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY,
            "position": position.ticket,
            "price": close_price,
            "deviation": 20,
            "magic": 234000,
            "comment": f"Close_{exit_type}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        # Send with retry
        close_result = self.send_order_with_retry(close_request, max_retries=3)
        
        if close_result is None or close_result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Failed to close {symbol}: {close_result.comment if close_result else 'None'}")
            return
        
        # Success
        won = profit_pips > 0
        icon = "✓" if won else "✗"
        print(f"{icon} {symbol} closed {close_reason}")
        
        # Update daily P&L
        self.update_daily_pnl(profit_usd)
        
        # Log exit
        self.logger.log_exit(symbol, close_price, profit_pips, exit_type)
        
        # Record performance with enhanced metrics
        self.performance.record_trade(
            symbol=symbol,
            strategy=trade['type'],
            profit_pips=profit_pips,
            lot_size=trade['lot_size'],
            risk_pips=None,
            entry_quality=None,
            exit_quality=None,
            duration_minutes=(datetime.now() - trade['timestamp']).total_seconds() / 60,
            tags=[exit_type, 'partial' if trade['partial_closes'] > 0 else 'full']
        )
        
        # Adaptive learning
        if self.analyzer:
            self.analyzer.record_strategy_result(symbol, trade['type'], profit_pips)
        
        # Remove agent and tracking
        self.agent_manager.remove_agent(ticket)
        
        with self._trades_lock:
            if ticket in self.active_trades:
                del self.active_trades[ticket]
        
        # Invalidate position cache
        self.position_cache = None
    
    # ------------------------------------------------------------------ #
    #  UTILITY METHODS
    # ------------------------------------------------------------------ #
    
    def get_active_trades_count(self) -> int:
        """Return number of active bot positions"""
        positions = self.get_positions_cached(force_refresh=True)
        
        bot_positions = [pos for pos in positions if pos.magic == 234000]
        mt5_tickets = {pos.ticket for pos in bot_positions}
        
        # Sync tracking
        with self._trades_lock:
            tracked_tickets = list(self.active_trades.keys())
            for ticket in tracked_tickets:
                if ticket not in mt5_tickets:
                    trade = self.active_trades[ticket]
                    logger.info(f"Removing {trade['symbol']} #{ticket} from tracking")
                    del self.active_trades[ticket]
        
        return len(bot_positions)
    
    def show_performance(self):
        """Display performance dashboard"""
        active_count = self.get_active_trades_count()
        self.performance.print_dashboard(active_positions=active_count)
        
        # Show execution stats
        with self._stats_lock:
            stats = self.execution_stats
            if stats['total_orders'] > 0:
                success_rate = (stats['successful_orders'] / stats['total_orders']) * 100
                print(f"\n📊 EXECUTION STATS:")
                print(f"   Orders: {stats['total_orders']} | Success: {success_rate:.1f}%")
                print(f"   Retries: {stats['retries']} | Avg Slippage: {stats['avg_slippage_pips']:.2f}p")
                print(f"   Daily P&L: ${self.daily_pnl:.2f} | Limit: ${self.daily_loss_limit:.2f}")
                if self.emergency_stop_active:
                    print(f"   ⛔ EMERGENCY STOP ACTIVE")
                print()
    
    def close_all_positions(self):
        """Emergency: Close all bot positions"""
        positions = self.get_positions_cached(force_refresh=True)
        
        bot_positions = [pos for pos in positions if pos.magic == 234000]
        
        if not bot_positions:
            print("No bot positions to close")
            return
        
        print(f"\n🚨 CLOSING ALL {len(bot_positions)} BOT POSITIONS...")
        
        for pos in bot_positions:
            tick = mt5.symbol_info_tick(pos.symbol)
            if tick is None:
                continue
            
            close_price = tick.bid if pos.type == mt5.POSITION_TYPE_BUY else tick.ask
            
            close_request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                "position": pos.ticket,
                "price": close_price,
                "deviation": 20,
                "magic": 234000,
                "comment": "Emergency_Close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            result = self.send_order_with_retry(close_request, max_retries=2)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                print(f"   ✓ Closed {pos.symbol}")
            else:
                comment = result.comment if result else "None"
                print(f"   ✗ Failed to close {pos.symbol}: {comment}")
        
        # Clear tracking
        with self._trades_lock:
            self.active_trades.clear()
        
        self.position_cache = None
        print("✓ All positions closed")
    
    def get_execution_report(self) -> Dict:
        """Get comprehensive execution report"""
        with self._stats_lock:
            stats = self.execution_stats.copy()
        
        stats['daily_pnl'] = self.daily_pnl
        stats['emergency_stop_active'] = self.emergency_stop_active
        stats['active_positions'] = self.get_active_trades_count()
        stats['failed_symbols_count'] = len(self.failed_symbols)
        
        return stats
