"""
Position Autopsy - Per-Position Learning System
Each position gets its own autopsy agent (like position_agent.py)
Only captures position-specific data, no system-wide logs
Works standalone - integrates directly with existing code
"""

import json
from datetime import datetime
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)


class PositionAutopsy:
    """
    One autopsy per position - like PositionAgent
    Captures only this position's data, analyzes on close
    """
    
    def __init__(self, ticket: int, symbol: str, entry_data: Dict):
        self.ticket = ticket
        self.symbol = symbol
        self.entry_time = datetime.now()
        
        # Entry snapshot
        self.entry = {
            'timestamp': self.entry_time.isoformat(),
            'price': entry_data['price'],
            'direction': entry_data['direction'],
            'strategy': entry_data['strategy'],
            'confidence': entry_data['confidence'],
            'sl': entry_data['sl'],
            'tp': entry_data['tp'],
            'spread': entry_data.get('spread', 0),
            'atr': entry_data.get('atr', 0),
            'trend': entry_data.get('trend', 'UNKNOWN'),
            'rsi': entry_data.get('rsi', 50),
            'support': entry_data.get('support', 0),
            'resistance': entry_data.get('resistance', 0)
        }
        
        # Track during trade
        self.max_profit_pips = 0
        self.max_loss_pips = 0
        self.agent_actions = []  # Track what agent did
        self.snapshots = []  # Periodic snapshots
        
        # Exit data (filled on close)
        self.exit = None
        
    def update(self, current_price: float, profit_pips: float, agent_action: Optional[Dict] = None):
        """
        Update during trade lifetime
        Called by position agent when it checks the position
        """
        # Track max profit/loss
        if profit_pips > self.max_profit_pips:
            self.max_profit_pips = profit_pips
        if profit_pips < self.max_loss_pips:
            self.max_loss_pips = profit_pips
        
        # Track agent actions
        if agent_action:
            self.agent_actions.append({
                'timestamp': datetime.now().isoformat(),
                'action': agent_action['type'],
                'details': agent_action
            })
    
    def close(self, exit_price: float, profit_pips: float, exit_reason: str, 
              market_data: Dict) -> Dict:
        """
        Called when position closes
        Returns complete autopsy if loss, None if profit
        """
        duration = (datetime.now() - self.entry_time).total_seconds() / 60
        
        self.exit = {
            'timestamp': datetime.now().isoformat(),
            'price': exit_price,
            'profit_pips': profit_pips,
            'reason': exit_reason,
            'duration_minutes': duration,
            'spread': market_data.get('spread', 0),
            'trend': market_data.get('trend', 'UNKNOWN')
        }
        
        # Only analyze losses
        if profit_pips < 0:
            return self._analyze_loss()
        
        return None
    
    def _analyze_loss(self) -> Dict:
        """
        Analyze what went wrong - ONLY for this position
        """
        mistakes = []
        
        # 1. Check analyzer mistakes
        if self.entry['direction'] == 'BUY' and self.exit['trend'] == 'DOWN':
            mistakes.append({
                'component': 'analyzer',
                'type': 'WRONG_TREND',
                'severity': 'HIGH',
                'description': 'Entered BUY but market was downtrend'
            })
        elif self.entry['direction'] == 'SELL' and self.exit['trend'] == 'UP':
            mistakes.append({
                'component': 'analyzer',
                'type': 'WRONG_TREND',
                'severity': 'HIGH',
                'description': 'Entered SELL but market was uptrend'
            })
        
        if self.entry['confidence'] < 0.70:
            mistakes.append({
                'component': 'analyzer',
                'type': 'LOW_CONFIDENCE',
                'severity': 'MEDIUM',
                'description': f'Signal confidence too low: {self.entry["confidence"]:.2f}'
            })
        
        # Check S/R
        if self.entry['direction'] == 'BUY' and self.entry['price'] > self.entry['resistance']:
            mistakes.append({
                'component': 'analyzer',
                'type': 'BOUGHT_ABOVE_RESISTANCE',
                'severity': 'HIGH',
                'description': 'Bought above resistance level'
            })
        elif self.entry['direction'] == 'SELL' and self.entry['price'] < self.entry['support']:
            mistakes.append({
                'component': 'analyzer',
                'type': 'SOLD_BELOW_SUPPORT',
                'severity': 'HIGH',
                'description': 'Sold below support level'
            })
        
        # 2. Check agent mistakes
        if self.max_profit_pips > 10:
            # Had profit but didn't protect it
            moved_to_breakeven = any(a['action'] == 'breakeven' for a in self.agent_actions)
            if not moved_to_breakeven:
                mistakes.append({
                    'component': 'agent',
                    'type': 'MISSED_BREAKEVEN',
                    'severity': 'HIGH',
                    'description': f'Had {self.max_profit_pips:.1f} pips profit but didn\'t move to breakeven'
                })
            
            # Check if took partials
            took_partials = any(a['action'] == 'partial_exit' for a in self.agent_actions)
            if not took_partials and self.max_profit_pips > 15:
                mistakes.append({
                    'component': 'agent',
                    'type': 'NO_PARTIAL_PROFIT',
                    'severity': 'MEDIUM',
                    'description': f'Had {self.max_profit_pips:.1f} pips profit but didn\'t take partials'
                })
        
        # Check if SL was too tight
        sl_distance = abs(self.entry['price'] - self.entry['sl'])
        if 'JPY' in self.symbol:
            sl_pips = sl_distance * 1000
        else:
            sl_pips = sl_distance * 100000
        
        if sl_pips < self.entry['atr'] * 1.5:
            mistakes.append({
                'component': 'agent',
                'type': 'SL_TOO_TIGHT',
                'severity': 'HIGH',
                'description': f'SL too tight ({sl_pips:.1f} pips) for volatility (ATR: {self.entry["atr"]:.1f})'
            })
        
        # 3. Determine root cause
        high_severity = [m for m in mistakes if m['severity'] == 'HIGH']
        root_cause = high_severity[0]['type'] if high_severity else (mistakes[0]['type'] if mistakes else 'MARKET_REVERSAL')
        
        # 4. Generate improvement
        improvement = self._generate_improvement(root_cause)
        
        return {
            'ticket': self.ticket,
            'symbol': self.symbol,
            'loss_pips': self.exit['profit_pips'],
            'duration_minutes': self.exit['duration_minutes'],
            'root_cause': root_cause,
            'mistakes': mistakes,
            'improvement': improvement,
            'entry': self.entry,
            'exit': self.exit,
            'max_profit_pips': self.max_profit_pips,
            'agent_actions': self.agent_actions
        }
    
    def _generate_improvement(self, root_cause: str) -> Dict:
        """
        Generate specific improvement for this mistake
        """
        improvements = {
            'WRONG_TREND': {
                'parameter': 'min_confidence',
                'action': 'increase',
                'from': 0.68,
                'to': 0.75,
                'reason': 'Too many wrong trend entries'
            },
            'LOW_CONFIDENCE': {
                'parameter': 'min_confidence',
                'action': 'increase',
                'from': 0.68,
                'to': 0.72,
                'reason': 'Low confidence signals losing'
            },
            'BOUGHT_ABOVE_RESISTANCE': {
                'parameter': 'sr_buffer_pips',
                'action': 'increase',
                'from': 5,
                'to': 10,
                'reason': 'Need more buffer from resistance'
            },
            'SOLD_BELOW_SUPPORT': {
                'parameter': 'sr_buffer_pips',
                'action': 'increase',
                'from': 5,
                'to': 10,
                'reason': 'Need more buffer from support'
            },
            'MISSED_BREAKEVEN': {
                'parameter': 'breakeven_trigger_pips',
                'action': 'decrease',
                'from': 10,
                'to': 7,
                'reason': 'Move to breakeven earlier'
            },
            'NO_PARTIAL_PROFIT': {
                'parameter': 'partial_profit_trigger_pips',
                'action': 'set',
                'to': 12,
                'reason': 'Take 50% profit at 12 pips'
            },
            'SL_TOO_TIGHT': {
                'parameter': 'sl_atr_multiplier',
                'action': 'increase',
                'from': 1.5,
                'to': 2.0,
                'reason': 'SL too tight for volatility'
            }
        }
        
        return improvements.get(root_cause, {
            'parameter': 'unknown',
            'action': 'review',
            'reason': f'Unknown root cause: {root_cause}'
        })


