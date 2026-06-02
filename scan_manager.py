"""
Scan Manager - Saves and loads market scan results with validation and history
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

# Platform-specific file locking
if sys.platform == 'win32':
    import msvcrt
else:
    import fcntl

logger = logging.getLogger(__name__)

class ScanManager:
    """
    Enhanced scan manager with:
    - Scan expiration checking
    - Data validation
    - File locking (concurrent access protection)
    - Scan history tracking
    - Quality metrics
    - Performance tracking
    """
    
    def __init__(self, scan_file: str = "market_scan.json", 
                 history_file: str = "scan_history.json",
                 max_scan_age_minutes: int = 30,
                 keep_history_hours: int = 24):
        self.scan_file = scan_file
        self.history_file = history_file
        self.max_scan_age_minutes = max_scan_age_minutes
        self.keep_history_hours = keep_history_hours
        
        # Scan quality tracking
        self.scan_stats = {
            'total_scans': 0,
            'successful_scans': 0,
            'failed_scans': 0,
            'avg_scan_duration': 0.0,
            'last_scan_time': None
        }
        
        # Schema version for future compatibility
        self.SCHEMA_VERSION = "1.0"
    
    def _acquire_file_lock(self, file_handle, timeout: int = 5) -> bool:
        """
        Acquire exclusive lock on file (prevents concurrent access corruption)
        Returns True if lock acquired, False if timeout
        """
        try:
            # Try to acquire lock with timeout
            start_time = datetime.now()
            while (datetime.now() - start_time).total_seconds() < timeout:
                try:
                    if sys.platform == 'win32':
                        # Windows file locking using msvcrt
                        # Seek to beginning and lock 1 byte
                        file_handle.seek(0)
                        msvcrt.locking(file_handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        # Unix file locking using fcntl
                        fcntl.flock(file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return True
                except (IOError, OSError):
                    # Lock held by another process, wait a bit
                    import time
                    time.sleep(0.1)
            
            logger.warning(f"Could not acquire file lock after {timeout}s")
            return False
        except Exception as e:
            logger.error(f"File locking error: {e}")
            return False
    
    def _release_file_lock(self, file_handle):
        """Release file lock"""
        try:
            if sys.platform == 'win32':
                # Windows file unlocking using msvcrt
                # Seek to beginning and unlock 1 byte
                file_handle.seek(0)
                msvcrt.locking(file_handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                # Unix file unlocking using fcntl
                fcntl.flock(file_handle.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            logger.error(f"Error releasing file lock: {e}")
    
    def _validate_scan_data(self, data: Dict) -> Tuple[bool, str]:
        """
        Validate scan data structure and content
        Returns (is_valid, error_message)
        """
        # Check required top-level fields (schema_version is optional for backward compatibility)
        required_fields = ['timestamp', 'scan_count', 'results']
        for field in required_fields:
            if field not in data:
                return False, f"Missing required field: {field}"
        
        # Add schema_version if missing (backward compatibility)
        if 'schema_version' not in data:
            logger.info("Adding missing schema_version to scan data")
            data['schema_version'] = self.SCHEMA_VERSION
        
        # Validate timestamp
        try:
            scan_time = datetime.fromisoformat(data['timestamp'])
        except (ValueError, TypeError) as e:
            return False, f"Invalid timestamp format: {e}"
        
        # Check scan age
        age_minutes = (datetime.now() - scan_time).total_seconds() / 60
        if age_minutes > self.max_scan_age_minutes:
            return False, f"Scan too old: {age_minutes:.1f} minutes (max: {self.max_scan_age_minutes})"
        
        # Validate results structure
        if not isinstance(data['results'], list):
            return False, "Results must be a list"
        
        if data['scan_count'] != len(data['results']):
            return False, f"Scan count mismatch: {data['scan_count']} != {len(data['results'])}"
        
        # Validate each result has minimum required fields
        required_result_fields = ['symbol', 'verdict']
        for i, result in enumerate(data['results']):
            if not isinstance(result, dict):
                return False, f"Result {i} is not a dict"
            
            for field in required_result_fields:
                if field not in result:
                    return False, f"Result {i} missing field: {field}"
            
            # Validate verdict values
            valid_verdicts = ['APPROVED', 'AVOID', 'CAUTION', 'NONE', 'SKIP']
            if result['verdict'] not in valid_verdicts:
                return False, f"Invalid verdict for {result['symbol']}: {result['verdict']}"
        
        return True, "Valid"
    
    def _calculate_scan_quality(self, scan_results: List[Dict]) -> Dict:
        """
        Calculate quality metrics for a scan
        Returns dict with quality scores
        """
        if not scan_results:
            return {'quality_score': 0.0, 'completeness': 0.0, 'confidence_avg': 0.0}
        
        # Count complete results (have minimum required fields)
        # Make this more flexible - only require core fields
        required_fields = ['symbol', 'verdict']
        optional_fields = ['recommended_strategy', 'multi_timeframe', 'volatility_regime', 'volume', 'spread']
        
        complete_count = 0
        confidence_sum = 0.0
        confidence_count = 0
        
        for result in scan_results:
            # Check if has required fields
            has_required = all(field in result for field in required_fields)
            if not has_required:
                continue
            
            # Count optional fields present
            optional_present = sum(1 for field in optional_fields if field in result)
            completeness_ratio = optional_present / len(optional_fields)
            
            # Consider it "complete" if it has at least 50% of optional fields
            if completeness_ratio >= 0.5:
                complete_count += 1
            
            # Track confidence (check both 'confidence' and 'strategy_confidence')
            conf_value = None
            if 'confidence' in result:
                conf_value = result['confidence']
            elif 'strategy_confidence' in result:
                conf_value = result['strategy_confidence']
            
            if conf_value is not None:
                try:
                    conf_float = float(conf_value)
                    confidence_sum += conf_float
                    confidence_count += 1
                except (ValueError, TypeError):
                    pass
        
        completeness = complete_count / len(scan_results) if len(scan_results) > 0 else 0.0
        avg_confidence = confidence_sum / confidence_count if confidence_count > 0 else 0.0
        
        # Quality score: weighted average of completeness and confidence
        # If no confidence data, use completeness only
        if confidence_count > 0:
            quality_score = (completeness * 0.6) + (avg_confidence * 0.4)
        else:
            quality_score = completeness
        
        return {
            'quality_score': quality_score,
            'completeness': completeness,
            'confidence_avg': avg_confidence,
            'total_symbols': len(scan_results),
            'complete_symbols': complete_count
        }
    
    def save_scan(self, scan_results: List[Dict], metadata: Dict = None, 
                  scan_duration: float = None):
        """
        Save scan results to JSON file with validation and locking
        
        Args:
            scan_results: List of scan result dicts
            metadata: Optional metadata (market conditions, broker status, etc.)
            scan_duration: Time taken to complete scan (seconds)
        """
        start_time = datetime.now()
        
        try:
            # Calculate quality metrics
            quality = self._calculate_scan_quality(scan_results)
            
            # Prepare data
            data = {
                'schema_version': self.SCHEMA_VERSION,
                'timestamp': start_time.isoformat(),
                'scan_count': len(scan_results),
                'scan_duration': scan_duration,
                'quality_metrics': quality,
                'metadata': metadata or {},
                'results': scan_results
            }
            
            # Add market conditions to metadata if not present
            if 'market_conditions' not in data['metadata']:
                data['metadata']['market_conditions'] = {
                    'scan_time': start_time.isoformat(),
                    'day_of_week': start_time.weekday(),
                    'hour': start_time.hour
                }
            
            # Validate before saving
            is_valid, error_msg = self._validate_scan_data(data)
            if not is_valid:
                logger.error(f"Scan validation failed: {error_msg}")
                self.scan_stats['failed_scans'] += 1
                return False
            
            # Save with file locking
            with open(self.scan_file, 'w') as f:
                if self._acquire_file_lock(f, timeout=5):
                    try:
                        json.dump(data, f, indent=2, default=str)
                        logger.info(f"✓ Scan results saved to {self.scan_file}")
                        logger.info(f"  Quality: {quality['quality_score']:.2%}, "
                                  f"Completeness: {quality['completeness']:.2%}, "
                                  f"Avg Confidence: {quality['confidence_avg']:.2f}")
                        
                        # Update stats
                        self.scan_stats['successful_scans'] += 1
                        self.scan_stats['last_scan_time'] = start_time.isoformat()
                        
                        # Save to history
                        self._save_to_history(data)
                        
                        return True
                    finally:
                        self._release_file_lock(f)
                else:
                    logger.error("Could not acquire file lock for saving scan")
                    self.scan_stats['failed_scans'] += 1
                    return False
                    
        except Exception as e:
            logger.error(f"Error saving scan: {e}")
            self.scan_stats['failed_scans'] += 1
            return False
        finally:
            self.scan_stats['total_scans'] += 1
    
    def _save_to_history(self, scan_data: Dict):
        """
        Save scan to history file (keeps last 24 hours)
        """
        try:
            # Load existing history
            history = []
            if Path(self.history_file).exists():
                with open(self.history_file, 'r') as f:
                    if self._acquire_file_lock(f, timeout=2):
                        try:
                            history = json.load(f)
                        finally:
                            self._release_file_lock(f)
            
            # Add new scan
            history.append({
                'timestamp': scan_data['timestamp'],
                'scan_count': scan_data['scan_count'],
                'quality_metrics': scan_data['quality_metrics'],
                'approved_count': sum(1 for r in scan_data['results'] if r['verdict'] == 'APPROVED'),
                'avoid_count': sum(1 for r in scan_data['results'] if r['verdict'] == 'AVOID')
            })
            
            # Remove old scans (older than keep_history_hours)
            cutoff_time = datetime.now() - timedelta(hours=self.keep_history_hours)
            history = [
                h for h in history 
                if datetime.fromisoformat(h['timestamp']) > cutoff_time
            ]
            
            # Save updated history
            with open(self.history_file, 'w') as f:
                if self._acquire_file_lock(f, timeout=2):
                    try:
                        json.dump(history, f, indent=2)
                    finally:
                        self._release_file_lock(f)
                        
        except Exception as e:
            logger.warning(f"Could not save scan history: {e}")
    
    def load_previous_scan(self, validate: bool = True) -> Optional[Dict]:
        """
        Load previous scan results if they exist and are valid
        
        Args:
            validate: If True, validates scan data and checks expiration
        
        Returns:
            Scan data dict or None if not available/invalid
        """
        if not Path(self.scan_file).exists():
            logger.info("No previous scan file found")
            return None
        
        try:
            with open(self.scan_file, 'r') as f:
                if not self._acquire_file_lock(f, timeout=5):
                    logger.error("Could not acquire file lock for loading scan")
                    return None
                
                try:
                    data = json.load(f)
                finally:
                    self._release_file_lock(f)
            
            # Validate if requested
            if validate:
                is_valid, error_msg = self._validate_scan_data(data)
                if not is_valid:
                    logger.warning(f"Previous scan invalid: {error_msg}")
                    return None
            
            # Calculate age
            scan_time = datetime.fromisoformat(data['timestamp'])
            age_minutes = (datetime.now() - scan_time).total_seconds() / 60
            
            logger.info(f"📂 Loaded previous scan from {age_minutes:.1f} minutes ago")
            logger.info(f"   Symbols: {data['scan_count']}, "
                       f"Quality: {data.get('quality_metrics', {}).get('quality_score', 0):.2%}")
            
            return data
            
        except json.JSONDecodeError as e:
            logger.error(f"Corrupted scan file: {e}")
            return None
        except Exception as e:
            logger.error(f"Could not load previous scan: {e}")
            return None
    
    def get_scan_history(self, hours: int = None) -> List[Dict]:
        """
        Get scan history for the last N hours
        
        Args:
            hours: Number of hours to retrieve (default: all available)
        
        Returns:
            List of historical scan summaries
        """
        if not Path(self.history_file).exists():
            return []
        
        try:
            with open(self.history_file, 'r') as f:
                history = json.load(f)
            
            if hours:
                cutoff_time = datetime.now() - timedelta(hours=hours)
                history = [
                    h for h in history 
                    if datetime.fromisoformat(h['timestamp']) > cutoff_time
                ]
            
            return history
            
        except Exception as e:
            logger.error(f"Could not load scan history: {e}")
            return []
    
    def compare_scans(self, old_results: List[Dict], new_results: List[Dict], 
                     active_positions: List[str] = None) -> Dict:
        """
        Compare two scans and identify changes with priority levels
        
        Args:
            old_results: Previous scan results
            new_results: Current scan results
            active_positions: List of symbols with active positions (prioritized)
        
        Returns:
            Dict with categorized changes and priority levels
        """
        changes = {
            'new_approved': [],
            'newly_avoided': [],
            'trend_changes': [],
            'volatility_changes': [],
            'strategy_changes': [],
            'confidence_changes': [],
            'active_position_changes': []  # High priority
        }
        
        # Create lookup dicts
        old_by_symbol = {r['symbol']: r for r in old_results}
        new_by_symbol = {r['symbol']: r for r in new_results}
        
        active_positions = active_positions or []
        
        for symbol in new_by_symbol:
            new = new_by_symbol[symbol]
            old = old_by_symbol.get(symbol)
            
            if not old:
                # New symbol in scan
                if new.get('verdict') == 'APPROVED':
                    changes['new_approved'].append({
                        'symbol': symbol,
                        'strategy': new.get('recommended_strategy'),
                        'confidence': new.get('confidence', 0),
                        'priority': 'HIGH' if symbol in active_positions else 'NORMAL'
                    })
                continue
            
            is_active = symbol in active_positions
            
            # Check verdict changes
            old_verdict = old.get('verdict')
            new_verdict = new.get('verdict')
            
            if old_verdict != new_verdict:
                if old_verdict != 'APPROVED' and new_verdict == 'APPROVED':
                    changes['new_approved'].append({
                        'symbol': symbol,
                        'strategy': new.get('recommended_strategy'),
                        'confidence': new.get('confidence', 0),
                        'priority': 'HIGH' if is_active else 'NORMAL'
                    })
                elif old_verdict == 'APPROVED' and new_verdict == 'AVOID':
                    change_info = {
                        'symbol': symbol,
                        'reason': new.get('avoid_reason', 'Unknown'),
                        'priority': 'CRITICAL' if is_active else 'HIGH'
                    }
                    changes['newly_avoided'].append(change_info)
                    
                    if is_active:
                        changes['active_position_changes'].append({
                            'symbol': symbol,
                            'change_type': 'VERDICT_TO_AVOID',
                            'old_verdict': old_verdict,
                            'new_verdict': new_verdict,
                            'priority': 'CRITICAL'
                        })
            
            # Check trend changes
            old_mtf = old.get('multi_timeframe', {})
            new_mtf = new.get('multi_timeframe', {})
            
            if old_mtf.get('overall') != new_mtf.get('overall'):
                change_info = {
                    'symbol': symbol,
                    'old_trend': old_mtf.get('overall'),
                    'new_trend': new_mtf.get('overall'),
                    'priority': 'CRITICAL' if is_active else 'NORMAL'
                }
                changes['trend_changes'].append(change_info)
                
                if is_active:
                    changes['active_position_changes'].append({
                        'symbol': symbol,
                        'change_type': 'TREND_SHIFT',
                        'old_trend': old_mtf.get('overall'),
                        'new_trend': new_mtf.get('overall'),
                        'priority': 'CRITICAL'
                    })
            
            # Check volatility regime changes
            old_vol = old.get('volatility_regime')
            new_vol = new.get('volatility_regime')
            
            if old_vol != new_vol and old_vol and new_vol:
                change_info = {
                    'symbol': symbol,
                    'old_regime': old_vol,
                    'new_regime': new_vol,
                    'priority': 'HIGH' if is_active else 'LOW'
                }
                changes['volatility_changes'].append(change_info)
                
                if is_active and new_vol == 'expanding':
                    changes['active_position_changes'].append({
                        'symbol': symbol,
                        'change_type': 'VOLATILITY_EXPANDING',
                        'old_regime': old_vol,
                        'new_regime': new_vol,
                        'priority': 'HIGH'
                    })
            
            # Check strategy changes
            old_strategy = old.get('recommended_strategy')
            new_strategy = new.get('recommended_strategy')
            
            if old_strategy != new_strategy and old_strategy and new_strategy:
                changes['strategy_changes'].append({
                    'symbol': symbol,
                    'old_strategy': old_strategy,
                    'new_strategy': new_strategy,
                    'priority': 'NORMAL'
                })
            
            # Check confidence changes (significant = >0.10 change)
            old_conf = old.get('confidence', 0)
            new_conf = new.get('confidence', 0)
            
            if abs(old_conf - new_conf) >= 0.10:
                change_info = {
                    'symbol': symbol,
                    'old_confidence': old_conf,
                    'new_confidence': new_conf,
                    'change': new_conf - old_conf,
                    'priority': 'HIGH' if is_active else 'LOW'
                }
                changes['confidence_changes'].append(change_info)
                
                if is_active and new_conf < 0.60:
                    changes['active_position_changes'].append({
                        'symbol': symbol,
                        'change_type': 'CONFIDENCE_DROP',
                        'old_confidence': old_conf,
                        'new_confidence': new_conf,
                        'priority': 'HIGH'
                    })
        
        return changes
    
    def print_changes(self, changes: Dict, max_display: int = 10, 
                     min_priority: str = 'LOW'):
        """
        Print detected changes in a readable format with priority filtering
        
        Args:
            changes: Changes dict from compare_scans()
            max_display: Maximum items to display per category
            min_priority: Minimum priority to display (LOW, NORMAL, HIGH, CRITICAL)
        """
        priority_levels = {'LOW': 0, 'NORMAL': 1, 'HIGH': 2, 'CRITICAL': 3}
        min_level = priority_levels.get(min_priority, 0)
        
        # Check if any changes exist
        has_changes = any(changes.values())
        if not has_changes:
            print("📊 No significant changes detected since last scan")
            return
        
        print("\n" + "="*70)
        print("📊 MARKET CHANGES DETECTED")
        print("="*70)
        
        # CRITICAL: Active position changes (always show first)
        if changes['active_position_changes']:
            print(f"\n🚨 ACTIVE POSITION ALERTS ({len(changes['active_position_changes'])}):")
            for change in changes['active_position_changes'][:max_display]:
                priority_icon = "🔴" if change['priority'] == 'CRITICAL' else "🟡"
                print(f"   {priority_icon} {change['symbol']}: {change['change_type']}")
                
                if change['change_type'] == 'VERDICT_TO_AVOID':
                    print(f"      ⚠️  NOW AVOIDING - Consider closing position!")
                elif change['change_type'] == 'TREND_SHIFT':
                    print(f"      Trend: {change['old_trend']} → {change['new_trend']}")
                elif change['change_type'] == 'VOLATILITY_EXPANDING':
                    print(f"      Volatility expanding - Risk increased!")
                elif change['change_type'] == 'CONFIDENCE_DROP':
                    print(f"      Confidence: {change['old_confidence']:.2f} → {change['new_confidence']:.2f}")
        
        # Filter and display other changes by priority
        def filter_by_priority(items):
            if isinstance(items, list) and items and isinstance(items[0], dict):
                return [item for item in items 
                       if priority_levels.get(item.get('priority', 'LOW'), 0) >= min_level]
            return items
        
        # New opportunities
        new_approved = filter_by_priority(changes['new_approved'])
        if new_approved:
            print(f"\n✅ NEW OPPORTUNITIES ({len(new_approved)}):")
            for item in new_approved[:max_display]:
                if isinstance(item, dict):
                    priority_icon = "⭐" if item.get('priority') == 'HIGH' else "•"
                    print(f"   {priority_icon} {item['symbol']}: {item.get('strategy', 'N/A')} "
                          f"(confidence: {item.get('confidence', 0):.2f})")
                else:
                    print(f"   • {item}")
        
        # Now avoiding
        newly_avoided = filter_by_priority(changes['newly_avoided'])
        if newly_avoided:
            print(f"\n❌ NOW AVOIDING ({len(newly_avoided)}):")
            for item in newly_avoided[:max_display]:
                if isinstance(item, dict):
                    priority_icon = "🔴" if item.get('priority') == 'CRITICAL' else "⚠️"
                    print(f"   {priority_icon} {item['symbol']}: {item.get('reason', 'Unknown')}")
                else:
                    print(f"   • {item}")
        
        # Trend shifts
        trend_changes = filter_by_priority(changes['trend_changes'])
        if trend_changes:
            print(f"\n🔄 TREND SHIFTS ({len(trend_changes)}):")
            for change in trend_changes[:max_display]:
                priority_icon = "🔴" if change.get('priority') == 'CRITICAL' else "•"
                print(f"   {priority_icon} {change['symbol']}: "
                      f"{change['old_trend']} → {change['new_trend']}")
        
        # Volatility changes
        vol_changes = filter_by_priority(changes['volatility_changes'])
        if vol_changes:
            print(f"\n📈 VOLATILITY CHANGES ({len(vol_changes)}):")
            for change in vol_changes[:max_display]:
                priority_icon = "⚠️" if change.get('priority') == 'HIGH' else "•"
                print(f"   {priority_icon} {change['symbol']}: "
                      f"{change['old_regime']} → {change['new_regime']}")
        
        # Confidence changes
        conf_changes = filter_by_priority(changes['confidence_changes'])
        if conf_changes:
            print(f"\n💯 CONFIDENCE CHANGES ({len(conf_changes)}):")
            for change in conf_changes[:max_display]:
                direction = "📈" if change['change'] > 0 else "📉"
                priority_icon = "⚠️" if change.get('priority') == 'HIGH' else "•"
                print(f"   {priority_icon} {change['symbol']}: "
                      f"{change['old_confidence']:.2f} → {change['new_confidence']:.2f} "
                      f"{direction} ({change['change']:+.2f})")
        
        # Strategy updates
        if changes['strategy_changes']:
            print(f"\n📊 STRATEGY UPDATES ({len(changes['strategy_changes'])}):")
            for change in changes['strategy_changes'][:max_display]:
                print(f"   • {change['symbol']}: "
                      f"{change['old_strategy']} → {change['new_strategy']}")
        
        print("="*70 + "\n")
    
    def get_scan_stats(self) -> Dict:
        """
        Get scan statistics and quality metrics
        
        Returns:
            Dict with scan performance stats
        """
        stats = self.scan_stats.copy()
        
        # Calculate success rate
        if stats['total_scans'] > 0:
            stats['success_rate'] = stats['successful_scans'] / stats['total_scans']
        else:
            stats['success_rate'] = 0.0
        
        # Get history stats
        history = self.get_scan_history(hours=24)
        if history:
            stats['scans_last_24h'] = len(history)
            stats['avg_quality_24h'] = sum(h.get('quality_metrics', {}).get('quality_score', 0) 
                                          for h in history) / len(history)
            stats['avg_approved_24h'] = sum(h.get('approved_count', 0) for h in history) / len(history)
        else:
            stats['scans_last_24h'] = 0
            stats['avg_quality_24h'] = 0.0
            stats['avg_approved_24h'] = 0.0
        
        return stats
    
    def print_scan_stats(self):
        """Print scan statistics in readable format"""
        stats = self.get_scan_stats()
        
        print("\n" + "="*70)
        print("📊 SCAN MANAGER STATISTICS")
        print("="*70)
        print(f"Total scans: {stats['total_scans']}")
        print(f"Successful: {stats['successful_scans']} ({stats['success_rate']:.1%})")
        print(f"Failed: {stats['failed_scans']}")
        
        if stats['last_scan_time']:
            last_scan = datetime.fromisoformat(stats['last_scan_time'])
            age = (datetime.now() - last_scan).total_seconds() / 60
            print(f"Last scan: {age:.1f} minutes ago")
        
        print(f"\nLast 24 hours:")
        print(f"  Scans: {stats['scans_last_24h']}")
        print(f"  Avg quality: {stats['avg_quality_24h']:.1%}")
        print(f"  Avg approved: {stats['avg_approved_24h']:.1f} symbols")
        print("="*70 + "\n")