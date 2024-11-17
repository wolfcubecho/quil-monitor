import subprocess
import json
from datetime import datetime, timedelta
import re
import os
import requests
import glob
import sys

def check_sudo():
    if os.geteuid() != 0:
        print("This script requires sudo privileges")
        sys.exit(1)

class QuilNodeMonitor:
    def __init__(self, log_file="quil_metrics.json"):
        self.log_file = log_file
        self.history = self._load_history()
        self.node_binary = self._get_latest_node_binary()

    def _get_latest_node_binary(self):
        try:
            node_binaries = glob.glob('./node-*-linux-amd64')
            if not node_binaries:
                raise Exception("No node binary found")
            
            def get_version_tuple(binary):
                version_match = re.search(r'node-(\d+)\.(\d+)\.(\d+)\.(\d+)-linux-amd64', binary)
                if version_match:
                    return tuple(int(x) for x in version_match.groups())
                return (0, 0, 0, 0)

            node_binaries.sort(key=get_version_tuple, reverse=True)
            latest_binary = node_binaries[0]
            
            if not os.path.exists(latest_binary):
                raise Exception(f"Binary {latest_binary} not found")
            if not os.access(latest_binary, os.X_OK):
                raise Exception(f"Binary {latest_binary} is not executable")
            
            print(f"Using node binary: {latest_binary}")
            return latest_binary
        except Exception as e:
            print(f"Error finding node binary: {e}")
            sys.exit(1)

    def _load_history(self):
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, 'r') as f:
                    return json.load(f)
            except:
                return {'daily_balance': {}, 'shard_metrics': {}}
        return {'daily_balance': {}, 'shard_metrics': {}}

    def _save_history(self):
        try:
            with open(self.log_file, 'w') as f:
                json.dump(self.history, f, indent=2)
        except Exception as e:
            print(f"Error saving history: {e}")

    def get_quil_price(self):
        try:
            url = "https://api.coingecko.com/api/v3/simple/price"
            params = {
                "ids": "wrapped-quil",
                "vs_currencies": "usd"
            }
            response = requests.get(url, params=params)
            data = response.json()
            return data.get("wrapped-quil", {}).get("usd", 0)
        except Exception as e:
            return 0

    def get_node_info(self):
        try:
            result = subprocess.run([self.node_binary, '--node-info'], 
                                 capture_output=True, text=True)
            
            if result.returncode != 0:
                return None

            ring_match = re.search(r'Prover Ring: (\d+)', result.stdout)
            ring = int(ring_match.group(1)) if ring_match else 0

            owned_balance_match = re.search(r'Owned balance: ([\d.]+) QUIL', result.stdout)
            owned_balance = float(owned_balance_match.group(1)) if owned_balance_match else 0

            date = datetime.now().strftime('%Y-%m-%d')
            self.history['daily_balance'][date] = owned_balance
            self._save_history()

            return {
                'ring': ring,
                'active_workers': 1024,
                'owned': owned_balance,
                'total': owned_balance
            }
        except Exception as e:
            print(f"Error getting node info: {e}")
            return None

    def get_shard_metrics(self, date=None):
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        
        try:
            cmd = f'journalctl -u ceremonyclient.service --since "{date} 00:00:00" --until "{date} 23:59:59" --no-hostname -o cat | grep -i shard'
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            shards = []
            fast_shards = 0    # 0-30 seconds
            medium_shards = 0  # 30-60 seconds
            slow_shards = 0    # 60+ seconds
            
            for line in result.stdout.splitlines():
                try:
                    data = json.loads(line)
                    frame_age = data.get('frame_age', 0)
                    shards.append(frame_age)
                    
                    if frame_age <= 30:
                        fast_shards += 1
                    elif frame_age <= 60:
                        medium_shards += 1
                    else:
                        slow_shards += 1
                except:
                    continue
            
            total_shards = len(shards)
            if total_shards > 0:
                current_time = datetime.now()
                if date == current_time.strftime('%Y-%m-%d'):
                    hours_passed = current_time.hour + current_time.minute / 60
                    shards_per_hour = total_shards / (hours_passed if hours_passed > 0 else 1)
                else:
                    shards_per_hour = total_shards / 24
                
                avg_frame_age = sum(shards) / total_shards
                
                # Calculate percentages
                fast_percent = (fast_shards / total_shards * 100) if total_shards > 0 else 0
                medium_percent = (medium_shards / total_shards * 100) if total_shards > 0 else 0
                slow_percent = (slow_shards / total_shards * 100) if total_shards > 0 else 0
            else:
                shards_per_hour = 0
                avg_frame_age = 0
                fast_percent = medium_percent = slow_percent = 0
            
            return {
                'date': date,
                'total_shards': total_shards,
                'shards_per_hour': shards_per_hour,
                'avg_frame_age': avg_frame_age,
                'fast_shards': fast_shards,
                'medium_shards': medium_shards,
                'slow_shards': slow_shards,
                'fast_percent': fast_percent,
                'medium_percent': medium_percent,
                'slow_percent': slow_percent
            }
        except Exception as e:
            return {
                'date': date,
                'total_shards': 0,
                'shards_per_hour': 0,
                'avg_frame_age': 0,
                'fast_shards': 0,
                'medium_shards': 0,
                'slow_shards': 0,
                'fast_percent': 0,
                'medium_percent': 0,
                'slow_percent': 0
            }

    def get_daily_earnings(self, date):
        try:
            yesterday = (datetime.strptime(date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
            
            if yesterday not in self.history['daily_balance']:
                return 0
                
            if date not in self.history['daily_balance']:
                if date == datetime.now().strftime('%Y-%m-%d'):
                    current_balance = self.get_node_info()['owned']
                else:
                    return 0
            else:
                current_balance = self.history['daily_balance'][date]
            
            yesterday_balance = self.history['daily_balance'][yesterday]
            return current_balance - yesterday_balance
        except Exception as e:
            return 0

    def get_earnings_data(self):
        dates = sorted(self.history['daily_balance'].keys())
        earnings = []
        
        for i in range(len(dates)-1):
            current_date = dates[i+1]
            daily_earn = self.get_daily_earnings(current_date)
            earnings.append((current_date, daily_earn))
            
        return sorted(earnings, reverse=True)

    def display_stats(self):
        print("\n=== QUIL Node Statistics ===")
        current_time = datetime.now()
        print(f"Time: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        node_info = self.get_node_info()
        quil_price = self.get_quil_price()
        
        earnings_data = self.get_earnings_data()
        
        if earnings_data:
            recent_earnings = earnings_data[:7]
            weekly_total = sum(earn for _, earn in recent_earnings)
            daily_avg = weekly_total / len(recent_earnings)
            weekly_avg = weekly_total
            
            if len(earnings_data) >= 30:
                monthly_total = sum(earn for _, earn in earnings_data[:30])
                monthly_avg = monthly_total
            else:
                monthly_avg = weekly_avg * 4
        else:
            daily_avg = weekly_avg = monthly_avg = 0
        
        if node_info:
            print(f"\nNode Information:")
            print(f"Ring:            {node_info['ring']}")
            print(f"Active Workers:  {node_info['active_workers']}")
            print(f"QUIL Price:      ${quil_price:.4f}")
            print(f"QUIL on Node:    {node_info['total']:.6f}")
            print(f"Daily Average:   {daily_avg:.6f} QUIL // ${daily_avg * quil_price:.2f}")
            print(f"Weekly Average:  {weekly_avg:.6f} QUIL // ${weekly_avg * quil_price:.2f}")
            print(f"Monthly Average: {monthly_avg:.6f} QUIL // ${monthly_avg * quil_price:.2f}")
        
        today = current_time.strftime('%Y-%m-%d')
        today_metrics = self.get_shard_metrics(today)
        today_earnings = self.get_daily_earnings(today)
        
        print(f"\nToday's Stats ({today}):")
        print(f"Earnings:        {today_earnings:.6f} QUIL // ${today_earnings * quil_price:.2f}")
        print(f"Shard Processing:")
        print(f"  Total Shards:    {today_metrics['total_shards']}")
        print(f"  Shards/Hour:     {today_metrics['shards_per_hour']:.2f}")
        print(f"  Average Time:    {today_metrics['avg_frame_age']:.2f} seconds")
        print(f"  0-30 sec:        {today_metrics['fast_shards']} shards ({today_metrics['fast_percent']:.1f}%)")
        print(f"  30-60 sec:       {today_metrics['medium_shards']} shards ({today_metrics['medium_percent']:.1f}%)")
        print(f"  60+ sec:         {today_metrics['slow_shards']} shards ({today_metrics['slow_percent']:.1f}%)")

        print("\nEarnings History:")
        for date, earn in earnings_data[:7]:
            print(f"{date}: {earn:.6f} QUIL // ${earn * quil_price:.2f}")

if __name__ == "__main__":
    check_sudo()
    monitor = QuilNodeMonitor()
    monitor.display_stats()
