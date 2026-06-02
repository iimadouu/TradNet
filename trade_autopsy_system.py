"""
Trade Autopsy System - Real-Time Learning from Losses
Captures everything about losing trades and uses AI to improve strategies LIVE

Features:
1. Captures complete trade context (before, during, after)
2. Analyzes WHY mistakes happened (analyzer, agent, executor)
3. Uses AI to learn patterns and suggest improvements
4. Updates trading logic in real-time while bot runs
5. Holds each component accountable for mistakes
"""

import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import MetaTrader5 as mt5
import logging
from pathlib import Path
import threading
import time

logger = logging.getLogger(__name__)


class TradeDataCollector:
    """
    Collects EVERY detail about a trade from entry to exit
    Nothing is missed - complete forensic data collection
    """
    
    def __init__(self):
        self.active_trades = {}  # {ticket: complete_trade_data}
        self.lock = threading.Lock()
        
    def capture_entry_context(self, ticket: int, symbol: str, signal: Dict, 
                             market_data: Dict, analyzer_state: Dict) -> Dict:
        """
        Capture EVERYTHING at trade entry
        """
        entry_context = {
            'ticket': ticket,
            'symbol': symbol,
            'timestamp': datetime.now().isoformat(),
            
            # Signal details
            'signal': {
                'direction': signal.get('signal'),
                'confidence': signal.get('confidence'),
                'strategy': signal.get('strategy'),
                'entry_price': signal.get('entry_price'),
                'sl': signal.get('sl'),
                'tp': signal.get('tp'),
                'reasons': signal.get('reasons', []),
                'indicators': signal.get('indicators', {})
            },
            
            # Market conditions at entry
            'market_entry': {
                'price': market_data.get('current_price'),
                'spread': market_data.get('spread'),
                'volume': market_data.get('volume'),
                'volatility': market_data.get('atr'),
                'trend': market_data.get('trend'),
                'support': market_data.get('support'),
                'resistance': market_data.get('resistance'),
                'time_of_day': datetime.now().hour,
                'day_of_week': datetime.now().weekday()
            },
            
            # Analyzer state at entry
            'analyzer_state': {
                'ema_fast': analyzer_state.get('ema_fast'),
                'ema_slow': analyzer_state.get('ema_slow'),
                'rsi': analyzer_state.get('rsi'),
                'macd': analyzer_state.get('macd'),
                'macd_signal': analyzer_state.get('macd_signal'),
                'bb_upper': analyzer_state.get('bb_upper'),
                'bb_lower': analyzer_state.get('bb_lower'),
                'atr': analyzer_state.get('atr'),
                'volume_ma': analyzer_state.get('volume_ma'),
                'pattern_detected': analyzer_state.get('pattern', None)
            },
            
            # Multi-timeframe context
            'mtf_context': {
                'h1_trend': analyzer_state.get('h1_trend'),
                'h4_trend': analyzer_state.get('h4_trend'),
                'd1_trend': analyzer_state.get('d1_trend'),
                'alignment': analyzer_state.get('mtf_alignment')
            },
            
            # Trade snapshots (will be updated during trade)
            'snapshots': [],
            
            # Exit data (will be filled when trade closes)
            'exit': None
        }
        
        with self.lock:
            self.active_trades[ticket] = entry_context
        
        return entry_context
    
    def capture_trade_snapshot(self, ticket: int, current_price: float, 
                               market_data: Dict, agent_decision: Optional[Dict] = None):
        """
        Capture periodic snapshots during trade lifetime
        Called every minute or when agent makes a decision
        """
        with self.lock:
            if ticket not in self.active_trades:
                return
            
            snapshot = {
                'timestamp': datetime.now().isoformat(),
                'price': current_price,
                'spread': market_data.get('spread'),
                'volatility': market_data.get('atr'),
                'profit_pips': market_data.get('profit_pips'),
                'agent_decision': agent_decision  # If agent adjusted SL/TP
            }
            
            self.active_trades[ticket]['snapshots'].append(snapshot)
    
    def capture_exit_context(self, ticket: int, exit_price: float, 
                            profit_pips: float, exit_reason: str,
                            market_data: Dict, agent_state: Dict) -> Dict:
        """
        Capture EVERYTHING at trade exit
        """
        with self.lock:
            if ticket not in self.active_trades:
                logger.warning(f"Trade {ticket} not found in active trades")
                return None
            
            trade_data = self.active_trades[ticket]
            
            # Calculate trade duration
            entry_time = datetime.fromisoformat(trade_data['timestamp'])
            exit_time = datetime.now()
            duration_minutes = (exit_time - entry_time).total_seconds() / 60
            
            # Exit context
            exit_context = {
                'timestamp': exit_time.isoformat(),
                'exit_price': exit_price,
                'profit_pips': profit_pips,
                'profit_usd': market_data.get('profit_usd', 0),
                'exit_reason': exit_reason,
                'duration_minutes': duration_minutes,
                
                # Market conditions at exit
                'market_exit': {
                    'price': exit_price,
                    'spread': market_data.get('spread'),
                    'volume': market_data.get('volume'),
                    'volatility': market_data.get('atr'),
                    'trend': market_data.get('trend'),
                    'time_of_day': exit_time.hour,
                    'day_of_week': exit_time.weekday()
                },
                
                # Agent state at exit
                'agent_state': {
                    'sl_adjustments': agent_state.get('sl_adjustments', 0),
                    'tp_adjustments': agent_state.get('tp_adjustments', 0),
                    'trailing_active': agent_state.get('trailing_active', False),
                    'breakeven_hit': agent_state.get('breakeven_hit', False),
                    'partial_exits': agent_state.get('partial_exits', [])
                },
                
                # Price movement analysis
                'price_movement': {
                    'max_profit_pips': market_data.get('max_profit_pips', 0),
                    'max_loss_pips': market_data.get('max_loss_pips', 0),
                    'price_range': market_data.get('price_range', 0),
                    'hit_tp': market_data.get('hit_tp', False),
                    'hit_sl': market_data.get('hit_sl', False)
                }
            }
            
            trade_data['exit'] = exit_context
            
            # Return complete trade data
            complete_data = self.active_trades.pop(ticket)
            return complete_data


