"""
Performance Tracker - Real-time statistics dashboard with persistence and advanced metrics
performance_tracker.py
"""

import json
import logging
from typing import Dict, List, Optional, Tuple
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path
import statistics

logger = logging.getLogger(__name__)


class PerformanceTracker:
    """
    Enhanced performance tracker with:
    - Data persistence (JSON)
    - Risk metrics (Sharpe, profit factor, expectancy)
    - Time-based analysis (hourly, daily, weekly)
    - Drawdown tracking and recovery
    - Position sizing metrics
    - Trade quality metrics
    - Export functionality
    - Multi-session tracking
    """
    
    def __init__(self, data_file: str = "performance_data.json",
                 history_file: str = "trade_history.json"):
        self.data_file = data_file
        self.history_file = history_file
        
        # Core data
        self.trades = []
        self.stats_by_strategy = defaultdict(lambda: {
            'wins': 0, 'losses': 0, 'total_pips': 0, 'total_risk': 0,
            'win_pips': 0, 'loss_pips': 0, 'trade_count': 0
        })
        self.stats_by_symbol = defaultdict(lambda: {
            'wins': 0, 'losses': 0, 'total_pips': 0, 'trade_count': 0
        })
        self.stats_by_hour = defaultdict(lambda: {
            'wins': 0, 'losses': 0, 'total_pips': 0, 'trade_count': 0
        })
        self.stats_by_day = defaultdict(lambda: {
            'wins': 0, 'losses': 0, 'total_pips': 0, 'trade_count': 0
        })
        
        self.session_start = datetime.now()
        
        # Pre-scan tracking
        self.scan_reports = {}
        self.approved_pairs = []
        self.caution_pairs = []
        self.avoided_pairs = []
        
        # Enhanced tracking
        self.recent_results = deque(maxlen=20)  # Last 20 trades
        self.current_streak = 0
        self.max_win_streak = 0
        self.max_loss_streak = 0
        self.max_drawdown_pips = 0
        self.peak_pips = 0
        self.drawdown_start_time = None
        self.drawdown_duration_max = timedelta(0)
        
        # Risk metrics
        self.daily_returns = []  # For Sharpe ratio
        self.risk_free_rate = 0.02  # 2% annual
        
        # Load existing data
        self._load_data()

    # ------------------------------------------------------------------ #
    #  DATA PERSISTENCE
    # ------------------------------------------------------------------ #
    
    def _load_data(self):
        """Load performance data from disk"""
        try:
            if Path(self.data_file).exists():
                with open(self.data_file, 'r') as f:
                    data = json.load(f)
                
                # Restore trades
                self.trades = data.get('trades', [])
                
                # Restore stats
                self.stats_by_strategy = defaultdict(lambda: {
                    'wins': 0, 'losses': 0, 'total_pips': 0, 'total_risk': 0,
                    'win_pips': 0, 'loss_pips': 0, 'trade_count': 0
                }, data.get('stats_by_strategy', {}))
                
                self.stats_by_symbol = defaultdict(lambda: {
                    'wins': 0, 'losses': 0, 'total_pips': 0, 'trade_count': 0
                }, data.get('stats_by_symbol', {}))
                
                self.stats_by_hour = defaultdict(lambda: {
                    'wins': 0, 'losses': 0, 'total_pips': 0, 'trade_count': 0
                }, data.get('stats_by_hour', {}))
                
                self.stats_by_day = defaultdict(lambda: {
                    'wins': 0, 'losses': 0, 'total_pips': 0, 'trade_count': 0
                }, data.get('stats_by_day', {}))
                
                # Restore metrics
                self.max_drawdown_pips = data.get('max_drawdown_pips', 0)
                self.peak_pips = data.get('peak_pips', 0)
                self.max_win_streak = data.get('max_win_streak', 0)
                self.max_loss_streak = data.get('max_loss_streak', 0)
                
                # Rebuild recent results
                if self.trades:
                    recent_trades = self.trades[-20:]
                    self.recent_results = deque([t['profit_pips'] for t in recent_trades], maxlen=20)
                    
                    # Recalculate current streak
                    self._recalculate_streak()
                
                logger.info(f"Loaded {len(self.trades)} trades from {self.data_file}")
                
        except Exception as e:
            logger.warning(f"Could not load performance data: {e}")
    
    def _save_data(self):
        """Save performance data to disk"""
        try:
            data = {
                'trades': self.trades,
                'stats_by_strategy': dict(self.stats_by_strategy),
                'stats_by_symbol': dict(self.stats_by_symbol),
                'stats_by_hour': dict(self.stats_by_hour),
                'stats_by_day': dict(self.stats_by_day),
                'max_drawdown_pips': self.max_drawdown_pips,
                'peak_pips': self.peak_pips,
                'max_win_streak': self.max_win_streak,
                'max_loss_streak': self.max_loss_streak,
                'last_updated': datetime.now().isoformat()
            }
            
            with open(self.data_file, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            
            logger.debug(f"Saved performance data to {self.data_file}")
            
        except Exception as e:
            logger.error(f"Could not save performance data: {e}")
    
    def _recalculate_streak(self):
        """Recalculate current streak from recent results"""
        if not self.recent_results:
            self.current_streak = 0
            return
        
        self.current_streak = 0
        for result in reversed(self.recent_results):
            if result > 0:
                if self.current_streak >= 0:
                    self.current_streak += 1
                else:
                    break
            else:
                if self.current_streak <= 0:
                    self.current_streak -= 1
                else:
                    break

    def record_scan_report(self, report: Dict):
        """Record a deep scan report for a pair"""
        symbol = report['symbol']
        self.scan_reports[symbol] = report

        if report['verdict'] == 'APPROVED':
            self.approved_pairs.append(symbol)
        elif report['verdict'] == 'CAUTION':
            self.caution_pairs.append(symbol)
        elif report['verdict'] == 'AVOID':
            self.avoided_pairs.append(symbol)

    def print_scan_summary(self):
        """Print the full pre-trade scan summary"""
        total = len(self.scan_reports)
        print("\n" + "=" * 70)
        print("🔍 PRE-TRADE MARKET SCAN REPORT")
        print("=" * 70)
        print(f"   Scanned: {total} pairs")
        print(f"   ✅ Approved: {len(self.approved_pairs)}")
        print(f"   ⚠️  Caution:  {len(self.caution_pairs)}")
        print(f"   ❌ Avoided:  {len(self.avoided_pairs)}")

        # Approved pairs detail
        if self.approved_pairs:
            print(f"\n✅ APPROVED FOR TRADING:")
            for sym in self.approved_pairs:
                r = self.scan_reports[sym]
                strategy = r.get('recommended_strategy', '?')
                risk = r.get('risk_score', '?')
                reasons = ', '.join(r.get('reasons', [])[:2])
                print(f"   {sym:<12} | Strategy: {strategy:<10} | Risk: {risk:<3} | {reasons}")

        # Caution pairs detail
        if self.caution_pairs:
            print(f"\n⚠️  CAUTION (trading with extra filters):")
            for sym in self.caution_pairs:
                r = self.scan_reports[sym]
                strategy = r.get('recommended_strategy', '?')
                risk = r.get('risk_score', '?')
                reasons = ', '.join(r.get('reasons', [])[:2])
                print(f"   {sym:<12} | Strategy: {strategy:<10} | Risk: {risk:<3} | {reasons}")

        # Avoided pairs
        if self.avoided_pairs:
            print(f"\n❌ AVOIDED (too risky):")
            for sym in self.avoided_pairs:
                r = self.scan_reports[sym]
                risk = r.get('risk_score', '?')
                reasons = ', '.join(r.get('reasons', [])[:2])
                print(f"   {sym:<12} | Risk: {risk:<3} | {reasons}")

        print("=" * 70 + "\n")

    def get_tradeable_pairs(self) -> List[Dict]:
        """Return approved + caution pairs with their recommended strategies"""
        pairs = []
        for sym in self.approved_pairs + self.caution_pairs:
            r = self.scan_reports[sym]
            pairs.append({
                'symbol': sym,
                'recommended_strategy': r.get('recommended_strategy'),
                'strategy_confidence': r.get('strategy_confidence', 0),
                'risk_level': r.get('risk_level'),
                'risk_score': r.get('risk_score', 100),
                'verdict': r.get('verdict')
            })
        return pairs

    # ------------------------------------------------------------------ #
    #  TRADE TRACKING METHODS (ENHANCED)
    # ------------------------------------------------------------------ #


    # ------------------------------------------------------------------ #
    #  PRE-SCAN REPORT METHODS
    # ------------------------------------------------------------------ #

    def record_scan_report(self, report: Dict):
        """Record a deep scan report for a pair"""
        symbol = report['symbol']
        self.scan_reports[symbol] = report

        if report['verdict'] == 'APPROVED':
            self.approved_pairs.append(symbol)
        elif report['verdict'] == 'CAUTION':
            self.caution_pairs.append(symbol)
        elif report['verdict'] == 'AVOID':
            self.avoided_pairs.append(symbol)

    def print_scan_summary(self):
        """Print the full pre-trade scan summary"""
        total = len(self.scan_reports)
        print("\n" + "=" * 70)
        print("🔍 PRE-TRADE MARKET SCAN REPORT")
        print("=" * 70)
        print(f"   Scanned: {total} pairs")
        print(f"   ✅ Approved: {len(self.approved_pairs)}")
        print(f"   ⚠️  Caution:  {len(self.caution_pairs)}")
        print(f"   ❌ Avoided:  {len(self.avoided_pairs)}")

        # Approved pairs detail
        if self.approved_pairs:
            print(f"\n✅ APPROVED FOR TRADING:")
            for sym in self.approved_pairs:
                r = self.scan_reports[sym]
                strategy = r.get('recommended_strategy', '?')
                risk = r.get('risk_score', '?')
                reasons = ', '.join(r.get('reasons', [])[:2])
                print(f"   {sym:<12} | Strategy: {strategy:<10} | Risk: {risk:<3} | {reasons}")

        # Caution pairs detail
        if self.caution_pairs:
            print(f"\n⚠️  CAUTION (trading with extra filters):")
            for sym in self.caution_pairs:
                r = self.scan_reports[sym]
                strategy = r.get('recommended_strategy', '?')
                risk = r.get('risk_score', '?')
                reasons = ', '.join(r.get('reasons', [])[:2])
                print(f"   {sym:<12} | Strategy: {strategy:<10} | Risk: {risk:<3} | {reasons}")

        # Avoided pairs
        if self.avoided_pairs:
            print(f"\n❌ AVOIDED (too risky):")
            for sym in self.avoided_pairs:
                r = self.scan_reports[sym]
                risk = r.get('risk_score', '?')
                reasons = ', '.join(r.get('reasons', [])[:2])
                print(f"   {sym:<12} | Risk: {risk:<3} | {reasons}")

        print("=" * 70 + "\n")

    def get_tradeable_pairs(self) -> List[Dict]:
        """Return approved + caution pairs with their recommended strategies"""
        pairs = []
        for sym in self.approved_pairs + self.caution_pairs:
            r = self.scan_reports[sym]
            pairs.append({
                'symbol': sym,
                'recommended_strategy': r.get('recommended_strategy'),
                'strategy_confidence': r.get('strategy_confidence', 0),
                'risk_level': r.get('risk_level'),
                'risk_score': r.get('risk_score', 100),
                'verdict': r.get('verdict')
            })
        return pairs

    # ------------------------------------------------------------------ #
    #  TRADE TRACKING METHODS (ENHANCED)
    # ------------------------------------------------------------------ #

    def record_trade(self, symbol: str, strategy: str, profit_pips: float,
                    lot_size: float = 0.01, risk_pips: float = None,
                    entry_quality: float = None, exit_quality: float = None,
                    duration_minutes: float = None, tags: List[str] = None):
        """
        Record a completed trade with comprehensive metrics
        
        Args:
            symbol: Trading pair
            strategy: Strategy used
            profit_pips: Profit/loss in pips
            lot_size: Position size
            risk_pips: Stop loss distance in pips
            entry_quality: Entry quality score (0-1)
            exit_quality: Exit quality score (0-1)
            duration_minutes: Trade duration
            tags: Optional tags for categorization
        """
        now = datetime.now()
        
        trade = {
            'symbol': symbol,
            'strategy': strategy,
            'profit_pips': profit_pips,
            'lot_size': lot_size,
            'risk_pips': risk_pips,
            'r_multiple': (profit_pips / risk_pips) if risk_pips and risk_pips > 0 else None,
            'entry_quality': entry_quality,
            'exit_quality': exit_quality,
            'duration_minutes': duration_minutes,
            'tags': tags or [],
            'timestamp': now.isoformat(),
            'hour': now.hour,
            'day_of_week': now.weekday(),
            'date': now.date().isoformat()
        }
        
        self.trades.append(trade)
        
        # Update streak tracking
        is_win = profit_pips > 0
        if is_win:
            if self.current_streak > 0:
                self.current_streak += 1
            else:
                self.current_streak = 1
            
            if self.current_streak > self.max_win_streak:
                self.max_win_streak = self.current_streak
        else:
            if self.current_streak < 0:
                self.current_streak -= 1
            else:
                self.current_streak = -1
            
            if abs(self.current_streak) > self.max_loss_streak:
                self.max_loss_streak = abs(self.current_streak)
        
        self.recent_results.append(profit_pips)

        # Update drawdown tracking
        total_pips = sum(t['profit_pips'] for t in self.trades)
        if total_pips > self.peak_pips:
            self.peak_pips = total_pips
            self.drawdown_start_time = None  # Reset drawdown
        
        drawdown = self.peak_pips - total_pips
        if drawdown > self.max_drawdown_pips:
            self.max_drawdown_pips = drawdown
            if self.drawdown_start_time is None:
                self.drawdown_start_time = now
        
        # Track drawdown duration
        if self.drawdown_start_time and drawdown > 0:
            duration = now - self.drawdown_start_time
            if duration > self.drawdown_duration_max:
                self.drawdown_duration_max = duration

        # Update strategy stats
        stats = self.stats_by_strategy[strategy]
        stats['trade_count'] += 1
        if is_win:
            stats['wins'] += 1
            stats['win_pips'] += profit_pips
        else:
            stats['losses'] += 1
            stats['loss_pips'] += abs(profit_pips)
        stats['total_pips'] += profit_pips
        if risk_pips:
            stats['total_risk'] += risk_pips

        # Update symbol stats
        sym_stats = self.stats_by_symbol[symbol]
        sym_stats['trade_count'] += 1
        if is_win:
            sym_stats['wins'] += 1
        else:
            sym_stats['losses'] += 1
        sym_stats['total_pips'] += profit_pips

        # Update time-based stats
        hour_stats = self.stats_by_hour[now.hour]
        hour_stats['trade_count'] += 1
        if is_win:
            hour_stats['wins'] += 1
        else:
            hour_stats['losses'] += 1
        hour_stats['total_pips'] += profit_pips
        
        day_stats = self.stats_by_day[now.weekday()]
        day_stats['trade_count'] += 1
        if is_win:
            day_stats['wins'] += 1
        else:
            day_stats['losses'] += 1
        day_stats['total_pips'] += profit_pips
        
        # Save to disk
        self._save_data()
        
        logger.info(f"Recorded trade: {symbol} {strategy} {profit_pips:+.1f}p")

    def get_strategy_stats(self) -> Dict:
        """Get performance by strategy"""
        stats = {}
        for strategy, data in self.stats_by_strategy.items():
            total = data['wins'] + data['losses']
            win_rate = (data['wins'] / total * 100) if total > 0 else 0
            avg_pips = data['total_pips'] / total if total > 0 else 0

            stats[strategy] = {
                'trades': total,
                'wins': data['wins'],
                'losses': data['losses'],
                'win_rate': win_rate,
                'total_pips': data['total_pips'],
                'avg_pips': avg_pips
            }
        return stats

    def get_symbol_stats(self, top_n: int = 5) -> Dict:
        """Get top/bottom performing symbols"""
        symbol_list = []
        for symbol, data in self.stats_by_symbol.items():
            total = data['wins'] + data['losses']
            if total > 0:
                symbol_list.append({
                    'symbol': symbol,
                    'trades': total,
                    'total_pips': data['total_pips'],
                    'win_rate': (data['wins'] / total * 100)
                })

        symbol_list.sort(key=lambda x: x['total_pips'], reverse=True)

        return {
            'best': symbol_list[:top_n],
            'worst': symbol_list[-top_n:][::-1]
        }

    def get_overall_stats(self) -> Dict:
        """Get overall session statistics"""
        total_trades = len(self.trades)
        if total_trades == 0:
            return {
                'total_trades': 0,
                'wins': 0,
                'losses': 0,
                'win_rate': 0,
                'total_pips': 0,
                'avg_pips': 0,
                'session_duration': str(datetime.now() - self.session_start).split('.')[0],
                'current_streak': 0,
                'max_drawdown': 0
            }

        wins = sum(1 for t in self.trades if t['profit_pips'] > 0)
        losses = total_trades - wins
        total_pips = sum(t['profit_pips'] for t in self.trades)

        return {
            'total_trades': total_trades,
            'wins': wins,
            'losses': losses,
            'win_rate': (wins / total_trades * 100),
            'total_pips': total_pips,
            'avg_pips': total_pips / total_trades,
            'session_duration': str(datetime.now() - self.session_start).split('.')[0],
            'current_streak': self.current_streak,
            'max_drawdown': self.max_drawdown_pips
        }

    def print_dashboard(self, active_positions: int = 0):
        """Print formatted performance dashboard"""
        overall = self.get_overall_stats()
        strategy_stats = self.get_strategy_stats()
        symbol_stats = self.get_symbol_stats(top_n=3)

        print("\n" + "=" * 60)
        print("📊 PERFORMANCE DASHBOARD")
        print("=" * 60)

        # Overall stats
        streak_icon = "🔥" if overall['current_streak'] > 2 else "❄️" if overall['current_streak'] < -2 else "➖"
        print(f"\n🎯 OVERALL SESSION ({overall['session_duration']})")
        print(f"   Trades: {overall['total_trades']} | Wins: {overall['wins']} | Losses: {overall['losses']} | Active: {active_positions}")
        print(f"   Win Rate: {overall['win_rate']:.1f}% | Total: {overall['total_pips']:+.1f} pips | Avg: {overall['avg_pips']:+.1f} pips")
        print(f"   Streak: {streak_icon} {overall['current_streak']:+d} | Max DD: {overall['max_drawdown']:.1f}p")
        
        if active_positions > 0:
            print(f"   ℹ️  Note: {active_positions} position(s) still open (not counted until closed)")

        # Strategy breakdown
        if strategy_stats:
            print(f"\n📈 BY STRATEGY")
            for strategy, stats in strategy_stats.items():
                print(f"   {strategy.upper():10}: {stats['trades']} trades | "
                      f"WR: {stats['win_rate']:.0f}% | "
                      f"Total: {stats['total_pips']:+.1f}p | "
                      f"Avg: {stats['avg_pips']:+.1f}p")

        # Best/worst pairs
        if symbol_stats['best']:
            print(f"\n✅ TOP PAIRS")
            for s in symbol_stats['best']:
                print(f"   {s['symbol']}: {s['total_pips']:+.1f}p ({s['trades']} trades, {s['win_rate']:.0f}% WR)")

        if symbol_stats['worst']:
            print(f"\n❌ WORST PAIRS")
            for s in symbol_stats['worst']:
                print(f"   {s['symbol']}: {s['total_pips']:+.1f}p ({s['trades']} trades, {s['win_rate']:.0f}% WR)")

        print("=" * 60 + "\n")

    # ------------------------------------------------------------------ #
    #  RISK METRICS
    # ------------------------------------------------------------------ #
    
    def calculate_expectancy(self) -> float:
        """
        Calculate expectancy (expected value per trade)
        Positive = profitable system
        """
        if not self.trades:
            return 0.0
        
        wins = [t['profit_pips'] for t in self.trades if t['profit_pips'] > 0]
        losses = [abs(t['profit_pips']) for t in self.trades if t['profit_pips'] < 0]
        
        if not wins and not losses:
            return 0.0
        
        win_rate = len(wins) / len(self.trades)
        loss_rate = 1 - win_rate
        
        avg_win = statistics.mean(wins) if wins else 0
        avg_loss = statistics.mean(losses) if losses else 0
        
        expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)
        return expectancy
    
    def calculate_profit_factor(self) -> float:
        """
        Calculate profit factor (gross profit / gross loss)
        > 1.0 = profitable
        """
        gross_profit = sum(t['profit_pips'] for t in self.trades if t['profit_pips'] > 0)
        gross_loss = abs(sum(t['profit_pips'] for t in self.trades if t['profit_pips'] < 0))
        
        if gross_loss == 0:
            return float('inf') if gross_profit > 0 else 0.0
        
        return gross_profit / gross_loss
    
    def calculate_sharpe_ratio(self, periods_per_year: int = 252) -> float:
        """
        Calculate Sharpe ratio (risk-adjusted return)
        > 1.0 = good, > 2.0 = very good, > 3.0 = excellent
        """
        if len(self.trades) < 2:
            return 0.0
        
        returns = [t['profit_pips'] for t in self.trades]
        
        if not returns:
            return 0.0
        
        avg_return = statistics.mean(returns)
        std_return = statistics.stdev(returns) if len(returns) > 1 else 0
        
        if std_return == 0:
            return 0.0
        
        # Annualized Sharpe
        sharpe = (avg_return - (self.risk_free_rate / periods_per_year)) / std_return
        sharpe_annualized = sharpe * (periods_per_year ** 0.5)
        
        return sharpe_annualized
    
    def calculate_max_consecutive_losses(self) -> int:
        """Calculate maximum consecutive losses"""
        if not self.trades:
            return 0
        
        max_losses = 0
        current_losses = 0
        
        for trade in self.trades:
            if trade['profit_pips'] < 0:
                current_losses += 1
                max_losses = max(max_losses, current_losses)
            else:
                current_losses = 0
        
        return max_losses
    
    def get_risk_metrics(self) -> Dict:
        """Get comprehensive risk metrics"""
        return {
            'expectancy': self.calculate_expectancy(),
            'profit_factor': self.calculate_profit_factor(),
            'sharpe_ratio': self.calculate_sharpe_ratio(),
            'max_drawdown_pips': self.max_drawdown_pips,
            'max_drawdown_duration': str(self.drawdown_duration_max).split('.')[0],
            'max_consecutive_losses': self.calculate_max_consecutive_losses(),
            'max_win_streak': self.max_win_streak,
            'max_loss_streak': self.max_loss_streak,
            'current_streak': self.current_streak
        }
    
    # ------------------------------------------------------------------ #
    #  TIME-BASED ANALYSIS
    # ------------------------------------------------------------------ #
    
    def get_hourly_performance(self) -> Dict:
        """Get performance by hour of day"""
        hourly = {}
        for hour, stats in self.stats_by_hour.items():
            if stats['trade_count'] > 0:
                hourly[hour] = {
                    'trades': stats['trade_count'],
                    'wins': stats['wins'],
                    'losses': stats['losses'],
                    'win_rate': (stats['wins'] / stats['trade_count'] * 100),
                    'total_pips': stats['total_pips'],
                    'avg_pips': stats['total_pips'] / stats['trade_count']
                }
        # Fix: Convert all keys to int before sorting
        try:
            return dict(sorted(hourly.items(), key=lambda x: int(x[0])))
        except (ValueError, TypeError):
            # If conversion fails, return unsorted
            return hourly
    
    def get_daily_performance(self) -> Dict:
        """Get performance by day of week"""
        days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        daily = {}
        
        for day_num, stats in self.stats_by_day.items():
            if stats['trade_count'] > 0:
                daily[days[day_num]] = {
                    'trades': stats['trade_count'],
                    'wins': stats['wins'],
                    'losses': stats['losses'],
                    'win_rate': (stats['wins'] / stats['trade_count'] * 100),
                    'total_pips': stats['total_pips'],
                    'avg_pips': stats['total_pips'] / stats['trade_count']
                }
        return daily
    
    def get_best_trading_hours(self, top_n: int = 3) -> List[Tuple[int, float]]:
        """Get best performing hours"""
        hourly = self.get_hourly_performance()
        sorted_hours = sorted(hourly.items(), key=lambda x: x[1]['avg_pips'], reverse=True)
        return [(hour, stats['avg_pips']) for hour, stats in sorted_hours[:top_n]]
    
    def get_worst_trading_hours(self, top_n: int = 3) -> List[Tuple[int, float]]:
        """Get worst performing hours"""
        hourly = self.get_hourly_performance()
        sorted_hours = sorted(hourly.items(), key=lambda x: x[1]['avg_pips'])
        return [(hour, stats['avg_pips']) for hour, stats in sorted_hours[:top_n]]

    # ------------------------------------------------------------------ #
    #  STATISTICS & ANALYSIS
    # ------------------------------------------------------------------ #

    def get_strategy_stats(self) -> Dict:
        """Get performance by strategy with advanced metrics"""
        stats = {}
        for strategy, data in self.stats_by_strategy.items():
            total = data['trade_count']
            if total == 0:
                continue
            
            win_rate = (data['wins'] / total * 100)
            avg_pips = data['total_pips'] / total
            avg_win = data['win_pips'] / data['wins'] if data['wins'] > 0 else 0
            avg_loss = data['loss_pips'] / data['losses'] if data['losses'] > 0 else 0
            
            # Profit factor for this strategy
            profit_factor = (data['win_pips'] / data['loss_pips']) if data['loss_pips'] > 0 else float('inf')
            
            # Expectancy for this strategy
            expectancy = (win_rate/100 * avg_win) - ((100-win_rate)/100 * avg_loss)

            stats[strategy] = {
                'trades': total,
                'wins': data['wins'],
                'losses': data['losses'],
                'win_rate': win_rate,
                'total_pips': data['total_pips'],
                'avg_pips': avg_pips,
                'avg_win': avg_win,
                'avg_loss': avg_loss,
                'profit_factor': profit_factor,
                'expectancy': expectancy
            }
        return stats

    def get_symbol_stats(self, top_n: int = 5) -> Dict:
        """Get top/bottom performing symbols"""
        symbol_list = []
        for symbol, data in self.stats_by_symbol.items():
            total = data['trade_count']
            if total > 0:
                symbol_list.append({
                    'symbol': symbol,
                    'trades': total,
                    'total_pips': data['total_pips'],
                    'win_rate': (data['wins'] / total * 100),
                    'avg_pips': data['total_pips'] / total
                })

        symbol_list.sort(key=lambda x: x['total_pips'], reverse=True)

        return {
            'best': symbol_list[:top_n],
            'worst': symbol_list[-top_n:][::-1] if len(symbol_list) >= top_n else []
        }

    def get_overall_stats(self) -> Dict:
        """Get overall session statistics with advanced metrics"""
        total_trades = len(self.trades)
        if total_trades == 0:
            return {
                'total_trades': 0,
                'wins': 0,
                'losses': 0,
                'win_rate': 0,
                'total_pips': 0,
                'avg_pips': 0,
                'session_duration': str(datetime.now() - self.session_start).split('.')[0],
                'current_streak': 0,
                'max_win_streak': 0,
                'max_loss_streak': 0,
                'max_drawdown': 0,
                'expectancy': 0,
                'profit_factor': 0,
                'sharpe_ratio': 0
            }

        wins = sum(1 for t in self.trades if t['profit_pips'] > 0)
        losses = total_trades - wins
        total_pips = sum(t['profit_pips'] for t in self.trades)
        
        risk_metrics = self.get_risk_metrics()

        return {
            'total_trades': total_trades,
            'wins': wins,
            'losses': losses,
            'win_rate': (wins / total_trades * 100),
            'total_pips': total_pips,
            'avg_pips': total_pips / total_trades,
            'session_duration': str(datetime.now() - self.session_start).split('.')[0],
            'current_streak': self.current_streak,
            'max_win_streak': self.max_win_streak,
            'max_loss_streak': self.max_loss_streak,
            'max_drawdown': self.max_drawdown_pips,
            'expectancy': risk_metrics['expectancy'],
            'profit_factor': risk_metrics['profit_factor'],
            'sharpe_ratio': risk_metrics['sharpe_ratio']
        }
    
    def get_win_loss_distribution(self) -> Dict:
        """Get distribution of wins and losses"""
        if not self.trades:
            return {'wins': [], 'losses': []}
        
        wins = [t['profit_pips'] for t in self.trades if t['profit_pips'] > 0]
        losses = [t['profit_pips'] for t in self.trades if t['profit_pips'] < 0]
        
        return {
            'wins': {
                'count': len(wins),
                'mean': statistics.mean(wins) if wins else 0,
                'median': statistics.median(wins) if wins else 0,
                'stdev': statistics.stdev(wins) if len(wins) > 1 else 0,
                'min': min(wins) if wins else 0,
                'max': max(wins) if wins else 0
            },
            'losses': {
                'count': len(losses),
                'mean': statistics.mean(losses) if losses else 0,
                'median': statistics.median(losses) if losses else 0,
                'stdev': statistics.stdev(losses) if len(losses) > 1 else 0,
                'min': min(losses) if losses else 0,
                'max': max(losses) if losses else 0
            }
        }
    
    def get_r_multiple_stats(self) -> Dict:
        """Get R-multiple statistics (risk-reward analysis)"""
        r_multiples = [t['r_multiple'] for t in self.trades if t.get('r_multiple') is not None]
        
        if not r_multiples:
            return {'available': False}
        
        return {
            'available': True,
            'count': len(r_multiples),
            'mean': statistics.mean(r_multiples),
            'median': statistics.median(r_multiples),
            'positive_r': sum(1 for r in r_multiples if r > 0),
            'negative_r': sum(1 for r in r_multiples if r < 0),
            'avg_win_r': statistics.mean([r for r in r_multiples if r > 0]) if any(r > 0 for r in r_multiples) else 0,
            'avg_loss_r': statistics.mean([r for r in r_multiples if r < 0]) if any(r < 0 for r in r_multiples) else 0
        }
    
    # ------------------------------------------------------------------ #
    #  FILTERING & QUERIES
    # ------------------------------------------------------------------ #
    
    def get_trades_by_date_range(self, start_date: str, end_date: str) -> List[Dict]:
        """Get trades within date range (YYYY-MM-DD format)"""
        return [
            t for t in self.trades
            if start_date <= t['date'] <= end_date
        ]
    
    def get_trades_by_strategy(self, strategy: str) -> List[Dict]:
        """Get all trades for a specific strategy"""
        return [t for t in self.trades if t['strategy'] == strategy]
    
    def get_trades_by_symbol(self, symbol: str) -> List[Dict]:
        """Get all trades for a specific symbol"""
        return [t for t in self.trades if t['symbol'] == symbol]
    
    def get_trades_by_tag(self, tag: str) -> List[Dict]:
        """Get all trades with a specific tag"""
        return [t for t in self.trades if tag in t.get('tags', [])]
    
    def get_recent_trades(self, n: int = 10) -> List[Dict]:
        """Get N most recent trades"""
        return self.trades[-n:] if self.trades else []

    # ------------------------------------------------------------------ #
    #  EXPORT & REPORTING
    # ------------------------------------------------------------------ #
    
    def export_to_csv(self, filename: str = "trades_export.csv"):
        """Export all trades to CSV file"""
        if not self.trades:
            logger.warning("No trades to export")
            return False
        
        try:
            import csv
            
            with open(filename, 'w', newline='') as f:
                fieldnames = ['timestamp', 'symbol', 'strategy', 'profit_pips', 'lot_size',
                            'risk_pips', 'r_multiple', 'entry_quality', 'exit_quality',
                            'duration_minutes', 'hour', 'day_of_week', 'tags']
                
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                
                for trade in self.trades:
                    row = {k: trade.get(k, '') for k in fieldnames}
                    row['tags'] = ','.join(trade.get('tags', []))
                    writer.writerow(row)
            
            logger.info(f"Exported {len(self.trades)} trades to {filename}")
            return True
            
        except Exception as e:
            logger.error(f"Export failed: {e}")
            return False
    
    def export_equity_curve(self, filename: str = "equity_curve.json"):
        """Export equity curve data for plotting"""
        if not self.trades:
            return False
        
        try:
            equity_curve = []
            cumulative_pips = 0
            
            for trade in self.trades:
                cumulative_pips += trade['profit_pips']
                equity_curve.append({
                    'timestamp': trade['timestamp'],
                    'cumulative_pips': cumulative_pips,
                    'trade_pips': trade['profit_pips']
                })
            
            with open(filename, 'w') as f:
                json.dump(equity_curve, f, indent=2)
            
            logger.info(f"Exported equity curve to {filename}")
            return True
            
        except Exception as e:
            logger.error(f"Equity curve export failed: {e}")
            return False
    
    def generate_performance_report(self, filename: str = "performance_report.txt"):
        """Generate comprehensive text report"""
        try:
            with open(filename, 'w') as f:
                f.write("="*70 + "\n")
                f.write("PERFORMANCE REPORT\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("="*70 + "\n\n")
                
                # Overall stats
                overall = self.get_overall_stats()
                f.write("OVERALL STATISTICS\n")
                f.write("-"*70 + "\n")
                f.write(f"Total Trades: {overall['total_trades']}\n")
                f.write(f"Wins: {overall['wins']} | Losses: {overall['losses']}\n")
                f.write(f"Win Rate: {overall['win_rate']:.1f}%\n")
                f.write(f"Total P/L: {overall['total_pips']:+.1f} pips\n")
                f.write(f"Average P/L: {overall['avg_pips']:+.1f} pips\n")
                f.write(f"Expectancy: {overall['expectancy']:+.2f} pips\n")
                f.write(f"Profit Factor: {overall['profit_factor']:.2f}\n")
                f.write(f"Sharpe Ratio: {overall['sharpe_ratio']:.2f}\n")
                f.write(f"Max Drawdown: {overall['max_drawdown']:.1f} pips\n")
                f.write(f"Current Streak: {overall['current_streak']:+d}\n")
                f.write(f"Max Win Streak: {overall['max_win_streak']}\n")
                f.write(f"Max Loss Streak: {overall['max_loss_streak']}\n\n")
                
                # Strategy breakdown
                strategy_stats = self.get_strategy_stats()
                if strategy_stats:
                    f.write("STRATEGY PERFORMANCE\n")
                    f.write("-"*70 + "\n")
                    for strategy, stats in strategy_stats.items():
                        f.write(f"\n{strategy.upper()}:\n")
                        f.write(f"  Trades: {stats['trades']}\n")
                        f.write(f"  Win Rate: {stats['win_rate']:.1f}%\n")
                        f.write(f"  Total P/L: {stats['total_pips']:+.1f} pips\n")
                        f.write(f"  Avg P/L: {stats['avg_pips']:+.1f} pips\n")
                        f.write(f"  Expectancy: {stats['expectancy']:+.2f} pips\n")
                        f.write(f"  Profit Factor: {stats['profit_factor']:.2f}\n")
                    f.write("\n")
                
                # Time-based analysis
                best_hours = self.get_best_trading_hours(3)
                worst_hours = self.get_worst_trading_hours(3)
                
                if best_hours:
                    f.write("BEST TRADING HOURS\n")
                    f.write("-"*70 + "\n")
                    for hour, avg_pips in best_hours:
                        f.write(f"  {hour:02d}:00 - {avg_pips:+.1f} pips avg\n")
                    f.write("\n")
                
                if worst_hours:
                    f.write("WORST TRADING HOURS\n")
                    f.write("-"*70 + "\n")
                    for hour, avg_pips in worst_hours:
                        f.write(f"  {hour:02d}:00 - {avg_pips:+.1f} pips avg\n")
                    f.write("\n")
                
                # Symbol performance
                symbol_stats = self.get_symbol_stats(5)
                if symbol_stats['best']:
                    f.write("TOP PERFORMING SYMBOLS\n")
                    f.write("-"*70 + "\n")
                    for s in symbol_stats['best']:
                        f.write(f"  {s['symbol']}: {s['total_pips']:+.1f}p "
                               f"({s['trades']} trades, {s['win_rate']:.0f}% WR)\n")
                    f.write("\n")
                
                if symbol_stats['worst']:
                    f.write("WORST PERFORMING SYMBOLS\n")
                    f.write("-"*70 + "\n")
                    for s in symbol_stats['worst']:
                        f.write(f"  {s['symbol']}: {s['total_pips']:+.1f}p "
                               f"({s['trades']} trades, {s['win_rate']:.0f}% WR)\n")
                    f.write("\n")
                
                f.write("="*70 + "\n")
            
            logger.info(f"Generated performance report: {filename}")
            return True
            
        except Exception as e:
            logger.error(f"Report generation failed: {e}")
            return False
    
    def get_statistics(self) -> Dict:
        """
        Get comprehensive statistics (alias for get_overall_stats for backward compatibility)
        """
        return self.get_overall_stats()

    def print_dashboard(self, active_positions: int = 0):
        """Print formatted performance dashboard with advanced metrics"""
        overall = self.get_overall_stats()
        strategy_stats = self.get_strategy_stats()
        symbol_stats = self.get_symbol_stats(top_n=3)
        risk_metrics = self.get_risk_metrics()

        print("\n" + "=" * 70)
        print("📊 PERFORMANCE DASHBOARD")
        print("=" * 70)

        # Overall stats
        streak_icon = "🔥" if overall['current_streak'] > 2 else "❄️" if overall['current_streak'] < -2 else "➖"
        print(f"\n🎯 OVERALL SESSION ({overall['session_duration']})")
        print(f"   Trades: {overall['total_trades']} | Wins: {overall['wins']} | "
              f"Losses: {overall['losses']} | Active: {active_positions}")
        print(f"   Win Rate: {overall['win_rate']:.1f}% | Total: {overall['total_pips']:+.1f} pips | "
              f"Avg: {overall['avg_pips']:+.1f} pips")
        print(f"   Streak: {streak_icon} {overall['current_streak']:+d} "
              f"(Max W: {overall['max_win_streak']}, Max L: {overall['max_loss_streak']})")
        
        # Risk metrics
        print(f"\n📈 RISK METRICS")
        print(f"   Expectancy: {risk_metrics['expectancy']:+.2f} pips/trade")
        print(f"   Profit Factor: {risk_metrics['profit_factor']:.2f}")
        print(f"   Sharpe Ratio: {risk_metrics['sharpe_ratio']:.2f}")
        print(f"   Max Drawdown: {risk_metrics['max_drawdown_pips']:.1f} pips "
              f"(Duration: {risk_metrics['max_drawdown_duration']})")
        
        if active_positions > 0:
            print(f"   ℹ️  Note: {active_positions} position(s) still open (not counted until closed)")

        # Strategy breakdown
        if strategy_stats:
            print(f"\n📊 BY STRATEGY")
            for strategy, stats in strategy_stats.items():
                print(f"   {strategy.upper():10}: {stats['trades']} trades | "
                      f"WR: {stats['win_rate']:.0f}% | "
                      f"Total: {stats['total_pips']:+.1f}p | "
                      f"Exp: {stats['expectancy']:+.1f}p | "
                      f"PF: {stats['profit_factor']:.2f}")

        # Best/worst pairs
        if symbol_stats['best']:
            print(f"\n✅ TOP PAIRS")
            for s in symbol_stats['best']:
                print(f"   {s['symbol']}: {s['total_pips']:+.1f}p "
                      f"({s['trades']} trades, {s['win_rate']:.0f}% WR, "
                      f"Avg: {s['avg_pips']:+.1f}p)")

        if symbol_stats['worst']:
            print(f"\n❌ WORST PAIRS")
            for s in symbol_stats['worst']:
                print(f"   {s['symbol']}: {s['total_pips']:+.1f}p "
                      f"({s['trades']} trades, {s['win_rate']:.0f}% WR, "
                      f"Avg: {s['avg_pips']:+.1f}p)")
        
        # Time-based insights
        best_hours = self.get_best_trading_hours(3)
        if best_hours:
            print(f"\n⏰ BEST HOURS")
            for hour, avg_pips in best_hours:
                # Convert hour to int if it's a string
                hour_int = int(hour) if isinstance(hour, str) else hour
                print(f"   {hour_int:02d}:00 - {avg_pips:+.1f} pips avg")

        print("=" * 70 + "\n")
