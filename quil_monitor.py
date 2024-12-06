#!/usr/bin/env python3
import subprocess
import json
from datetime import datetime, timedelta
import re
import os
import requests
import sys

# Processing Time Thresholds (seconds)
THRESHOLDS = {
    'creation': {
        'good': 17,      # 0-17s
        'warning': 50    # 17-50s, >50s critical
    },
    'submission': {
        'good': 28,      # 0-28s
        'warning': 70    # 28-70s, >70s critical
    },
    'cpu': {
        'good': 20,      # 0-20s
        'warning': 30    # 20-30s, >30s critical
    }
}

# ANSI Colors
COLORS = {
    'green': '\033[92m',
    'yellow': '\033[93m',
    'red': '\033[91m',
    'reset': '\033[0m',
    'bold': '\033[1m',
    'cyan': '\033[96m'
}

class QuilNodeMonitor:
    def __init__(self):
        self.history_file = "quil_history.json"
        self.history = self._load_history()
        home = os.path.expanduser('~')
        self.node_binary = self._get_binary(f"{home}/ceremonyclient/node", "node")
        self.qclient_binary = self._get_binary(f"{home}/ceremonyclient/client", "qclient")
        
    def _get_binary(self, directory, prefix):
        cmd = f'find "{directory}" -type f -executable -name "{prefix}-*" ! -name "*.dgst*" ! -name "*.sig*" | sort -V | tail -n 1'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0 or not result.stdout.strip():
            print(f"Error: No {prefix} binary found")
            sys.exit(1)
        return result.stdout.strip()

    def _load_history(self):
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r') as f:
                    return json.load(f)
            except:
                return self._init_history()
        return self._init_history()
        
    def _init_history(self):
        return {
            'daily_metrics': {},
            'daily_earnings': {},
            'landing_rates': {},
            'daily_balance': {}
        }

    def _save_history(self):
        try:
            # Keep only recent history (last 30 days)
            cutoff = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
            for key in ['daily_metrics', 'daily_earnings', 'landing_rates', 'daily_balance']:
                if key in self.history:
                    self.history[key] = {k: v for k, v in self.history[key].items() 
                                       if k >= cutoff}
            
            with open(self.history_file, 'w') as f:
                json.dump(self.history, f, indent=2)
        except Exception as e:
            print(f"Error saving history: {e}")

    def get_node_info(self):
        result = subprocess.run([self.node_binary, '--node-info'], 
                              capture_output=True, text=True)
        if result.returncode != 0:
            return None
            
        patterns = {
            'ring': r'Prover Ring: (\d+)',
            'active_workers': r'Active Workers: (\d+)',
            'seniority': r'Seniority: (\d+)',
            'owned_balance': r'Owned balance: ([\d.]+) QUIL'
        }
        
        info = {}
        for key, pattern in patterns.items():
            match = re.search(pattern, result.stdout)
            value = float(match.group(1)) if match else 0
            info[key] = int(value) if key != 'owned_balance' else value

        # Store balance in history
        today = datetime.now().strftime('%Y-%m-%d')
        self.history['daily_balance'][today] = info['owned_balance']
        return info

    def get_quil_price(self):
        try:
            response = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "wrapped-quil", "vs_currencies": "usd"}
            )
            return response.json().get("wrapped-quil", {}).get("usd", 0)
        except:
            return 0

    def calculate_stats(self, times, thresholds):
        if not times:
            return {
                'total': 0,
                'good': 0,
                'warning': 0,
                'critical': 0,
                'good_pct': 0,
                'warning_pct': 0,
                'critical_pct': 0,
                'avg_time': 0
            }
            
        total = len(times)
        good = sum(1 for t in times if t <= thresholds['good'])
        warning = sum(1 for t in times if thresholds['good'] < t <= thresholds['warning'])
        critical = sum(1 for t in times if t > thresholds['warning'])
        
        return {
            'total': total,
            'good': good,
            'warning': warning,
            'critical': critical,
            'good_pct': (good/total)*100 if total > 0 else 0,
            'warning_pct': (warning/total)*100 if total > 0 else 0,
            'critical_pct': (critical/total)*100 if total > 0 else 0,
            'avg_time': sum(times)/total if total > 0 else 0
        }

    def get_coin_data(self):
        """Get coin transactions since midnight"""
        today = datetime.now().strftime('%Y-%m-%d')
        result = subprocess.run(
            [self.qclient_binary, 'token', 'coins', 'metadata', '--public-rpc'],
            capture_output=True, text=True
        )
        
        coins = 0
        earnings = 0
        for line in result.stdout.splitlines():
            if 'Timestamp' in line and today in line and 'QUIL' in line:
                amount_match = re.search(r'([\d.]+)\s*QUIL', line)
                if amount_match:
                    amount = float(amount_match.group(1))
                    if amount <= 30:  # Only count mining rewards
                        coins += 1
                        earnings += amount

        # Store in history
        self.history['daily_earnings'][today] = earnings
        self._save_history()
        return coins, earnings

    def get_earnings_history(self, days=7):
        """Get historical earnings with landing rates"""
        history_data = []
        today = datetime.now().date()
    
        for i in range(days):
            date = (today - timedelta(days=i)).strftime('%Y-%m-%d')
            earnings = self.history.get('daily_earnings', {}).get(date, 0)
            landing = self.history.get('landing_rates', {}).get(date, {'rate': 0, 'coins': 0, 'frames': 0})
            prev_balance = self.history.get('daily_balance', {}).get(date, 0)
            history_data.append((date, earnings, landing, prev_balance))
    
        return history_data

    def process_logs(self):
        """Process logs using single journalctl command"""
        cmd = f"""journalctl -u ceremonyclient.service --since today --no-hostname -o json | grep -E 'creating data shard ring proof|submitting data proof'"""
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        
        creation_data = {}
        creation_times = []
        submission_times = []
        cpu_times = []
        frames = set()
        transactions = set()
        
        for line in result.stdout.splitlines():
            try:
                data = json.loads(line)
                msg = json.loads(data.get('MESSAGE', '{}'))
                frame_number = msg.get('frame_number')
                frame_age = float(msg.get('frame_age', 0))
                
                if "creating data shard ring proof" in data.get('MESSAGE', ''):
                    creation_times.append(frame_age)
                    creation_data[frame_number] = frame_age
                    frames.add(frame_number)
                elif "submitting data proof" in data.get('MESSAGE', ''):
                    submission_times.append(frame_age)
                    transactions.add(frame_number)
                    if frame_number in creation_data:
                        cpu_time = frame_age - creation_data[frame_number]
                        if cpu_time > 0:
                            cpu_times.append(cpu_time)
            except:
                continue

        today = datetime.now().strftime('%Y-%m-%d')
        metrics = {
            'creation': self.calculate_stats(creation_times, THRESHOLDS['creation']),
            'submission': self.calculate_stats(submission_times, THRESHOLDS['submission']),
            'cpu': self.calculate_stats(cpu_times, THRESHOLDS['cpu']),
            'frames': len(frames),
            'submitted': len(transactions)
        }
        
        # Store metrics in history
        self.history['daily_metrics'][today] = metrics
        return metrics

    def display_stats(self):
        node_info = self.get_node_info()
        if not node_info:
            print("Failed to get node info")
            return

        metrics = self.process_logs()
        quil_price = self.get_quil_price()
        coins, earnings = self.get_coin_data()
        
        # Calculate landing rate from actual coins
        landing_rate = (coins / metrics['frames'] * 100) if metrics['frames'] > 0 else 0
        
        # Store landing rate
        today = datetime.now().strftime('%Y-%m-%d')
        self.history['landing_rates'][today] = {
            'rate': landing_rate,
            'coins': coins,
            'frames': metrics['frames']
        }
        
        # Get history and calculate averages
        history = self.get_earnings_history(7)
        valid_earnings = [earn for _, earn, _, _ in history if earn > 0]
        daily_avg = sum(valid_earnings) / len(valid_earnings) if valid_earnings else 0
        weekly_avg = daily_avg * 7
        monthly_avg = daily_avg * 30

        print("\n=== QUIL Node Statistics ===")
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        print(f"\nNode Information:")
        print(f"Ring: {node_info['ring']}")
        print(f"Active Workers: {node_info['active_workers']}")
        print(f"Seniority: {node_info['seniority']}")
        print(f"QUIL Price: ${quil_price:.4f}")
        print(f"Balance: {node_info['owned_balance']:.6f} QUIL (${node_info['owned_balance'] * quil_price:.2f})")
        
        print(f"\nEarnings Averages:")
        print(f"Daily Average:   {daily_avg:.6f} QUIL // ${daily_avg * quil_price:.2f}")
        print(f"Weekly Average:  {weekly_avg:.6f} QUIL // ${weekly_avg * quil_price:.2f}")
        print(f"Monthly Average: {monthly_avg:.6f} QUIL // ${monthly_avg * quil_price:.2f}")
        
        print(f"\nToday's Performance:")
        print(f"Earnings: {earnings:.6f} QUIL // ${earnings * quil_price:.2f}")
        print(f"Landing Rate: {landing_rate:.2f}% ({coins}/{metrics['frames']} frames)")

        self._display_section("Creation Stage (Network Latency)", 
                          metrics['creation'], 
                          THRESHOLDS['creation'])
        self._display_section("Submission Stage (Total Time)", 
                          metrics['submission'], 
                          THRESHOLDS['submission'])
        self._display_section("CPU Processing Time", 
                          metrics['cpu'], 
                          THRESHOLDS['cpu'])

        print("\nHistory (Last 7 Days):")
        for date, earnings, landing, balance in history:
            print(f"{date}: {earnings:.6f} QUIL // ${earnings * quil_price:.2f} "
                  f"(Landing: {landing['rate']:.2f}% - {landing['coins']}/{landing['frames']} frames)")

    def _display_section(self, title, stats, thresholds):
        print(f"\n{title}:")
        print(f"  Total Proofs:    {stats['total']}")
        print(f"  Average Time:    {stats['avg_time']:.2f}s")
        
        good_color = COLORS['green'] if stats['good_pct'] > 50 else COLORS['reset']
        warning_color = COLORS['yellow'] if stats['warning_pct'] > 50 else COLORS['reset']
        critical_color = COLORS['red'] if stats['critical_pct'] > 50 else COLORS['reset']

        print(f"  0-{thresholds['good']}s:         "
              f"{good_color}{stats['good']} proofs ({stats['good_pct']:.1f}%){COLORS['reset']}")
        print(f"  {thresholds['good']}-{thresholds['warning']}s:     "
              f"{warning_color}{stats['warning']} proofs ({stats['warning_pct']:.1f}%){COLORS['reset']}")
        print(f"  >{thresholds['warning']}s:         "
              f"{critical_color}{stats['critical']} proofs ({stats['critical_pct']:.1f}%){COLORS['reset']}")

def main():
    if os.geteuid() != 0:
        print("This script requires sudo privileges")
        sys.exit(1)

    monitor = QuilNodeMonitor()
    monitor.display_stats()

if __name__ == "__main__":
    main()