class MistakeAnalyzer:
    """
    Analyzes WHY the trade lost money
    Holds each component accountable: Analyzer, Agent, Executor
    """
    
    def __init__(self):
        self.mistake_patterns = []
        
    def analyze_loss(self, trade_data: Dict) -> Dict:
        """
        Deep analysis of what went wrong
        Returns accountability report
        """
        analysis = {
            'ticket': trade_data['ticket'],
            'symbol': trade_data['symbol'],
            'loss_pips': trade_data['exit']['profit_pips'],
            'loss_usd': trade_data['exit']['profit_usd'],
            'mistakes': [],
            'accountability': {
                'analyzer': [],
                'agent': [],
                'executor': []
            },
            'root_cause': None,
            'severity': 'LOW'
        }
        
        # 1. ANALYZER MISTAKES
        analyzer_mistakes = self._analyze_analyzer_mistakes(trade_data)
        analysis['accountability']['analyzer'] = analyzer_mistakes
        analysis['mistakes'].extend(analyzer_mistakes)
        
        # 2. AGENT MISTAKES
        agent_mistakes = self._analyze_agent_mistakes(trade_data)
        analysis['accountability']['agent'] = agent_mistakes
        analysis['mistakes'].extend(agent_mistakes)
        
        # 3. EXECUTOR MISTAKES
        executor_mistakes = self._analyze_executor_mistakes(trade_data)
        analysis['accountability']['executor'] = executor_mistakes
        analysis['mistakes'].extend(executor_mistakes)
        
        # 4. DETERMINE ROOT CAUSE
        analysis['root_cause'] = self._determine_root_cause(analysis['mistakes'])
        
        # 5. CALCULATE SEVERITY
        analysis['severity'] = self._calculate_severity(trade_data, analysis['mistakes'])
        
        return analysis
    
    def _analyze_analyzer_mistakes(self, trade_data: Dict) -> List[Dict]:
        """
        Check if analyzer made wrong entry decision
        """
        mistakes = []
        signal = trade_data['signal']
        market_entry = trade_data['market_entry']
        market_exit = trade_data['exit']['market_exit']
        
        # Mistake 1: Wrong trend detection
        if signal['direction'] == 'BUY' and market_exit['trend'] == 'DOWN':
            mistakes.append({
                'type': 'WRONG_TREND',
                'description': 'Entered BUY but market was in downtrend',
                'confidence': signal['confidence'],
                'severity': 'HIGH'
            })
        elif signal['direction'] == 'SELL' and market_exit['trend'] == 'UP':
            mistakes.append({
                'type': 'WRONG_TREND',
                'description': 'Entered SELL but market was in uptrend',
                'confidence': signal['confidence'],
                'severity': 'HIGH'
            })
        
        # Mistake 2: Low confidence signal
        if signal['confidence'] < 0.70:
            mistakes.append({
                'type': 'LOW_CONFIDENCE',
                'description': f'Signal confidence too low: {signal["confidence"]:.2f}',
                'severity': 'MEDIUM'
            })
        
        # Mistake 3: MTF misalignment
        mtf = trade_data.get('mtf_context', {})
        if not mtf.get('alignment'):
            mistakes.append({
                'type': 'MTF_MISALIGNMENT',
                'description': 'Multi-timeframe trends not aligned',
                'h1': mtf.get('h1_trend'),
                'h4': mtf.get('h4_trend'),
                'd1': mtf.get('d1_trend'),
                'severity': 'MEDIUM'
            })
        
        # Mistake 4: Bad entry timing (high spread)
        if market_entry.get('spread', 0) > market_entry.get('volatility', 1) * 0.5:
            mistakes.append({
                'type': 'HIGH_SPREAD_ENTRY',
                'description': 'Entered during high spread conditions',
                'spread': market_entry.get('spread'),
                'severity': 'LOW'
            })
        
        # Mistake 5: Counter-support/resistance
        entry_price = signal['entry_price']
        if signal['direction'] == 'BUY':
            if entry_price > market_entry.get('resistance', float('inf')):
                mistakes.append({
                    'type': 'BOUGHT_ABOVE_RESISTANCE',
                    'description': 'Bought above resistance level',
                    'entry': entry_price,
                    'resistance': market_entry.get('resistance'),
                    'severity': 'HIGH'
                })
        else:  # SELL
            if entry_price < market_entry.get('support', 0):
                mistakes.append({
                    'type': 'SOLD_BELOW_SUPPORT',
                    'description': 'Sold below support level',
                    'entry': entry_price,
                    'support': market_entry.get('support'),
                    'severity': 'HIGH'
                })
        
        return mistakes
    
    def _analyze_agent_mistakes(self, trade_data: Dict) -> List[Dict]:
        """
        Check if agent managed the trade poorly
        """
        mistakes = []
        exit_data = trade_data['exit']
        agent_state = exit_data['agent_state']
        price_movement = exit_data['price_movement']
        
        # Mistake 1: Didn't move to breakeven when in profit
        if price_movement['max_profit_pips'] > 10 and not agent_state['breakeven_hit']:
            mistakes.append({
                'type': 'MISSED_BREAKEVEN',
                'description': f'Had {price_movement["max_profit_pips"]:.1f} pips profit but didn\'t move to breakeven',
                'max_profit': price_movement['max_profit_pips'],
                'severity': 'HIGH'
            })
        
        # Mistake 2: Didn't take partial profits
        if price_movement['max_profit_pips'] > 15 and len(agent_state['partial_exits']) == 0:
            mistakes.append({
                'type': 'NO_PARTIAL_PROFIT',
                'description': f'Had {price_movement["max_profit_pips"]:.1f} pips profit but didn\'t take partials',
                'max_profit': price_movement['max_profit_pips'],
                'severity': 'MEDIUM'
            })
        
        # Mistake 3: SL too tight
        signal = trade_data['signal']
        atr = trade_data['market_entry'].get('volatility', 0)
        sl_distance = abs(signal['entry_price'] - signal['sl'])
        if 'JPY' in trade_data['symbol']:
            sl_pips = sl_distance * 1000
        else:
            sl_pips = sl_distance * 100000
        
        if sl_pips < atr * 1.5:
            mistakes.append({
                'type': 'SL_TOO_TIGHT',
                'description': f'SL too tight ({sl_pips:.1f} pips) for volatility (ATR: {atr:.1f})',
                'sl_pips': sl_pips,
                'atr': atr,
                'severity': 'HIGH'
            })
        
        # Mistake 4: Didn't trail stop in strong trend
        if price_movement['max_profit_pips'] > 20 and not agent_state['trailing_active']:
            mistakes.append({
                'type': 'NO_TRAILING_STOP',
                'description': f'Had {price_movement["max_profit_pips"]:.1f} pips profit but didn\'t trail stop',
                'max_profit': price_movement['max_profit_pips'],
                'severity': 'MEDIUM'
            })
        
        return mistakes
    
    def _analyze_executor_mistakes(self, trade_data: Dict) -> List[Dict]:
        """
        Check if executor had execution issues
        """
        mistakes = []
        
        # Mistake 1: High slippage
        signal_entry = trade_data['signal']['entry_price']
        actual_entry = trade_data['market_entry']['price']
        slippage = abs(signal_entry - actual_entry)
        
        if 'JPY' in trade_data['symbol']:
            slippage_pips = slippage * 1000
        else:
            slippage_pips = slippage * 100000
        
        if slippage_pips > 2:
            mistakes.append({
                'type': 'HIGH_SLIPPAGE',
                'description': f'High slippage on entry: {slippage_pips:.1f} pips',
                'slippage_pips': slippage_pips,
                'severity': 'LOW'
            })
        
        # Mistake 2: Slow execution
        # (Would need execution time data)
        
        return mistakes
    
    def _determine_root_cause(self, mistakes: List[Dict]) -> str:
        """
        Determine the primary root cause of the loss
        """
        if not mistakes:
            return 'MARKET_REVERSAL'
        
        # Prioritize by severity
        high_severity = [m for m in mistakes if m.get('severity') == 'HIGH']
        if high_severity:
            return high_severity[0]['type']
        
        medium_severity = [m for m in mistakes if m.get('severity') == 'MEDIUM']
        if medium_severity:
            return medium_severity[0]['type']
        
        return mistakes[0]['type'] if mistakes else 'UNKNOWN'
    
    def _calculate_severity(self, trade_data: Dict, mistakes: List[Dict]) -> str:
        """
        Calculate overall severity of the loss
        """
        loss_pips = abs(trade_data['exit']['profit_pips'])
        high_mistakes = sum(1 for m in mistakes if m.get('severity') == 'HIGH')
        
        if loss_pips > 30 or high_mistakes >= 2:
            return 'CRITICAL'
        elif loss_pips > 15 or high_mistakes >= 1:
            return 'HIGH'
        elif loss_pips > 5:
            return 'MEDIUM'
        else:
            return 'LOW'