class AutopsyManager:
    """
    Manages all position autopsies
    Learns from patterns and provides improved parameters
    """
    
    def __init__(self):
        self.active_autopsies = {}  # {ticket: PositionAutopsy}
        self.completed_autopsies = []
        self.adjustments = {}  # Current parameter adjustments
        self.pattern_counts = {}  # Track recurring patterns
        
    def create_autopsy(self, ticket: int, symbol: str, entry_data: Dict) -> PositionAutopsy:
        """
        Create autopsy for new position
        """
        autopsy = PositionAutopsy(ticket, symbol, entry_data)
        self.active_autopsies[ticket] = autopsy
        return autopsy
    
    def get_autopsy(self, ticket: int) -> Optional[PositionAutopsy]:
        """
        Get autopsy for active position
        """
        return self.active_autopsies.get(ticket)
    
    def close_autopsy(self, ticket: int, exit_price: float, profit_pips: float,
                     exit_reason: str, market_data: Dict):
        """
        Close autopsy and analyze if loss
        """
        autopsy = self.active_autopsies.pop(ticket, None)
        if not autopsy:
            return
        
        # Get analysis
        analysis = autopsy.close(exit_price, profit_pips, exit_reason, market_data)
        
        # Only process losses
        if analysis:
            self.completed_autopsies.append(analysis)
            self._learn_from_loss(analysis)
            self._save_autopsy(analysis)
    
    def _learn_from_loss(self, analysis: Dict):
        """
        Learn from loss and update adjustments
        """
        root_cause = analysis['root_cause']
        
        # Track pattern
        self.pattern_counts[root_cause] = self.pattern_counts.get(root_cause, 0) + 1
        
        # Apply improvement if pattern is strong (3+ occurrences)
        if self.pattern_counts[root_cause] >= 3:
            improvement = analysis['improvement']
            if improvement.get('parameter'):
                param = improvement['parameter']
                
                # Update adjustment
                self.adjustments[param] = improvement
                
                logger.info(f"🔧 Autopsy adjustment: {param} = {improvement.get('to')} ({improvement['reason']})")
    
    def _save_autopsy(self, analysis: Dict):
        """
        Save autopsy to file (one line per loss)
        """
        try:
            with open('position_autopsies.jsonl', 'a') as f:
                f.write(json.dumps(analysis) + '\n')
        except Exception as e:
            logger.error(f"Failed to save autopsy: {e}")
    
    def get_adjustment(self, parameter: str, default):
        """
        Get adjusted parameter value
        """
        adjustment = self.adjustments.get(parameter)
        if adjustment:
            return adjustment.get('to', default)
        return default
    
    def get_statistics(self) -> Dict:
        """
        Get autopsy statistics
        """
        return {
            'active_autopsies': len(self.active_autopsies),
            'total_losses_analyzed': len(self.completed_autopsies),
            'active_adjustments': len(self.adjustments),
            'pattern_counts': self.pattern_counts.copy()
        }
    
    def print_summary(self):
        """
        Print autopsy summary (called periodically)
        """
        if not self.completed_autopsies:
            return
        
        recent = self.completed_autopsies[-10:]  # Last 10 losses
        
        print(f"\n📊 AUTOPSY SUMMARY (Last {len(recent)} losses):")
        
        # Group by root cause
        causes = {}
        for a in recent:
            cause = a['root_cause']
            causes[cause] = causes.get(cause, 0) + 1
        
        for cause, count in sorted(causes.items(), key=lambda x: x[1], reverse=True):
            print(f"   {cause}: {count}x")
        
        # Show active adjustments
        if self.adjustments:
            print(f"\n🔧 Active Adjustments:")
            for param, adj in self.adjustments.items():
                print(f"   {param}: {adj.get('to')} ({adj['reason']})")


