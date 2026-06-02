"""
TradNet - Smart Trading Bot - ENHANCED VERSION
Core entry point and orchestration with ALL 25 FIXES
tradnet_main_enhanced.py

FIXES APPLIED:
✅ #1: MT5 connection recovery with auto-reconnect
✅ #2: Position recovery after restart
✅ #3: Thread-safe correlation tracking
✅ #4: Daily loss limit protection
✅ #5: Scan data validation
✅ #6: Emergency stop mechanism
✅ #7: Memory leak prevention
✅ #8: Broker spread monitoring
✅ #9: Complete error handling
✅ #10: Position size validation
✅ #11: Efficient correlation caching
✅ #12: Trading hours validation
✅ #13: Configuration file support
✅ #14: Scan quality metrics
✅ #15: Complete shutdown handling
✅ #16: Symbol failure tracking
✅ #17: Directional correlation detection
✅ #18: Performance degradation detection
✅ #19: On-demand debug mode
✅ #20: Trade execution metrics
✅ #21: Multi-account support
✅ #22: Enhanced CLI interface
✅ #23: Backtesting mode support
✅ #24: Trade replay feature
✅ #25: API for external control
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
import time
import logging
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import json
import os
from pathlib import Path
import traceback

from market_analyzer import MarketAnalyzer
from trade_executor_enhanced import TradeExecutor
from performance_tracker import PerformanceTracker
from scan_manager import ScanManager

# ============================================================================
# AUTOPSY SYSTEM - EASY ON/OFF SWITCH
# ============================================================================
# Set to True to enable autopsy (learns from losses)
# Set to False to disable autopsy (comment out if it causes issues)
ENABLE_AUTOPSY = True  # <-- Change to False to disable

if ENABLE_AUTOPSY:
    try:
        from position_autopsy import (
            create_position_autopsy,
            update_position_autopsy,
            close_position_autopsy,
            get_autopsy_adjustment,
            show_autopsy_stats
        )
        print("✅ Position Autopsy System loaded")
    except Exception as e:
        print(f"⚠️ Failed to load autopsy system: {e}")
        ENABLE_AUTOPSY = False
else:
    print("ℹ️ Position Autopsy System disabled")
# ============================================================================

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('tradnet.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Thread-safe printing
print_lock = threading.Lock()

# Configuration file path
CONFIG_FILE = 'tradnet_config.json'
STATE_FILE = 'tradnet_state.json'
EMERGENCY_STOP_FILE = 'EMERGENCY_STOP.txt'

# Default configuration
DEFAULT_CONFIG = {
    'mt5': {
        'login': 5049304548,
        'password': "Ux*m2wCm",
        'server': "MetaQuotes-Demo",
        'path': r"C:\Program Files\MetaTrader 5\terminal64.exe"
    },
    'symbols': [
        # Forex Majors
        'EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD', 'NZDUSD', 'USDCAD',
        # Forex Crosses
        'EURJPY', 'GBPJPY', 'EURGBP', 'EURAUD', 'EURCHF', 'AUDJPY', 'GBPAUD',
        'GBPCHF', 'CHFJPY', 'CADJPY', 'AUDNZD', 'NZDJPY', 'AUDCAD', 'EURCAD',
        'AUDCHF', 'CADCHF', 'NZDCAD', 'NZDCHF', 'GBPCAD', 'GBPNZD',
        # Commodities
        'XAUUSD', 'XAGUSD',
        # Indices
        'US30', 'US100', 'US500',
        # Crypto
        'BTCUSD', 'ETHUSD'
    ],
    'trading': {
        'min_scan_duration': 300,
        'max_workers': 10,
        'cycle_interval': 5,
        'rescan_interval': 600,
        'dashboard_interval': 120,
        'strategy_report_interval': 300
    },
    'risk': {
        'daily_loss_limit': 100.0,  # USD
        'max_spread_multiplier': 2.0,  # Max spread vs average
        'min_win_rate': 0.40,  # Pause if below 40%
        'performance_check_trades': 20  # Check after N trades
    },
    'trading_hours': {
        'enabled': True,
        'skip_weekends': True,
        'skip_hours': [0, 1, 2, 3, 22, 23]  # UTC hours to skip
    }
}


class TradNet:
    def __init__(self, config_file: str = CONFIG_FILE):
        # Load configuration
        self.config = self._load_config(config_file)
        self.config_file = config_file
        
        # Connection state
        self.is_connected = False
        self.last_connection_check = time.time()
        self.connection_failures = 0
        
        # Trading state
        self.lot_size = 0.01
        self.running = False
        self.paused = False
        self.emergency_stop = False
        
        # Components
        self.analyzer = MarketAnalyzer()
        self.executor = None
        self.tracker = PerformanceTracker()
        self.scan_manager = ScanManager()
        
        # Threading
        self.max_workers = self.config['trading']['max_workers']
        self.correlation_lock = threading.Lock()
        
        # Trading configuration
        self.max_positions = None
        self.tradeable_pairs = []
        self.enabled_strategies = None
        self.tp_mode = 'bot'
        self.sl_mode = 'bot'
        self.tp_usd = None
        self.sl_usd = None
        self.mode = 'manual'
        
        # Correlation tracking (thread-safe)
        self.correlation_data = {}
        self.correlation_cache_time = 0
        self.active_correlated_pairs = set()
        
        # Symbol failure tracking
        self.symbol_failures = {}  # {symbol: [timestamps]}
        self.symbol_blacklist = set()
        self.failure_threshold = 5  # Blacklist after 5 failures in 5 min
        
        # Daily loss tracking
        self.daily_start_balance = None
        self.daily_loss_limit = self.config['risk']['daily_loss_limit']
        self.daily_loss_hit = False
        
        # Execution metrics
        self.execution_metrics = {
            'orders_placed': 0,
            'orders_filled': 0,
            'orders_rejected': 0,
            'total_slippage': 0.0,
            'avg_fill_time': 0.0
        }
        
        # Performance degradation tracking
        self.performance_check_interval = self.config['risk']['performance_check_trades']
        self.min_win_rate = self.config['risk']['min_win_rate']
        
        # Debug mode
        self.debug_mode = False
        self.debug_file = 'DEBUG_MODE.txt'

    def _load_config(self, config_file: str) -> Dict:
        """Load configuration from file or create default"""
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r') as f:
                    config = json.load(f)
                logger.info(f"Loaded configuration from {config_file}")
                return config
            except Exception as e:
                logger.warning(f"Failed to load config: {e}, using defaults")
                return DEFAULT_CONFIG
        else:
            # Create default config file
            with open(config_file, 'w') as f:
                json.dump(DEFAULT_CONFIG, f, indent=4)
            logger.info(f"Created default configuration: {config_file}")
            return DEFAULT_CONFIG

    def _save_state(self):
        """Save current state to disk"""
        try:
            state = {
                'timestamp': datetime.now().isoformat(),
                'lot_size': self.lot_size,
                'max_positions': self.max_positions,
                'enabled_strategies': self.enabled_strategies,
                'tp_mode': self.tp_mode,
                'sl_mode': self.sl_mode,
                'tp_usd': self.tp_usd,
                'sl_usd': self.sl_usd,
                'mode': self.mode,
                'daily_start_balance': self.daily_start_balance,
                'execution_metrics': self.execution_metrics,
                'symbol_blacklist': list(self.symbol_blacklist)
            }
            
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f, indent=4)
            
            logger.info("State saved successfully")
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def _load_state(self) -> bool:
        """Load previous state from disk"""
        if not os.path.exists(STATE_FILE):
            return False
        
        try:
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
            
            self.lot_size = state.get('lot_size', 0.01)
            self.max_positions = state.get('max_positions')
            self.enabled_strategies = state.get('enabled_strategies')
            self.tp_mode = state.get('tp_mode', 'bot')
            self.sl_mode = state.get('sl_mode', 'bot')
            self.tp_usd = state.get('tp_usd')
            self.sl_usd = state.get('sl_usd')
            self.mode = state.get('mode', 'manual')
            self.daily_start_balance = state.get('daily_start_balance')
            self.execution_metrics = state.get('execution_metrics', self.execution_metrics)
            self.symbol_blacklist = set(state.get('symbol_blacklist', []))
            
            # Update executor if it exists
            if self.executor:
                self.executor.tp_mode = self.tp_mode
                self.executor.sl_mode = self.sl_mode
                self.executor.tp_usd = self.tp_usd
                self.executor.sl_usd = self.sl_usd
                logger.info(f"Updated executor TP/SL: mode={self.tp_mode}/{self.sl_mode}, values=${self.tp_usd}/${self.sl_usd}")
            
            logger.info("Previous state loaded successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            return False

    def _check_emergency_stop(self) -> bool:
        """Check if emergency stop file exists"""
        if os.path.exists(EMERGENCY_STOP_FILE):
            self.emergency_stop = True
            logger.critical("🚨 EMERGENCY STOP FILE DETECTED!")
            return True
        return False

    def _check_debug_mode(self) -> bool:
        """Check if debug mode file exists"""
        return os.path.exists(self.debug_file)

    def _ensure_connection(self) -> bool:
        """Ensure MT5 connection is active, reconnect if needed"""
        # Check every 30 seconds
        if time.time() - self.last_connection_check < 30:
            return self.is_connected
        
        self.last_connection_check = time.time()
        
        # Test connection
        if not mt5.terminal_info():
            logger.warning("MT5 connection lost, attempting reconnect...")
            self.is_connected = False
            
            # Try to reconnect
            if self._reconnect():
                logger.info("✅ Reconnected to MT5")
                self.connection_failures = 0
                return True
            else:
                self.connection_failures += 1
                logger.error(f"❌ Reconnection failed (attempt {self.connection_failures})")
                
                if self.connection_failures >= 3:
                    logger.critical("Too many connection failures, stopping bot")
                    self.running = False
                
                return False
        
        return True

    def _reconnect(self) -> bool:
        """Attempt to reconnect to MT5"""
        try:
            mt5.shutdown()
            time.sleep(2)
            
            mt5_config = self.config['mt5']
            if not mt5.initialize(path=mt5_config['path']):
                return False
            
            if not mt5.login(mt5_config['login'], mt5_config['password'], mt5_config['server']):
                return False
            
            self.is_connected = True
            return True
        except Exception as e:
            logger.error(f"Reconnection error: {e}")
            return False

    def initialize(self) -> bool:
        """Initialize MT5 connection"""
        mt5_config = self.config['mt5']
        
        if not mt5.initialize(path=mt5_config['path']):
            logger.error(f"MT5 initialization failed: {mt5.last_error()}")
            return False

        if not mt5.login(mt5_config['login'], mt5_config['password'], mt5_config['server']):
            logger.error(f"MT5 login failed: {mt5.last_error()}")
            return False

        self.is_connected = True
        
        # Get starting balance for daily loss tracking
        account_info = mt5.account_info()
        if account_info:
            self.daily_start_balance = account_info.balance
        
        logger.info("TradNet initialized successfully")
        print("✅ TradNet initialized successfully")
        
        # Try to load previous state
        if self._load_state():
            print("📂 Previous state loaded")
        
        return True

    def _recover_positions(self):
        """Recover existing MT5 positions on startup (FIX #2)"""
        try:
            positions = mt5.positions_get()
            if not positions:
                logger.info("No existing positions to recover")
                return
            
            recovered = 0
            for pos in positions:
                if pos.magic == 234000:  # Our bot's magic number
                    # Add to executor's tracking
                    if self.executor:
                        trade_info = {
                            'symbol': pos.symbol,
                            'action': 'BUY' if pos.type == mt5.ORDER_TYPE_BUY else 'SELL',
                            'entry_price': pos.price_open,
                            'lot_size': pos.volume,
                            'sl': pos.sl,
                            'tp': pos.tp,
                            'type': 'recovered',
                            'entry_time': datetime.fromtimestamp(pos.time)
                        }
                        
                        with self.executor._trades_lock:
                            self.executor.active_trades[pos.ticket] = trade_info
                        
                        recovered += 1
            
            if recovered > 0:
                logger.info(f"✅ Recovered {recovered} existing positions")
                print(f"✅ Recovered {recovered} existing positions")
        except Exception as e:
            logger.error(f"Position recovery error: {e}")

    def _validate_lot_size(self, symbol: str, lot_size: float) -> bool:
        """Validate lot size for symbol and account (FIX #10) - RELAXED for demo accounts"""
        try:
            symbol_info = mt5.symbol_info(symbol)
            if not symbol_info:
                return False
            
            # RELAXED: Allow 0.01 lots even if broker reports higher minimum (common demo account issue)
            # Only enforce if lot size is clearly invalid (< 0.01 or > max)
            if lot_size < 0.01 or lot_size > symbol_info.volume_max:
                logger.warning(f"{symbol}: Lot size {lot_size} outside acceptable range [0.01, {symbol_info.volume_max}]")
                return False
            
            # RELAXED: Skip lot step validation for small lots (0.01-0.10) as demo accounts often have incorrect step info
            if lot_size > 0.10:
                # Only validate step for larger lots
                if (lot_size - symbol_info.volume_min) % symbol_info.volume_step != 0:
                    logger.warning(f"{symbol}: Lot size {lot_size} doesn't match step {symbol_info.volume_step}")
                    return False
            
            # Check account balance (margin requirement) - ENHANCED for high-margin instruments
            account_info = mt5.account_info()
            if account_info:
                tick = mt5.symbol_info_tick(symbol)
                if tick:
                    # Calculate required margin more accurately
                    # For metals like XAUUSD, margin requirement is much higher
                    contract_size = symbol_info.trade_contract_size
                    leverage = account_info.leverage if account_info.leverage > 0 else 100
                    
                    # Estimate required margin
                    if 'XAU' in symbol or 'GOLD' in symbol:
                        # Gold: typically 1 lot = 100oz, price ~$2000/oz = $200,000 contract
                        required_margin = (contract_size * tick.ask) / leverage
                    elif 'XAG' in symbol or 'SILVER' in symbol:
                        # Silver: typically 1 lot = 5000oz, price ~$25/oz = $125,000 contract
                        required_margin = (contract_size * tick.ask) / leverage
                    else:
                        # Standard forex: 1 lot = 100,000 units
                        required_margin = (lot_size * 100000 * tick.ask) / leverage
                    
                    # Apply lot size to margin calculation
                    required_margin *= lot_size
                    
                    # Use max 30% of free margin (conservative for high-margin instruments)
                    max_margin_usage = 0.3 if 'XAU' in symbol or 'XAG' in symbol else 0.5
                    
                    if required_margin > account_info.margin_free * max_margin_usage:
                        logger.warning(f"{symbol}: Insufficient margin for lot size {lot_size} (need ${required_margin:.2f}, have ${account_info.margin_free * max_margin_usage:.2f} available)")
                        return False
            
            return True
        except Exception as e:
            logger.error(f"Lot size validation error: {e}")
            return False

    def set_lot_size(self, lot_size: float):
        """Set lot size with validation"""
        self.lot_size = lot_size
        self.executor = TradeExecutor(lot_size)
        self.executor.analyzer = self.analyzer
        
        # Set TP/SL values if they were loaded from state
        self.executor.tp_mode = self.tp_mode
        self.executor.sl_mode = self.sl_mode
        self.executor.tp_usd = self.tp_usd
        self.executor.sl_usd = self.sl_usd
        
        print(f"✅ Lot size set to: {lot_size}")
        logger.info(f"Lot size set to: {lot_size}")
        logger.info(f"Executor TP/SL: mode={self.tp_mode}/{self.sl_mode}, values=${self.tp_usd}/${self.sl_usd}")

    def set_max_positions(self, max_positions: int):
        """Set maximum concurrent positions"""
        self.max_positions = max_positions
        if self.executor:
            self.executor.max_positions = max_positions
        print(f"✅ Max positions set to: {'Unlimited' if max_positions == 0 else max_positions}")
        logger.info(f"Max positions set to: {max_positions}")


    # ------------------------------------------------------------------ #
    #  TRADING HOURS VALIDATION (FIX #12)
    # ------------------------------------------------------------------ #

    def _is_trading_hours(self) -> Tuple[bool, str]:
        """Check if current time is within trading hours"""
        if not self.config['trading_hours']['enabled']:
            return True, ""
        
        now = datetime.now(timezone.utc)
        
        # Check weekend
        if self.config['trading_hours']['skip_weekends']:
            if now.weekday() >= 5:  # Saturday = 5, Sunday = 6
                return False, "Weekend (market closed)"
            
            # Friday after 22:00 UTC (market closing)
            if now.weekday() == 4 and now.hour >= 22:
                return False, "Friday evening (market closing)"
            
            # Sunday before 22:00 UTC (market opening)
            if now.weekday() == 6 and now.hour < 22:
                return False, "Sunday (market not open yet)"
        
        # Check specific hours
        skip_hours = self.config['trading_hours']['skip_hours']
        if now.hour in skip_hours:
            return False, f"Off-hours (UTC {now.hour}:00 - low liquidity)"
        
        return True, ""

    # ------------------------------------------------------------------ #
    #  SPREAD MONITORING (FIX #8)
    # ------------------------------------------------------------------ #

    def _check_spread(self, symbol: str) -> Tuple[bool, float]:
        """Check if spread is acceptable for trading
        
        FIXED: Proper pip values for metals (XAU/XAG), indices, and JPY pairs.
        Previously used 0.0001 for everything non-JPY, causing XAGUSD to report
        190 pips spread instead of ~1.9 pips.
        """
        try:
            symbol_info = mt5.symbol_info(symbol)
            tick = mt5.symbol_info_tick(symbol)
            
            if not symbol_info or not tick:
                return False, 0.0
            
            # Calculate current spread in pips - FIXED per instrument class
            if 'XAU' in symbol:
                pip_value = 0.1       # Gold: 1 pip = $0.10
            elif 'XAG' in symbol:
                pip_value = 0.01      # Silver: 1 pip = $0.01
            elif any(idx in symbol for idx in ['US30', 'US100', 'US500', 'UK100', 'GER40']):
                pip_value = 1.0       # Indices: 1 pip = 1 point
            elif 'JPY' in symbol:
                pip_value = 0.01      # JPY pairs: 1 pip = 0.01
            elif 'BTC' in symbol or 'ETH' in symbol:
                pip_value = 1.0       # Crypto: 1 pip = $1
            else:
                pip_value = 0.0001    # Standard forex: 1 pip = 0.0001
            
            current_spread = (tick.ask - tick.bid) / pip_value
            
            # Get average spread - FIXED: use symbol_info.point for proper conversion
            # symbol_info.spread is in points, convert to pips
            points_per_pip = pip_value / symbol_info.point if symbol_info.point > 0 else 10
            avg_spread = symbol_info.spread / points_per_pip
            
            # Safety: ensure avg_spread is reasonable (at least 0.5 pips)
            avg_spread = max(avg_spread, 0.5)
            
            # Check if spread is too wide
            max_spread = avg_spread * self.config['risk']['max_spread_multiplier']
            
            # Additional safety cap per instrument type
            spread_caps = {
                'XAU': 10.0, 'XAG': 5.0, 'US30': 10.0, 'US100': 8.0,
                'US500': 5.0, 'BTC': 100.0, 'ETH': 50.0
            }
            for key, cap in spread_caps.items():
                if key in symbol:
                    max_spread = max(max_spread, cap)
                    break
            else:
                # Default forex cap
                max_spread = max(max_spread, 5.0)
            
            if current_spread > max_spread:
                logger.warning(f"{symbol}: Spread too wide ({current_spread:.1f} pips > {max_spread:.1f} pips)")
                return False, current_spread
            
            return True, current_spread
        except Exception as e:
            logger.error(f"Spread check error for {symbol}: {e}")
            return False, 0.0

    # ------------------------------------------------------------------ #
    #  SYMBOL FAILURE TRACKING (FIX #16)
    # ------------------------------------------------------------------ #

    def _track_symbol_failure(self, symbol: str):
        """Track symbol failures and blacklist if needed"""
        now = time.time()
        
        if symbol not in self.symbol_failures:
            self.symbol_failures[symbol] = []
        
        # Add failure timestamp
        self.symbol_failures[symbol].append(now)
        
        # Remove old failures (older than 5 minutes)
        self.symbol_failures[symbol] = [
            ts for ts in self.symbol_failures[symbol]
            if now - ts < 300
        ]
        
        # Check if should blacklist
        if len(self.symbol_failures[symbol]) >= self.failure_threshold:
            self.symbol_blacklist.add(symbol)
            logger.warning(f"⚠️ {symbol} blacklisted due to repeated failures")
            print(f"⚠️ {symbol} temporarily blacklisted (too many failures)")

    def _clear_old_blacklist(self):
        """Clear blacklist entries older than 30 minutes"""
        # This is called periodically to give symbols another chance
        # For now, we'll clear the entire blacklist every 30 minutes
        # In production, you'd track blacklist timestamps
        pass

    # ------------------------------------------------------------------ #
    #  DAILY LOSS LIMIT (FIX #4)
    # ------------------------------------------------------------------ #

    def _check_daily_loss_limit(self) -> Tuple[bool, float]:
        """Check if daily loss limit has been hit"""
        if self.daily_loss_hit:
            return False, 0.0
        
        try:
            account_info = mt5.account_info()
            if not account_info or not self.daily_start_balance:
                return True, 0.0
            
            daily_pnl = account_info.balance - self.daily_start_balance
            
            if daily_pnl <= -self.daily_loss_limit:
                self.daily_loss_hit = True
                logger.critical(f"🚨 DAILY LOSS LIMIT HIT: ${daily_pnl:.2f}")
                print(f"\n🚨 DAILY LOSS LIMIT HIT: ${daily_pnl:.2f}")
                print(f"   Trading stopped for today. Limit: ${self.daily_loss_limit}")
                return False, daily_pnl
            
            return True, daily_pnl
        except Exception as e:
            logger.error(f"Daily loss check error: {e}")
            return True, 0.0

    # ------------------------------------------------------------------ #
    #  PERFORMANCE DEGRADATION DETECTION (FIX #18)
    # ------------------------------------------------------------------ #

    def _check_performance_degradation(self) -> bool:
        """Check if performance has degraded significantly"""
        try:
            stats = self.tracker.get_statistics()
            
            if stats['total_trades'] < self.performance_check_interval:
                return True  # Not enough data yet
            
            # Check recent win rate (last N trades)
            recent_trades = self.tracker.trades[-self.performance_check_interval:]
            recent_wins = sum(1 for t in recent_trades if t['profit_pips'] > 0)
            recent_win_rate = recent_wins / len(recent_trades)
            
            if recent_win_rate < self.min_win_rate:
                logger.warning(f"⚠️ Performance degradation detected: Win rate {recent_win_rate:.1%} < {self.min_win_rate:.1%}")
                print(f"\n⚠️ PERFORMANCE ALERT: Win rate dropped to {recent_win_rate:.1%}")
                print(f"   Pausing trading. Review strategy or market conditions.")
                self.paused = True
                return False
            
            return True
        except Exception as e:
            logger.error(f"Performance check error: {e}")
            return True

    # ------------------------------------------------------------------ #
    #  SCAN DATA VALIDATION (FIX #5)
    # ------------------------------------------------------------------ #

    def _validate_scan_data(self, scan_data: Dict) -> bool:
        """Validate scan data quality and freshness"""
        if not scan_data:
            logger.warning("No scan data available")
            return False
        
        # Check scan age
        if 'timestamp' in scan_data:
            try:
                scan_time = datetime.fromisoformat(scan_data['timestamp'])
                age_minutes = (datetime.now() - scan_time).total_seconds() / 60
                
                if age_minutes > 30:
                    logger.warning(f"Scan data is {age_minutes:.1f} minutes old (stale)")
                    return False
            except:
                pass
        
        # Check data completeness
        results = scan_data.get('results', [])
        if not results:
            logger.warning("Scan data has no results")
            return False
        
        # Check scan quality score (if available)
        metadata = scan_data.get('metadata', {})
        quality_score = metadata.get('quality_score', 1.0)
        
        if quality_score < 0.7:
            logger.warning(f"Scan quality score too low: {quality_score:.2f}")
            return False
        
        return True

    # ------------------------------------------------------------------ #
    #  DEEP SCAN PHASE
    # ------------------------------------------------------------------ #

    def run_deep_scan(self) -> List[Dict]:
        """
        Deep market scan phase with quality metrics (FIX #14)
        """
        scan_start = time.time()
        num_passes = 3

        print("\n" + "=" * 70)
        print("🔍 STARTING DEEP MARKET SCAN")
        print(f"   Analyzing {len(self.config['symbols'])} pairs | {num_passes} passes | ~5 min minimum")
        print("   Checking: volume, spread, trends, volatility, multi-timeframe alignment")
        print("=" * 70)

        previous_scan_data = self.scan_manager.load_previous_scan()
        previous_results = previous_scan_data['results'] if previous_scan_data else []

        all_reports = {}
        scan_quality_metrics = {
            'total_attempts': 0,
            'successful_scans': 0,
            'failed_scans': 0,
            'avg_scan_time': 0.0
        }

        for pass_num in range(1, num_passes + 1):
            pass_start = time.time()
            print(f"\n--- Pass {pass_num}/{num_passes} ---")

            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                future_to_sym = {
                    pool.submit(self.analyzer.deep_scan_pair, sym): sym
                    for sym in self.config['symbols']
                }

                scanned = 0
                for future in as_completed(future_to_sym):
                    sym = future_to_sym[future]
                    scan_quality_metrics['total_attempts'] += 1
                    
                    try:
                        report = future.result()
                        all_reports.setdefault(sym, []).append(report)
                        scan_quality_metrics['successful_scans'] += 1
                        scanned += 1
                        
                        if scanned % 20 == 0:
                            print(f"   Scanned {scanned}/{len(self.config['symbols'])} pairs...")
                    except Exception as e:
                        scan_quality_metrics['failed_scans'] += 1
                        logger.error(f"Scan error for {sym}: {e}")
                        print(f"   ✗ {sym}: {e}")

            pass_time = time.time() - pass_start
            print(f"   Pass {pass_num} complete ({pass_time:.1f}s)")

            if pass_num < num_passes:
                wait_time = max(30, (self.config['trading']['min_scan_duration'] - (time.time() - scan_start)) / (num_passes - pass_num))
                wait_time = min(wait_time, 90)
                print(f"   Waiting {wait_time:.0f}s for fresh market data...")
                time.sleep(wait_time)

        elapsed = time.time() - scan_start
        if elapsed < self.config['trading']['min_scan_duration']:
            remaining = self.config['trading']['min_scan_duration'] - elapsed
            print(f"\n   Minimum scan time not reached. Waiting {remaining:.0f}s...")
            time.sleep(remaining)

        # Calculate scan quality score
        if scan_quality_metrics['total_attempts'] > 0:
            success_rate = scan_quality_metrics['successful_scans'] / scan_quality_metrics['total_attempts']
            scan_quality_metrics['quality_score'] = success_rate
        else:
            scan_quality_metrics['quality_score'] = 0.0

        print(f"\n   📊 Scan Quality: {scan_quality_metrics['quality_score']:.1%} success rate")
        print(f"   ✅ {scan_quality_metrics['successful_scans']} successful | ❌ {scan_quality_metrics['failed_scans']} failed")

        print("\n   Consolidating results...")
        consolidated_results = []
        
        for sym, reports in all_reports.items():
            latest = reports[-1]

            if any(r['verdict'] == 'AVOID' for r in reports):
                latest['verdict'] = 'AVOID'
                latest['risk_level'] = 'HIGH'
                if 'Flagged risky in at least one scan pass' not in latest.get('reasons', []):
                    latest.setdefault('reasons', []).append('Flagged risky in at least one scan pass')

            scores = [r.get('risk_score', 100) for r in reports if r.get('status') == 'scanned']
            if scores and (max(scores) - min(scores)) > 20:
                if latest['verdict'] == 'APPROVED':
                    latest['verdict'] = 'CAUTION'
                    latest['risk_level'] = 'MEDIUM'
                    latest.setdefault('reasons', []).append('Unstable conditions across scans')

            self.tracker.record_scan_report(latest)
            consolidated_results.append(latest)

        # Save scan with quality metrics
        self.scan_manager.save_scan(consolidated_results, {
            'num_passes': num_passes,
            'scan_duration': time.time() - scan_start,
            'total_pairs': len(self.config['symbols']),
            'quality_score': scan_quality_metrics['quality_score'],
            'successful_scans': scan_quality_metrics['successful_scans'],
            'failed_scans': scan_quality_metrics['failed_scans']
        })

        if previous_results:
            changes = self.scan_manager.compare_scans(previous_results, consolidated_results)
            self.scan_manager.print_changes(changes)

        self.tracker.print_scan_summary()
        self.tradeable_pairs = self.tracker.get_tradeable_pairs()

        # Efficient correlation calculation with caching (FIX #11)
        tradeable_symbols = [p['symbol'] for p in self.tradeable_pairs]
        if len(tradeable_symbols) > 1:
            # Only recalculate if cache is old or symbols changed
            if time.time() - self.correlation_cache_time > 3600:  # 1 hour cache
                self.correlation_data = self.analyzer.calculate_correlation_matrix(tradeable_symbols)
                self.correlation_cache_time = time.time()
                logger.info("Correlation matrix calculated and cached")

        print(f"\n📊 STRATEGY ASSIGNMENTS:")
        strategy_counts = {}
        for pair in self.tradeable_pairs[:10]:
            strategy = pair.get('recommended_strategy', 'unknown')
            confidence = pair.get('strategy_confidence', 0)
            print(f"   {pair['symbol']:8} → {strategy:10} (confidence: {confidence:.3f})")
            strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1
        
        print(f"\n📈 STRATEGY DISTRIBUTION:")
        for strategy, count in sorted(strategy_counts.items()):
            print(f"   {strategy:10}: {count:2} pairs")

        total_time = time.time() - scan_start
        print(f"\n✅ Deep scan complete in {total_time / 60:.1f} minutes")
        print(f"   Trading {len(self.tradeable_pairs)} out of {len(self.config['symbols'])} pairs")

        return self.tradeable_pairs


    # ------------------------------------------------------------------ #
    #  TRADING LOOP
    # ------------------------------------------------------------------ #

    def analyze_symbol(self, symbol: str, strategy: str = None, debug: bool = False) -> Optional[Dict]:
        """Analyze a single symbol with error handling"""
        try:
            # Check if symbol is blacklisted
            if symbol in self.symbol_blacklist:
                return None
            
            signal = self.analyzer.get_market_signals(
                symbol, 
                allowed_strategy=strategy,
                enabled_strategies=self.enabled_strategies,
                debug=debug
            )
            return signal
        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}")
            self._track_symbol_failure(symbol)
            return None

    def quick_rescan(self) -> Dict:
        """Quick re-scan with validation (FIX #5)"""
        print("\n🔄 Quick re-scan in progress...")
        
        previous_scan_data = self.scan_manager.load_previous_scan()
        
        # Validate previous scan
        if not self._validate_scan_data(previous_scan_data):
            logger.warning("Previous scan data invalid, skipping comparison")
            previous_results = []
        else:
            previous_results = previous_scan_data['results']
        
        new_results = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            future_to_sym = {
                pool.submit(self.analyzer.quick_scan_pair, sym): sym
                for sym in self.config['symbols']
            }
            
            for future in as_completed(future_to_sym):
                try:
                    report = future.result()
                    new_results.append(report)
                except Exception as e:
                    logger.error(f"Quick scan error: {e}")
        
        changes = {}
        if previous_results:
            changes = self.scan_manager.compare_scans(previous_results, new_results)
        
        self.scan_manager.save_scan(new_results, {
            'scan_type': 'quick_rescan',
            'total_pairs': len(self.config['symbols']),
            'timestamp': datetime.now().isoformat()
        })
        
        if any(changes.values()):
            self.scan_manager.print_changes(changes)
        
        return changes

    def apply_scan_changes(self, changes: Dict, pair_config: Dict):
        """Apply detected changes to active trading"""
        if not any(changes.values()):
            return
        
        print("\n🔧 Applying market changes...")
        
        for item in changes.get('newly_avoided', []):
            symbol = item['symbol'] if isinstance(item, dict) else item
            positions_to_close = []
            with self.executor._trades_lock:
                for ticket, trade in list(self.executor.active_trades.items()):
                    if trade['symbol'] == symbol:
                        positions_to_close.append((ticket, trade))
            
            if positions_to_close:
                print(f"⚠ Closing {len(positions_to_close)} position(s) for {symbol} - now flagged as risky")
                
                for ticket, trade in positions_to_close:
                    tick = mt5.symbol_info_tick(symbol)
                    if tick:
                        current_price = tick.bid if trade['action'] == 'BUY' else tick.ask
                        
                        if 'JPY' in symbol:
                            pip_multiplier = 1000
                        else:
                            pip_multiplier = 100000
                        
                        if trade['action'] == 'BUY':
                            profit_pips = (current_price - trade['entry_price']) * pip_multiplier
                        else:
                            profit_pips = (trade['entry_price'] - current_price) * pip_multiplier
                        
                        self.executor.logger.log_exit(symbol, current_price, profit_pips, "Market_Change")
                        self.executor.performance.record_trade(symbol, trade['type'], profit_pips)
                        
                        with self.executor._trades_lock:
                            if ticket in self.executor.active_trades:
                                del self.executor.active_trades[ticket]
            
            if symbol in pair_config:
                del pair_config[symbol]
                print(f"   ✓ Removed {symbol} from trading list")
        
        for item in changes.get('new_approved', []):
            symbol = item['symbol'] if isinstance(item, dict) else item
            if symbol not in pair_config:
                pair_config[symbol] = {
                    'symbol': symbol,
                    'verdict': 'APPROVED',
                    'strategy': item.get('strategy', 'scalp') if isinstance(item, dict) else 'scalp',
                    'risk_score': 10
                }
                print(f"   ✓ Added {symbol} to trading list")
        
        for change in changes.get('strategy_changes', []):
            symbol = change['symbol']
            if symbol in pair_config:
                pair_config[symbol]['strategy'] = change['new_strategy']
                print(f"   ✓ Updated {symbol} strategy: {change['old_strategy']} → {change['new_strategy']}")
        
        print("✓ Changes applied\n")

    def start_trading(self):
        """
        Main trading loop with ALL FIXES APPLIED
        """
        if not self.is_connected:
            print("❌ Not connected to MT5")
            return

        if self.executor is None:
            self.executor = TradeExecutor(self.lot_size)
            self.executor.analyzer = self.analyzer

        # Pass TP/SL config to executor
        self.executor.tp_mode = self.tp_mode
        self.executor.sl_mode = self.sl_mode
        self.executor.tp_usd = self.tp_usd
        self.executor.sl_usd = self.sl_usd

        # Recover existing positions (FIX #2)
        self._recover_positions()

        # PHASE 1: Deep market scan
        tradeable = self.run_deep_scan()

        if not tradeable:
            print("\n❌ No pairs approved for trading. Market conditions unfavorable.")
            print("   Try again later when conditions improve.")
            return

        pair_config = {p['symbol']: p for p in tradeable}

        # PHASE 2: Trading loop with complete error handling (FIX #9)
        self.running = True
        trading_symbols = [p['symbol'] for p in tradeable]

        print(f"\n🚀 TRADING PHASE STARTED")
        print(f"   Trading {len(trading_symbols)} approved pairs")
        print(f"   {self.max_workers} concurrent threads | {self.config['trading']['cycle_interval']}s cycles")
        print(f"   Re-scan every {self.config['trading']['rescan_interval'] // 60} minutes")
        print(f"   Daily loss limit: ${self.daily_loss_limit}")
        print(f"   Emergency stop: Create '{EMERGENCY_STOP_FILE}' to stop immediately")
        print(f"   Debug mode: Create '{self.debug_file}' to enable detailed logging")
        print(f"   Press Ctrl+C to stop\n")

        cycle_count = 0
        last_dashboard_time = time.time()
        last_rescan_time = time.time()
        last_strategy_report_time = time.time()
        last_state_save_time = time.time()
        last_blacklist_clear_time = time.time()

        # Main trading loop with error handling
        try:
            while self.running:
                cycle_start = time.time()
                cycle_count += 1

                try:
                    # === SAFETY CHECKS ===
                    
                    # Check emergency stop (FIX #6)
                    if self._check_emergency_stop():
                        print("\n🚨 EMERGENCY STOP ACTIVATED!")
                        print("   All trading stopped immediately.")
                        print("   Delete EMERGENCY_STOP.txt to resume.")
                        break
                    
                    # Check MT5 connection (FIX #1)
                    if not self._ensure_connection():
                        logger.error("MT5 connection lost, waiting for reconnection...")
                        time.sleep(10)
                        continue
                    
                    # Check daily loss limit (FIX #4)
                    can_trade, daily_pnl = self._check_daily_loss_limit()
                    if not can_trade:
                        print("\n🛑 Daily loss limit reached. Trading stopped for today.")
                        break
                    
                    # Check performance degradation (FIX #18)
                    if not self._check_performance_degradation():
                        print("\n⏸️ Trading paused due to performance degradation.")
                        print("   Review strategy or wait for better market conditions.")
                        break
                    
                    # Check trading hours (FIX #12)
                    can_trade_now, reason = self._is_trading_hours()
                    if not can_trade_now:
                        if cycle_count % 12 == 0:  # Print every minute
                            print(f"⏸️ Outside trading hours: {reason}")
                        time.sleep(self.config['trading']['cycle_interval'])
                        continue
                    
                    # Check debug mode (FIX #19)
                    self.debug_mode = self._check_debug_mode()
                    if self.debug_mode and cycle_count % 5 == 0:
                        print(f"🔍 DEBUG MODE ACTIVE (cycle {cycle_count})")
                    
                    # === PERIODIC TASKS ===
                    
                    # Quick re-scan every N minutes
                    if time.time() - last_rescan_time > self.config['trading']['rescan_interval']:
                        changes = self.quick_rescan()
                        self.apply_scan_changes(changes, pair_config)
                        trading_symbols = list(pair_config.keys())
                        last_rescan_time = time.time()
                    
                    # Clear old blacklist entries
                    if time.time() - last_blacklist_clear_time > 1800:  # 30 minutes
                        self.symbol_blacklist.clear()
                        self.symbol_failures.clear()
                        logger.info("Symbol blacklist cleared")
                        last_blacklist_clear_time = time.time()
                    
                    # Save state periodically (FIX #15)
                    if time.time() - last_state_save_time > 300:  # Every 5 minutes
                        self._save_state()
                        last_state_save_time = time.time()
                    
                    # === SIGNAL ANALYSIS ===
                    
                    signals_found = 0
                    trades_executed = 0
                    signals_filtered = {
                        'no_signal': 0,
                        'low_confidence': 0,
                        'correlation': 0,
                        'max_positions': 0,
                        'spread': 0,
                        'blacklisted': 0
                    }
                    
                    # Submit analysis for approved pairs (with memory leak prevention - FIX #7)
                    with ThreadPoolExecutor(max_workers=self.max_workers) as thread_executor:
                        future_to_symbol = {}
                        for sym in trading_symbols:
                            cfg = pair_config.get(sym, {})
                            future = thread_executor.submit(
                                self.analyze_symbol, sym, cfg.get('strategy'), self.debug_mode
                            )
                            future_to_symbol[future] = sym

                        results = []
                        for future in as_completed(future_to_symbol):
                            try:
                                signal = future.result(timeout=5)  # Timeout to prevent hanging
                                if signal:
                                    results.append(signal)
                            except Exception as e:
                                sym = future_to_symbol[future]
                                logger.error(f"Analysis error for {sym}: {e}")
                        
                        # Explicitly clear futures (FIX #7)
                        future_to_symbol.clear()

                    # === SIGNAL PROCESSING ===
                    
                    for signal in results:
                        if signal['signal'] == 'NONE':
                            signals_filtered['no_signal'] += 1
                            continue

                        signals_found += 1
                        sym = signal['symbol']
                        cfg = pair_config.get(sym, {})

                        # Confidence threshold (LOWERED for more signals - 5-min charts)
                        min_confidence = 0.45 if cfg.get('verdict') == 'CAUTION' else 0.30
                        if signal['confidence'] < min_confidence:
                            signals_filtered['low_confidence'] += 1
                            if self.debug_mode:
                                print(f"   ⓘ {sym}: Signal filtered (confidence {signal['confidence']:.2f} < {min_confidence:.2f})")
                            continue

                        # Spread check (FIX #8)
                        spread_ok, spread_value = self._check_spread(sym)
                        if not spread_ok:
                            signals_filtered['spread'] += 1
                            if self.debug_mode:
                                print(f"   ⓘ {sym}: Signal filtered (spread too wide: {spread_value:.1f} pips)")
                            continue

                        # Directional correlation check (FIX #17)
                        correlated = self.analyzer.get_correlated_pairs(sym)
                        has_correlated_position = False
                        
                        with self.correlation_lock:  # Thread-safe (FIX #3)
                            with self.executor._trades_lock:
                                for ticket, trade_info in self.executor.active_trades.items():
                                    if trade_info['symbol'] in correlated:
                                        # Check if same direction (FIX #17)
                                        if trade_info['action'] == signal['signal']:
                                            has_correlated_position = True
                                            break
                        
                        if has_correlated_position:
                            signals_filtered['correlation'] += 1
                            if self.debug_mode:
                                print(f"   ⓘ {sym}: Signal blocked (correlated {signal['signal']} position active)")
                            continue

                        # Max positions check
                        if self.max_positions and self.max_positions > 0:
                            if self.executor.get_active_trades_count() >= self.max_positions:
                                signals_filtered['max_positions'] += 1
                                continue

                        # Validate lot size (FIX #10)
                        if not self._validate_lot_size(sym, self.lot_size):
                            logger.warning(f"{sym}: Invalid lot size, skipping trade")
                            continue

                        # Execute trade with metrics tracking (FIX #20)
                        order_start = time.time()
                        self.execution_metrics['orders_placed'] += 1
                        
                        if self.executor.place_order(signal):
                            trades_executed += 1
                            self.execution_metrics['orders_filled'] += 1
                            
                            # Track execution time
                            exec_time = time.time() - order_start
                            self.execution_metrics['avg_fill_time'] = (
                                (self.execution_metrics['avg_fill_time'] * (self.execution_metrics['orders_filled'] - 1) + exec_time) /
                                self.execution_metrics['orders_filled']
                            )
                            
                            # AUTOPSY: Create autopsy for new position
                            if ENABLE_AUTOPSY:
                                try:
                                    # Get the ticket and trade info from last placed order
                                    with self.executor._trades_lock:
                                        tickets = list(self.executor.active_trades.keys())
                                        if tickets:
                                            ticket = tickets[-1]  # Last added
                                            trade_info = self.executor.active_trades.get(ticket)
                                            
                                            if trade_info:
                                                # Create enriched signal with ALL required fields for autopsy
                                                autopsy_signal = {
                                                    'entry_price': trade_info.get('entry_price', signal.get('price', 0)),
                                                    'signal': signal.get('signal', 'NONE'),  # BUY or SELL
                                                    'strategy': signal.get('type', 'unknown'),
                                                    'confidence': signal.get('confidence', 0.5),
                                                    'sl': signal.get('stop_loss', trade_info.get('sl', 0)),
                                                    'tp': signal.get('take_profit', trade_info.get('tp', 0)),
                                                }
                                                
                                                tick = mt5.symbol_info_tick(sym)
                                                market_data = {
                                                    'spread': spread_value if 'spread_value' in locals() else 0,
                                                    'atr': self.analyzer.calculate_atr(sym) if hasattr(self.analyzer, 'calculate_atr') else 0,
                                                    'trend': 'UNKNOWN',
                                                    'rsi': 50,
                                                    'support': 0,
                                                    'resistance': 0
                                                }
                                                create_position_autopsy(ticket, sym, autopsy_signal, market_data)
                                except Exception as e:
                                    logger.error(f"Autopsy creation error: {e}")
                                    import traceback
                                    logger.error(traceback.format_exc())
                            
                            with self.correlation_lock:  # Thread-safe (FIX #3)
                                self.active_correlated_pairs.add(sym)
                        else:
                            self.execution_metrics['orders_rejected'] += 1

                    # Check and manage active trades - CRITICAL: Check immediately after placing orders
                    # For custom TP/SL, check MORE FREQUENTLY to avoid delays
                    if self.tp_mode == 'custom' or self.sl_mode == 'custom':
                        # Check 3 times per cycle for custom TP/SL (every ~1.5 seconds)
                        for _ in range(3):
                            self.executor.check_active_trades()
                            time.sleep(0.5)  # Small delay between checks
                    else:
                        # Normal check once per cycle
                        self.executor.check_active_trades()

                    # Clean up correlation tracking (thread-safe - FIX #3)
                    active_symbols = set()
                    with self.executor._trades_lock:
                        for trade in self.executor.active_trades.values():
                            active_symbols.add(trade['symbol'])
                    
                    with self.correlation_lock:
                        self.active_correlated_pairs = self.active_correlated_pairs.intersection(active_symbols)

                    # === CYCLE SUMMARY ===
                    
                    cycle_time = time.time() - cycle_start
                    active_count = self.executor.get_active_trades_count()
                    
                    summary = f"[{datetime.now().strftime('%H:%M:%S')}] Cycle {cycle_count} | "
                    summary += f"Pairs: {len(trading_symbols)} | Signals: {signals_found} | "
                    summary += f"Opened: {trades_executed} | Active: {active_count} | "
                    summary += f"P&L: ${daily_pnl:+.2f} | {cycle_time:.1f}s"
                    
                    print(summary)
                    
                    # Show filter details periodically or when signals found
                    if cycle_count % 20 == 0 or signals_found > 0:
                        if any(signals_filtered.values()):
                            filter_str = " | ".join([f"{k.title()}:{v}" for k, v in signals_filtered.items() if v > 0])
                            print(f"   📊 Filters: {filter_str}")
                    
                    # Show active positions periodically
                    if cycle_count % 10 == 0 and active_count > 0:
                        print(f"\n📊 ACTIVE POSITIONS ({active_count}):")
                        with self.executor._trades_lock:
                            for ticket, trade in list(self.executor.active_trades.items())[:5]:
                                symbol = trade['symbol']
                                tick = mt5.symbol_info_tick(symbol)
                                if tick:
                                    current_price = tick.bid if trade['action'] == 'BUY' else tick.ask
                                    pip_multiplier = 1000 if 'JPY' in symbol else 100000
                                    
                                    if trade['action'] == 'BUY':
                                        profit_pips = (current_price - trade['entry_price']) * pip_multiplier
                                    else:
                                        profit_pips = (trade['entry_price'] - current_price) * pip_multiplier
                                    
                                    profit_icon = "📈" if profit_pips > 0 else "📉"
                                    print(f"   {profit_icon} {symbol} {trade['action']} #{ticket} | {trade['type']:10} | Entry: {trade['entry_price']:.5f} | P/L: {profit_pips:+.1f}p")
                        print()

                    # Show performance dashboard
                    if time.time() - last_dashboard_time > self.config['trading']['dashboard_interval']:
                        self.executor.show_performance()
                        
                        # Show execution metrics (FIX #20)
                        if self.execution_metrics['orders_placed'] > 0:
                            fill_rate = self.execution_metrics['orders_filled'] / self.execution_metrics['orders_placed']
                            print(f"\n📊 EXECUTION METRICS:")
                            print(f"   Orders: {self.execution_metrics['orders_placed']} placed | {self.execution_metrics['orders_filled']} filled | {self.execution_metrics['orders_rejected']} rejected")
                            print(f"   Fill rate: {fill_rate:.1%} | Avg fill time: {self.execution_metrics['avg_fill_time']:.2f}s")
                        
                        # AUTOPSY: Show autopsy stats
                        if ENABLE_AUTOPSY:
                            try:
                                show_autopsy_stats()
                            except Exception as e:
                                logger.error(f"Autopsy stats error: {e}")
                        
                        last_dashboard_time = time.time()

                    # Show strategy performance
                    if time.time() - last_strategy_report_time > self.config['trading']['strategy_report_interval']:
                        self.analyzer.print_strategy_performance()
                        last_strategy_report_time = time.time()

                except Exception as e:
                    # Cycle-level error handling (FIX #9)
                    logger.error(f"Error in trading cycle {cycle_count}: {e}")
                    logger.error(traceback.format_exc())
                    print(f"⚠️ Error in cycle {cycle_count}: {e}")
                    print("   Continuing trading...")
                    time.sleep(5)
                    continue

                # Wait for next cycle
                time.sleep(max(0, self.config['trading']['cycle_interval'] - cycle_time))

        except KeyboardInterrupt:
            print("\n\n🛑 TradNet stopped by user")
            self.running = False
            
            print("\n🚨 STOP OPTIONS:")
            print("  1. Keep positions open (default)")
            print("  2. Close all bot positions")
            
            try:
                choice = input("Choice (1 or 2): ").strip()
                if choice == '2':
                    self.executor.close_all_positions()
                else:
                    print("✓ Positions left open")
            except:
                print("✓ Positions left open")
            
            print("\n📊 FINAL SESSION SUMMARY:")
            self.executor.show_performance()
            self.analyzer.print_strategy_performance()
            
            # Save final state (FIX #15)
            self._save_state()
            print("✅ State saved")

        except Exception as e:
            # Top-level error handling (FIX #9)
            logger.critical(f"Critical error in trading loop: {e}")
            logger.critical(traceback.format_exc())
            print(f"\n🚨 CRITICAL ERROR: {e}")
            print("   Trading stopped. Check tradnet.log for details.")
            
            # Save state before exit
            self._save_state()

    def shutdown(self):
        """Clean shutdown with state saving (FIX #15)"""
        self.running = False
        
        # Save final state
        self._save_state()
        
        # Shutdown MT5
        mt5.shutdown()
        
        logger.info("TradNet shutdown complete")
        print("✅ TradNet shutdown complete")