class AIStrategyEnhancer:
    """
    Uses AI/ML to learn from mistakes and enhance strategies in REAL-TIME
    Updates trading logic while bot is still running
    """
    
    def __init__(self):
        self.loss_database = []
        self.pattern_rules = {}
        self.strategy_adjustments = {}
        self.learning_enabled = True
        
    def learn_from_loss(self, trade_data: Dict, mistake_analysis: Dict) -> Dict:
        """
        Learn from a losing trade and generate strategy improvements
        """
        # Store in database
        self.loss_database.append({
            'trade': trade_data,
            'analysis': mistake_analysis,
            'timestamp': datetime.now().isoformat()
        })
        
        # Analyze patterns
        patterns = self._detect_patterns()
        
        # Generate improvements
        improvements = self._generate_improvements(mistake_analysis, patterns)
        
        # Apply improvements if confidence is high
        if improvements['confidence'] > 0.75:
            self._apply_improvements(improvements)
        
        return improvements
    
    def _detect_patterns(self) -> Dict:
        """
        Detect recurring patterns in losses
        """
        if len(self.loss_database) < 5:
            return {}
        
        patterns = {
            'recurring_mistakes': {},
            'bad_symbols': {},
            'bad_times': {},
            'bad_strategies': {},
            'bad_conditions': {}
        }
        
        # Analyze last 50 losses
        recent_losses = self.loss_database[-50:]
        
        # 1. Recurring mistake types
        for loss in recent_losses:
            for mistake in loss['analysis']['mistakes']:
                mistake_type = mistake['type']
                patterns['recurring_mistakes'][mistake_type] = \
                    patterns['recurring_mistakes'].get(mistake_type, 0) + 1
        
        # 2. Symbols with high loss rate
        for loss in recent_losses:
            symbol = loss['trade']['symbol']
            patterns['bad_symbols'][symbol] = \
                patterns['bad_symbols'].get(symbol, 0) + 1
        
        # 3. Times with high loss rate
        for loss in recent_losses:
            hour = datetime.fromisoformat(loss['trade']['timestamp']).hour
            patterns['bad_times'][hour] = \
                patterns['bad_times'].get(hour, 0) + 1
        
        # 4. Strategies with high loss rate
        for loss in recent_losses:
            strategy = loss['trade']['signal']['strategy']
            patterns['bad_strategies'][strategy] = \
                patterns['bad_strategies'].get(strategy, 0) + 1
        
        # 5. Market conditions with high loss rate
        for loss in recent_losses:
            trend = loss['trade']['market_entry'].get('trend')
            if trend:
                patterns['bad_conditions'][f'trend_{trend}'] = \
                    patterns['bad_conditions'].get(f'trend_{trend}', 0) + 1
        
        return patterns
    
    def _generate_improvements(self, mistake_analysis: Dict, patterns: Dict) -> Dict:
        """
        Generate specific strategy improvements based on analysis
        """
        improvements = {
            'timestamp': datetime.now().isoformat(),
            'root_cause': mistake_analysis['root_cause'],
            'severity': mistake_analysis['severity'],
            'adjustments': [],
            'confidence': 0.0
        }
        
        # Generate adjustments based on root cause
        root_cause = mistake_analysis['root_cause']
        
        if root_cause == 'WRONG_TREND':
            improvements['adjustments'].append({
                'component': 'analyzer',
                'parameter': 'min_confidence',
                'action': 'increase',
                'from': 0.68,
                'to': 0.75,
                'reason': 'Too many wrong trend entries'
            })
            improvements['adjustments'].append({
                'component': 'analyzer',
                'parameter': 'mtf_alignment_required',
                'action': 'enable',
                'reason': 'Require multi-timeframe alignment'
            })
            improvements['confidence'] = 0.85
        
        elif root_cause == 'LOW_CONFIDENCE':
            improvements['adjustments'].append({
                'component': 'analyzer',
                'parameter': 'min_confidence',
                'action': 'increase',
                'from': 0.68,
                'to': 0.72,
                'reason': 'Low confidence signals losing'
            })
            improvements['confidence'] = 0.80
        
        elif root_cause == 'MTF_MISALIGNMENT':
            improvements['adjustments'].append({
                'component': 'analyzer',
                'parameter': 'require_mtf_alignment',
                'action': 'enable',
                'reason': 'MTF misalignment causing losses'
            })
            improvements['confidence'] = 0.90
        
        elif root_cause == 'BOUGHT_ABOVE_RESISTANCE' or root_cause == 'SOLD_BELOW_SUPPORT':
            improvements['adjustments'].append({
                'component': 'analyzer',
                'parameter': 'sr_buffer',
                'action': 'increase',
                'from': 5,
                'to': 10,
                'reason': 'Need more buffer from S/R levels'
            })
            improvements['confidence'] = 0.85
        
        elif root_cause == 'MISSED_BREAKEVEN':
            improvements['adjustments'].append({
                'component': 'agent',
                'parameter': 'breakeven_trigger_pips',
                'action': 'decrease',
                'from': 10,
                'to': 7,
                'reason': 'Move to breakeven earlier'
            })
            improvements['confidence'] = 0.80
        
        elif root_cause == 'NO_PARTIAL_PROFIT':
            improvements['adjustments'].append({
                'component': 'agent',
                'parameter': 'partial_profit_enabled',
                'action': 'enable',
                'reason': 'Enable partial profit taking'
            })
            improvements['adjustments'].append({
                'component': 'agent',
                'parameter': 'partial_profit_trigger',
                'action': 'set',
                'to': 12,
                'reason': 'Take 50% profit at 12 pips'
            })
            improvements['confidence'] = 0.75
        
        elif root_cause == 'SL_TOO_TIGHT':
            improvements['adjustments'].append({
                'component': 'agent',
                'parameter': 'sl_atr_multiplier',
                'action': 'increase',
                'from': 1.5,
                'to': 2.0,
                'reason': 'SL too tight for volatility'
            })
            improvements['confidence'] = 0.85
        
        elif root_cause == 'NO_TRAILING_STOP':
            improvements['adjustments'].append({
                'component': 'agent',
                'parameter': 'trailing_stop_enabled',
                'action': 'enable',
                'reason': 'Enable trailing stop for trends'
            })
            improvements['adjustments'].append({
                'component': 'agent',
                'parameter': 'trailing_trigger_pips',
                'action': 'set',
                'to': 15,
                'reason': 'Start trailing at 15 pips profit'
            })
            improvements['confidence'] = 0.80
        
        # Check for recurring patterns
        if patterns.get('recurring_mistakes'):
            most_common = max(patterns['recurring_mistakes'].items(), key=lambda x: x[1])
            if most_common[1] >= 5:  # If same mistake 5+ times
                improvements['adjustments'].append({
                    'component': 'system',
                    'parameter': 'pattern_detected',
                    'action': 'alert',
                    'pattern': most_common[0],
                    'count': most_common[1],
                    'reason': f'Recurring pattern: {most_common[0]} ({most_common[1]} times)'
                })
                improvements['confidence'] = min(improvements['confidence'] + 0.10, 1.0)
        
        return improvements
    
    def _apply_improvements(self, improvements: Dict):
        """
        Apply improvements to live trading system
        """
        logger.info(f"🔧 Applying strategy improvements (confidence: {improvements['confidence']:.2f})")
        
        for adjustment in improvements['adjustments']:
            component = adjustment['component']
            parameter = adjustment['parameter']
            action = adjustment['action']
            
            # Store adjustment
            key = f"{component}.{parameter}"
            self.strategy_adjustments[key] = adjustment
            
            logger.info(f"   ✓ {component}.{parameter}: {action} - {adjustment['reason']}")
        
        # Save adjustments to file for persistence
        self._save_adjustments()
    
    def _save_adjustments(self):
        """Save strategy adjustments to file"""
        try:
            with open('strategy_adjustments.json', 'w') as f:
                json.dump({
                    'timestamp': datetime.now().isoformat(),
                    'adjustments': self.strategy_adjustments,
                    'total_losses_analyzed': len(self.loss_database)
                }, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save adjustments: {e}")
    
    def get_current_adjustments(self) -> Dict:
        """Get current strategy adjustments"""
        return self.strategy_adjustments
    
    def get_adjustment(self, component: str, parameter: str, default=None):
        """Get specific adjustment value"""
        key = f"{component}.{parameter}"
        adjustment = self.strategy_adjustments.get(key)
        if adjustment:
            return adjustment.get('to', default)
        return default


class TradeAutopsySystem:
    """
    Main autopsy system that coordinates everything
    Runs in background thread, doesn't interfere with trading
    """
    
    def __init__(self):
        self.collector = TradeDataCollector()
        self.analyzer = MistakeAnalyzer()
        self.enhancer = AIStrategyEnhancer()
        self.running = False
        self.thread = None
        self.autopsy_queue = []
        self.lock = threading.Lock()
        
    def start(self):
        """Start the autopsy system in background"""
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("🔬 Trade Autopsy System started")
    
    def stop(self):
        """Stop the autopsy system"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("🔬 Trade Autopsy System stopped")
    
    def _run(self):
        """Background processing loop"""
        while self.running:
            try:
                # Process queued autopsies
                if self.autopsy_queue:
                    with self.lock:
                        trade_data = self.autopsy_queue.pop(0)
                    
                    # Perform autopsy
                    self._perform_autopsy(trade_data)
                
                time.sleep(1)  # Check every second
            except Exception as e:
                logger.error(f"Autopsy system error: {e}")
    
    def _perform_autopsy(self, trade_data: Dict):
        """Perform complete autopsy on a losing trade"""
        try:
            logger.info(f"\n{'='*70}")
            logger.info(f"🔬 TRADE AUTOPSY - Ticket #{trade_data['ticket']}")
            logger.info(f"{'='*70}")
            
            # 1. Analyze mistakes
            mistake_analysis = self.analyzer.analyze_loss(trade_data)
            
            logger.info(f"\n📊 LOSS SUMMARY:")
            logger.info(f"   Symbol: {trade_data['symbol']}")
            logger.info(f"   Loss: {mistake_analysis['loss_pips']:.1f} pips (${mistake_analysis['loss_usd']:.2f})")
            logger.info(f"   Root Cause: {mistake_analysis['root_cause']}")
            logger.info(f"   Severity: {mistake_analysis['severity']}")
            
            # 2. Show accountability
            logger.info(f"\n⚖️ ACCOUNTABILITY:")
            for component, mistakes in mistake_analysis['accountability'].items():
                if mistakes:
                    logger.info(f"\n   {component.upper()}:")
                    for mistake in mistakes:
                        logger.info(f"      ❌ {mistake['type']}: {mistake['description']}")
            
            # 3. Learn and improve
            improvements = self.enhancer.learn_from_loss(trade_data, mistake_analysis)
            
            if improvements['adjustments']:
                logger.info(f"\n🔧 STRATEGY IMPROVEMENTS (Confidence: {improvements['confidence']:.2f}):")
                for adj in improvements['adjustments']:
                    logger.info(f"   ✓ {adj['component']}.{adj['parameter']}: {adj['action']}")
                    logger.info(f"      Reason: {adj['reason']}")
            
            # 4. Save autopsy report
            self._save_autopsy_report(trade_data, mistake_analysis, improvements)
            
            logger.info(f"\n{'='*70}\n")
            
        except Exception as e:
            logger.error(f"Autopsy failed: {e}")
    
    def _save_autopsy_report(self, trade_data: Dict, analysis: Dict, improvements: Dict):
        """Save detailed autopsy report"""
        try:
            report = {
                'timestamp': datetime.now().isoformat(),
                'trade': trade_data,
                'analysis': analysis,
                'improvements': improvements
            }
            
            # Append to autopsy log
            with open('autopsy_reports.jsonl', 'a') as f:
                f.write(json.dumps(report) + '\n')
        except Exception as e:
            logger.error(f"Failed to save autopsy report: {e}")
    
    # Public API for integration with trading system
    
    def on_trade_entry(self, ticket: int, symbol: str, signal: Dict, 
                       market_data: Dict, analyzer_state: Dict):
        """Called when a trade is entered"""
        self.collector.capture_entry_context(ticket, symbol, signal, market_data, analyzer_state)
    
    def on_trade_update(self, ticket: int, current_price: float, 
                       market_data: Dict, agent_decision: Optional[Dict] = None):
        """Called periodically during trade lifetime"""
        self.collector.capture_trade_snapshot(ticket, current_price, market_data, agent_decision)
    
    def on_trade_exit(self, ticket: int, exit_price: float, profit_pips: float,
                     exit_reason: str, market_data: Dict, agent_state: Dict):
        """Called when a trade exits"""
        trade_data = self.collector.capture_exit_context(
            ticket, exit_price, profit_pips, exit_reason, market_data, agent_state
        )
        
        if trade_data and profit_pips < 0:  # Only autopsy losses
            with self.lock:
                self.autopsy_queue.append(trade_data)
    
    def get_strategy_adjustment(self, component: str, parameter: str, default=None):
        """Get current strategy adjustment value"""
        return self.enhancer.get_adjustment(component, parameter, default)
    
    def get_all_adjustments(self) -> Dict:
        """Get all current strategy adjustments"""
        return self.enhancer.get_current_adjustments()
    
    def get_statistics(self) -> Dict:
        """Get autopsy statistics"""
        return {
            'total_losses_analyzed': len(self.enhancer.loss_database),
            'active_trades_tracked': len(self.collector.active_trades),
            'pending_autopsies': len(self.autopsy_queue),
            'total_adjustments': len(self.enhancer.strategy_adjustments)
        }


# Example usage
if __name__ == "__main__":
    # Initialize system
    autopsy = TradeAutopsySystem()
    autopsy.start()
    
    print("Trade Autopsy System - Example Usage")
    print("="*70)
    print("\nThis system will:")
    print("1. Capture EVERYTHING about each trade")
    print("2. Analyze WHY losses happened")
    print("3. Hold components accountable (analyzer, agent, executor)")
    print("4. Learn patterns and improve strategies")
    print("5. Update trading logic in REAL-TIME")
    print("\nIntegration points:")
    print("  - autopsy.on_trade_entry() - When trade opens")
    print("  - autopsy.on_trade_update() - During trade (every minute)")
    print("  - autopsy.on_trade_exit() - When trade closes")
    print("  - autopsy.get_strategy_adjustment() - Get improved parameters")
    print("\n" + "="*70)
    
    # Keep running
    try:
        while True:
            stats = autopsy.get_statistics()
            print(f"\rStats: {stats['total_losses_analyzed']} losses analyzed, "
                  f"{stats['total_adjustments']} adjustments active", end='')
            time.sleep(5)
    except KeyboardInterrupt:
        autopsy.stop()
        print("\n\nAutopsy system stopped")
