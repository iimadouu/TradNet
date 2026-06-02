"""
Trade Logger - CSV logging for post-analysis - ENHANCED
trade_logger.py
"""

import csv
from datetime import datetime
from pathlib import Path
from typing import Dict
import threading

class TradeLogger:
    def __init__(self, log_file: str = "trades_log.csv"):
        self.log_file = log_file
        self._lock = threading.Lock()
        self.ensure_log_exists()
    
    def ensure_log_exists(self):
        """Create log file with headers if it doesn't exist"""
        if not Path(self.log_file).exists():
            with open(self.log_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'Timestamp', 'Symbol', 'Action', 'Strategy', 'Confidence',
                    'Entry_Price', 'SL', 'TP', 'Lot_Size', 'Ticket',
                    'Exit_Time', 'Exit_Price', 'Profit_Pips', 'Exit_Reason',
                    'Indicators'
                ])
    
    def log_entry(self, trade_data: Dict):
        """Log trade entry - THREAD SAFE"""
        with self._lock:
            with open(self.log_file, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    trade_data.get('timestamp', datetime.now()),
                    trade_data.get('symbol'),
                    trade_data.get('action'),
                    trade_data.get('strategy'),
                    trade_data.get('confidence'),
                    trade_data.get('entry_price'),
                    trade_data.get('sl'),
                    trade_data.get('tp'),
                    trade_data.get('lot_size'),
                    trade_data.get('ticket'),
                    '',  # Exit time (filled later)
                    '',  # Exit price (filled later)
                    '',  # Profit pips (filled later)
                    '',  # Exit reason (filled later)
                    trade_data.get('indicators', '')
                ])
    
    def log_exit(self, symbol: str, exit_price: float, profit_pips: float, reason: str):
        """Update log with exit information - THREAD SAFE"""
        with self._lock:
            try:
                # Read all rows
                rows = []
                with open(self.log_file, 'r', newline='') as f:
                    reader = csv.reader(f)
                    rows = list(reader)
                
                # Find last entry for this symbol without exit data
                updated = False
                for i in range(len(rows) - 1, 0, -1):
                    if len(rows[i]) > 10 and rows[i][1] == symbol and rows[i][10] == '':
                        rows[i][10] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        rows[i][11] = str(exit_price)
                        rows[i][12] = f"{profit_pips:.1f}"
                        rows[i][13] = reason
                        updated = True
                        break
                
                if updated:
                    # Write back
                    with open(self.log_file, 'w', newline='') as f:
                        writer = csv.writer(f)
                        writer.writerows(rows)
                else:
                    # Fallback: append exit record
                    with open(self.log_file, 'a', newline='') as f:
                        writer = csv.writer(f)
                        writer.writerow([
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            symbol, 'EXIT', '', '', '', '', '', '', '',
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            str(exit_price), f"{profit_pips:.1f}", reason, ''
                        ])
                        
            except Exception as e:
                print(f"⚠ Log update failed for {symbol}: {e}")
                # Fallback: append new exit record
                try:
                    with open(self.log_file, 'a', newline='') as f:
                        writer = csv.writer(f)
                        writer.writerow([
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            symbol, 'EXIT', '', '', '', '', '', '', '',
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            str(exit_price), f"{profit_pips:.1f}", reason, ''
                        ])
                except Exception as e2:
                    print(f"⚠ Fallback log also failed: {e2}")