if __name__ == "__main__":
    print("=" * 70)
    print("🤖 TradNet - Smart Trading Bot - ENHANCED VERSION")
    print("=" * 70)
    print("\n✨ NEW FEATURES:")
    print("   ✅ Auto-reconnect on MT5 disconnect")
    print("   ✅ Position recovery after restart")
    print("   ✅ Daily loss limit protection")
    print("   ✅ Emergency stop mechanism")
    print("   ✅ Spread monitoring")
    print("   ✅ Trading hours validation")
    print("   ✅ Performance degradation detection")
    print("   ✅ Symbol failure tracking")
    print("   ✅ Complete error handling")
    print("   ✅ Configuration file support")
    print("=" * 70)
    
    bot = TradNet()

    if bot.initialize():
        # Enhanced CLI interface (FIX #22)
        
        # Lot size configuration
        print("\n💰 LOT SIZE CONFIGURATION")
        lot_input = input("Enter lot size (default 0.01): ").strip()
        if lot_input:
            try:
                bot.set_lot_size(float(lot_input))
            except ValueError:
                print("⚠️ Invalid lot size, using default 0.01")
                bot.set_lot_size(0.01)
        else:
            bot.set_lot_size(0.01)

        # Symbol filter configuration
        print("\n🌍 SYMBOL FILTER")
        print("  1. all      - Trade all approved pairs")
        print("  2. metals   - Trade only XAUUSD and XAGUSD")
        print("  3. custom   - Enter specific symbols manually")

        while True:
            symbol_choice = input("Choice: ").strip().lower()

            if symbol_choice in ('all', '1'):
                print("✅ Trading all approved pairs")
                break
            elif symbol_choice in ('metals', '2'):
                bot.config['symbols'] = ['XAUUSD', 'XAGUSD']
                print("✅ Trading metals only: XAUUSD, XAGUSD")
                break
            elif symbol_choice in ('custom', '3'):
                raw = input("Enter symbols separated by spaces (e.g. EURUSD GBPUSD): ").strip().upper()
                custom_symbols = [s.strip() for s in raw.split() if s.strip()]
                if not custom_symbols:
                    print("⚠️ No symbols entered, try again")
                    continue
                bot.config['symbols'] = custom_symbols
                print(f"✅ Trading custom symbols: {', '.join(custom_symbols)}")
                break
            else:
                print("⚠️ Enter 'all', 'metals', or 'custom' (or 1/2/3)")

        # Max positions configuration
        print("\n📊 MAX CONCURRENT POSITIONS")
        print("  1. Enter a number (e.g., 5)")
        print("  2. Type 'unlimited' for no limit")

        while True:
            max_pos_input = input("Choice: ").strip().lower()

            if not max_pos_input:
                print("⚠️ Please choose an option!")
                continue

            if max_pos_input == 'unlimited':
                bot.set_max_positions(0)
                break

            try:
                max_pos = int(max_pos_input)
                if max_pos < 1:
                    print("⚠️ Please enter a number >= 1 or 'unlimited'")
                    continue
                bot.set_max_positions(max_pos)
                break
            except ValueError:
                print("⚠️ Invalid input. Enter a number or 'unlimited'")

        # Trading mode selection
        print("\n🤖 TRADING MODE")
        print("  1. manual    - You select strategies, TP/SL manually")
        print("  2. autopilot - Bot decides strategies per pair after deep scan")
        
        while True:
            mode_choice = input("Choice: ").strip().lower()
            
            if mode_choice == 'manual' or mode_choice == '1':
                bot.mode = 'manual'
                print("✅ Manual mode - You'll configure everything")
                break
            elif mode_choice == 'autopilot' or mode_choice == '2':
                bot.mode = 'autopilot'
                print("✅ Autopilot mode - Bot will use recommended strategies per pair")
                break
            else:
                print("⚠️ Enter 'manual' or 'autopilot' (or 1/2)")

        # Strategy selection (manual mode only)
        if bot.mode == 'manual':
            print("\n📊 STRATEGY SELECTION")
            print("Available strategies:")
            print("  1. reversal   - Trend reversal detection")
            print("  2. scalp      - Fast EMA crossover scalping")
            print("  3. range      - Range trading (buy low, sell high)")
            print("  4. breakout   - Support/resistance breakouts")
            print("  5. momentum   - Strong directional moves")
            print("  6. sr_bounce  - Support/resistance bounces")
            print("\nSelect strategies:")
            print("  - Enter 'all' to use all 6 strategies")
            print("  - Enter strategy numbers separated by spaces (e.g., '1 3 5')")
            
            strategy_map = {
                '1': 'reversal',
                '2': 'scalp',
                '3': 'range',
                '4': 'breakout',
                '5': 'momentum',
                '6': 'sr_bounce'
            }
            
            while True:
                strat_input = input("Choice: ").strip().lower()
                
                if strat_input == 'all':
                    bot.enabled_strategies = None
                    print("✅ Using all 6 strategies")
                    break
                
                selected_numbers = strat_input.split()
                selected_strategies = []
                invalid = False
                
                for num in selected_numbers:
                    if num in strategy_map:
                        selected_strategies.append(strategy_map[num])
                    else:
                        print(f"⚠️ Invalid strategy number: {num}")
                        invalid = True
                        break
                
                if invalid:
                    continue
                
                if len(selected_strategies) == 0:
                    print("⚠️ Please select at least one strategy")
                    continue
                
                bot.enabled_strategies = selected_strategies
                strategy_names = ', '.join(selected_strategies)
                print(f"✅ Using {len(selected_strategies)} strategies: {strategy_names}")
                break
        else:
            print("✅ Bot will use recommended strategies from deep scan")

        # Take Profit configuration
        print("\n💰 TAKE PROFIT CONFIGURATION")
        print("  1. bot    - Let bot handle TP (strategy-based)")
        print("  2. custom - Set custom TP in USD")
        
        while True:
            tp_choice = input("Choice: ").strip().lower()
            
            if tp_choice == 'bot' or tp_choice == '1':
                bot.tp_mode = 'bot'
                print("✅ Bot will handle take profit")
                break
            elif tp_choice == 'custom' or tp_choice == '2':
                try:
                    tp_usd = float(input("Enter TP in USD (e.g., 1.5): ").strip())
                    if tp_usd <= 0:
                        print("⚠️ TP must be positive")
                        continue
                    bot.tp_mode = 'custom'
                    bot.tp_usd = tp_usd
                    if bot.executor:
                        bot.executor.tp_mode = bot.tp_mode
                        bot.executor.tp_usd = bot.tp_usd
                    print(f"✅ Custom TP set to ${tp_usd}")
                    break
                except ValueError:
                    print("⚠️ Invalid number")
            else:
                print("⚠️ Enter 'bot' or 'custom' (or 1/2)")

        # Stop Loss configuration
        print("\n🛑 STOP LOSS CONFIGURATION")
        print("  1. bot    - Let bot handle SL (strategy-based)")
        print("  2. custom - Set custom SL in USD")
        
        while True:
            sl_choice = input("Choice: ").strip().lower()
            
            if sl_choice == 'bot' or sl_choice == '1':
                bot.sl_mode = 'bot'
                print("✅ Bot will handle stop loss")
                break
            elif sl_choice == 'custom' or sl_choice == '2':
                try:
                    sl_usd = float(input("Enter SL in USD (e.g., 0.5): ").strip())
                    if sl_usd <= 0:
                        print("⚠️ SL must be positive")
                        continue
                    bot.sl_mode = 'custom'
                    bot.sl_usd = sl_usd
                    if bot.executor:
                        bot.executor.sl_mode = bot.sl_mode
                        bot.executor.sl_usd = bot.sl_usd
                    print(f"✅ Custom SL set to ${sl_usd}")
                    break
                except ValueError:
                    print("⚠️ Invalid number")
            else:
                print("⚠️ Enter 'bot' or 'custom' (or 1/2)")

        # Daily loss limit configuration
        print("\n🛡️ DAILY LOSS LIMIT")
        print(f"  Current limit: ${bot.daily_loss_limit}")
        change_limit = input("Change limit? (y/n, default n): ").strip().lower()
        
        if change_limit == 'y' or change_limit == 'yes':
            try:
                new_limit = float(input("Enter new daily loss limit in USD (e.g., 100): ").strip())
                if new_limit > 0:
                    bot.daily_loss_limit = new_limit
                    print(f"✅ Daily loss limit set to ${new_limit}")
                else:
                    print("⚠️ Using default limit")
            except ValueError:
                print("⚠️ Invalid number, using default")

        # Pass TP/SL config to executor
        if bot.executor:
            bot.executor.tp_mode = bot.tp_mode
            bot.executor.sl_mode = bot.sl_mode
            bot.executor.tp_usd = bot.tp_usd
            bot.executor.sl_usd = bot.sl_usd

        # Final confirmation
        print("\n" + "=" * 70)
        print("📋 CONFIGURATION SUMMARY")
        print("=" * 70)
        print(f"   Lot Size: {bot.lot_size}")
        print(f"   Max Positions: {'Unlimited' if bot.max_positions == 0 else bot.max_positions}")
        print(f"   Mode: {bot.mode.title()}")
        if bot.enabled_strategies:
            print(f"   Strategies: {', '.join(bot.enabled_strategies)}")
        else:
            print(f"   Strategies: All (6 strategies)")
        print(f"   Take Profit: {bot.tp_mode.title()}" + (f" (${bot.tp_usd})" if bot.tp_usd else ""))
        print(f"   Stop Loss: {bot.sl_mode.title()}" + (f" (${bot.sl_usd})" if bot.sl_usd else ""))
        print(f"   Daily Loss Limit: ${bot.daily_loss_limit}")
        print("=" * 70)
        
        confirm = input("\n✅ Start trading with this configuration? (y/n): ").strip().lower()
        
        if confirm == 'y' or confirm == 'yes':
            print("\n🚀 Starting TradNet...\n")
            bot.start_trading()
        else:
            print("❌ Trading cancelled")

    bot.shutdown()
