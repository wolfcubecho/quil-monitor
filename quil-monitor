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
        print("This script requires sudo privileges to access node info and logs.")
        print("Please run with: sudo python3 quil_monitor.py")
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
            print(f"Error getting QUIL price: {e}")
            return 0

    def get_node_info(self):
        try:
            result = subprocess.run([self.node_binary, '--node-info'], 
                                 capture_output=True, text=True)
            
            if result.returncode != 0:
                print(f"Error running node info command: {result.stderr}")
                return None

            ring_match = re.search(r'Prover Ring: (\d+)', result.stdout)
            ring = int(ring_match.group(1)) if ring_match else 0

            try:
                workers_cmd = 'journalctl -u ceremonyclient.service --since "1 minute ago" --no-hostname -o cat | grep -i shard | tail -n 1'
                workers_result = subprocess.run(workers_cmd, shell=True, capture_output=True, text=True)
                if workers_result.stdout.strip():
                    workers_data = json.loads(workers_result.stdout.strip())
                    active_workers = workers_data.get('active_workers', 0)
                else:
                    active_workers = 1024
            except Exception as e:
                print(f"Warning: Could not get worker count: {e}")
                active_workers = 1024

            owned_balance_match = re.search(r'Owned balance: ([\d.]+) QUIL', result.stdout)
            owned_balance = float(owned_balance_match.group(1)) if owned_balance_match else 0

            bridged_balance_match = re.search(r'Bridged balance: ([\d.]+) QUIL', result.stdout)
            bridged_balance = float(bridged_balance_match.group(1)) if bridged_balance_match else 0

            return {
                'ring': ring,
                'active_workers': active_workers,
                'owned': owned_balance,
                'bridged': bridged_balance,
                'total': owned_balance + bridged_balance
            }
        except Exception as e:
            print(f"Error getting node info: {e}")
            return None

    def get_calendar_day_metrics(self, days_ago=0):
        try:
            target_date = datetime.now() - timedelta(days=days_ago)
            start_time = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_time = start_time + timedelta(days=1)

            cmd = f'journalctl -u ceremonyclient.service --since "{start_time.strftime("%Y-%m-%d %H:%M:%S")}" --until "{end_time.strftime("%Y-%m-%d %H:%M:%S")}" --no-hostname -o cat | grep -i shard'
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            shard_times = []
            prev_frame = None
            prev_time = None
            frame_numbers = set()
            frame_ages = []
            
            for line in result.stdout.splitlines():
                try:
                    data = json.loads(line)
                    frame_number = data.get('frame_number')
                    timestamp = data.get('ts')
                    frame_age = data.get('frame_age', 0)
                    
                    frame_numbers.add(frame_number)
                    frame_ages.append(frame_age)
                    
                    if prev_frame is not None and prev_time is not None:
                        time_diff = timestamp - prev_time
                        shard_times.append(time_diff)
                    
                    prev_frame = frame_number
                    prev_time = timestamp
                except:
                    continue
            
            return {
                'date': start_time.strftime('%Y-%m-%d'),
                'unique_frames': len(frame_numbers),
                'total_entries': len(shard_times) + 1,
                'avg_time': sum(shard_times) / len(shard_times) if shard_times else 0,
                'avg_frame_age': sum(frame_ages) / len(frame_ages) if frame_ages else 0,
                'min_frame_age': min(frame_ages) if frame_ages else 0,
                'max_frame_age': max(frame_ages) if frame_ages else 0,
                'total_shards': len(frame_numbers)
            }
        except Exception as e:
            print(f"Error getting metrics for {target_date.strftime('%Y-%m-%d')}: {e}")
            return {
                'date': target_date.strftime('%Y-%m-%d'),
                'unique_frames': 0,
                'total_entries': 0,
                'avg_time': 0,
                'avg_frame_age': 0,
                'min_frame_age': 0,
                'max_frame_age': 0,
                'total_shards': 0
            }

    def update_metrics(self):
        today = datetime.now().strftime('%Y-%m-%d')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        
        node_info = self.get_node_info()
        if node_info is not None:
            current_balance = node_info['owned']
            self.history['daily_balance'][today] = current_balance
            
            if yesterday not in self.history['daily_balance']:
                self.history['daily_balance'][yesterday] = current_balance - 0.1
        
        metrics = {
            'today': self.get_calendar_day_metrics(0),
            'yesterday': self.get_calendar_day_metrics(1),
            'last_7_days': [self.get_calendar_day_metrics(i) for i in range(7)]
        }
        
        self.history['shard_metrics'][today] = metrics['today']
        
        self._save_history()
        return node_info, metrics

    def display_stats(self):
        print("\n=== QUIL Node Statistics ===")
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        node_info, metrics = self.update_metrics()
        quil_price = self.get_quil_price()
        
        balances = [(date, bal) for date, bal in self.history['daily_balance'].items()]
        balances.sort(reverse=True)
        
        weekly_avg = monthly_avg = daily_avg = 0
        current_balance = next(iter(balances))[1] if balances else 0
        
        if len(balances) >= 2:
            total_days = min(7, len(balances) - 1)
            if total_days > 0:
                oldest_balance_within_range = balances[total_days][1]
                total_earn = current_balance - oldest_balance_within_range
                daily_avg = total_earn / total_days
                weekly_avg = daily_avg * 7
                monthly_avg = daily_avg * 30

        if node_info:
            print(f"\nNode Information:")
            print(f"Ring:            {node_info['ring']}")
            print(f"Active Workers:  {node_info['active_workers']}")
            print(f"QUIL Price:      ${quil_price:.4f}")
            print(f"QUIL on Node:    {node_info['total']:.6f}")
            print(f"Weekly Average:  {weekly_avg:.6f} QUIL // ${weekly_avg * quil_price:.2f}")
            print(f"Monthly Average: {monthly_avg:.6f} QUIL // ${monthly_avg * quil_price:.2f}")
            print(f"Daily Average:   {daily_avg:.6f} QUIL // ${daily_avg * quil_price:.2f}")

        if len(balances) >= 2:
            latest_balance = balances[0][1]
            prev_balance = balances[1][1] if len(balances) > 1 else latest_balance
            daily_earn = latest_balance - prev_balance
            
            today_data = metrics['today']
            print(f"\nToday's Stats ({datetime.now().strftime('%Y-%m-%d')}):")
            print(f"Earnings:        {daily_earn:.6f} QUIL // ${daily_earn * quil_price:.2f}")
            print(f"Total Shards:    {today_data['total_shards']}")
            print(f"Avg Time Between: {today_data['avg_time']:.2f} seconds")
            print(f"Avg Frame Age:    {today_data['avg_frame_age']:.2f} seconds")

            print("\nEarnings History:")
            for i in range(len(balances)-1):
                if i >= 7: break
                date = balances[i][0]
                daily_earn = balances[i][1] - balances[i+1][1] if i+1 < len(balances) else 0
                print(f"{date}: {daily_earn:.6f} QUIL // ${daily_earn * quil_price:.2f}")

if __name__ == "__main__":
    check_sudo()
    monitor = QuilNodeMonitor()
    monitor.display_stats()
