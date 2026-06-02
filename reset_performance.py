"""
Quick script to reset performance data for testing
This will backup your current data and create a fresh start
"""
import json
import shutil
from datetime import datetime

# Backup current data
backup_file = f'performance_data_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
try:
    shutil.copy('performance_data.json', backup_file)
    print(f"✅ Backed up to: {backup_file}")
except Exception as e:
    print(f"⚠️ Backup failed: {e}")

# Create fresh performance data
fresh_data = {
    "trades": [],
    "stats_by_strategy": {},
    "stats_by_symbol": {},
    "stats_by_hour": {},
    "stats_by_day": {}
}

with open('performance_data.json', 'w') as f:
    json.dump(fresh_data, f, indent=2)

print("✅ Performance data reset!")
print("   You can now test the bot with a clean slate")
print(f"   Your old data is saved in: {backup_file}")