# Global manager instance
autopsy_manager = AutopsyManager()


# ============================================================================
# INTEGRATION HELPERS - Use these in your existing code
# ============================================================================

def create_position_autopsy(ticket: int, symbol: str, signal: Dict, market_data: Dict):
    """
    Call this when opening a position
    
    Example:
        ticket = place_order(signal)
        create_position_autopsy(ticket, symbol, signal, market_data)
    """
    entry_data = {
        'price': signal['entry_price'],
        'direction': signal['signal'],
        'strategy': signal['strategy'],
        'confidence': signal['confidence'],
        'sl': signal['sl'],
        'tp': signal['tp'],
        'spread': market_data.get('spread', 0),
        'atr': market_data.get('atr', 0),
        'trend': market_data.get('trend', 'UNKNOWN'),
        'rsi': market_data.get('rsi', 50),
        'support': market_data.get('support', 0),
        'resistance': market_data.get('resistance', 0)
    }
    
    return autopsy_manager.create_autopsy(ticket, symbol, entry_data)


def update_position_autopsy(ticket: int, current_price: float, profit_pips: float, 
                            agent_action: Optional[Dict] = None):
    """
    Call this when agent checks/updates position
    
    Example:
        if moved_to_breakeven:
            update_position_autopsy(ticket, price, profit, {'type': 'breakeven'})
    """
    autopsy = autopsy_manager.get_autopsy(ticket)
    if autopsy:
        autopsy.update(current_price, profit_pips, agent_action)


def close_position_autopsy(ticket: int, exit_price: float, profit_pips: float,
                           exit_reason: str, market_data: Dict):
    """
    Call this when closing position
    
    Example:
        close_position(ticket)
        close_position_autopsy(ticket, exit_price, profit_pips, 'SL', market_data)
    """
    autopsy_manager.close_autopsy(ticket, exit_price, profit_pips, exit_reason, market_data)


def get_autopsy_adjustment(parameter: str, default):
    """
    Get adjusted parameter from autopsy learning
    
    Example:
        min_conf = get_autopsy_adjustment('min_confidence', 0.68)
        breakeven_trigger = get_autopsy_adjustment('breakeven_trigger_pips', 10)
    """
    return autopsy_manager.get_adjustment(parameter, default)


def show_autopsy_stats():
    """
    Show autopsy statistics
    
    Example:
        if cycle % 100 == 0:
            show_autopsy_stats()
    """
    autopsy_manager.print_summary()


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════════════════╗
║            POSITION AUTOPSY - STANDALONE VERSION                 ║
╚══════════════════════════════════════════════════════════════════╝

Features:
✅ One autopsy per position (like position_agent.py)
✅ Only captures position-specific data
✅ No system-wide logs
✅ Works standalone with existing code
✅ Simple integration (3 function calls)

Integration:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. When opening position:
   create_position_autopsy(ticket, symbol, signal, market_data)

2. When agent checks position:
   update_position_autopsy(ticket, price, profit, agent_action)

3. When closing position:
   close_position_autopsy(ticket, exit_price, profit, reason, market_data)

4. Use adjusted parameters:
   min_conf = get_autopsy_adjustment('min_confidence', 0.68)

5. Show stats (optional):
   show_autopsy_stats()

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Output:
- position_autopsies.jsonl (one line per loss)
- Adjustments applied automatically after 3+ occurrences

No complex setup, no background threads, just simple function calls!
    """)
