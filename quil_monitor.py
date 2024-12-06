#!/usr/bin/env python3

import subprocess
import json
from datetime import datetime, timedelta
import re
import os
import requests
import glob
import sys
import argparse

# Configuration
TELEGRAM_CONFIG = {
    'bot_token': 'YOUR_BOT_TOKEN',    
    'chat_id': 'YOUR_CHAT_ID',        
    'node_name': 'Node-1',            
    'enabled': True,
    'daily_report_hour': 0,           
    'daily_report_minute': 5          
}

# Thresholds (seconds)
THRESHOLDS = {
    'creation': {
        'good': 17,     
        'warning': 50   
    },
    'submission': {
        'good': 28,     
        'warning': 70   
    },
    'cpu': {
        'good': 20,     
        'warning': 30   
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
        self.node_binary = self._get_latest_binary('node')
        self.qclient_binary = self._get_latest_binary('qclient')
        
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
            'daily_balance': {}
        }

    def _save_history(self):
        with open(self.history_file, 'w') as f:
            json.dump(self.history, f, indent=2)

    def _get_latest_binary(self, binary_type):
        pattern = f'./{binary_type}-*-linux-amd64'
        binaries = glob.glob(pattern)
        if not binaries:
            raise Exception(f"No {binary_type} binary found")
            
        latest = max(binaries, key=lambda x: [int(n) for n in re.findall(r'\d+', x)])
        if not os.access(latest, os.X_OK):
            os.chmod(latest, 0o755)
        return latest

    def get_node_info(self):
        result = subprocess.run([self.node_binary, '--node-info'], 
                              capture_output=True, text=True)
        if result.returncode != 0:
            return None
            
        info = {}
        patterns = {
            'ring': r'Prover Ring: (\d+)',
            'active_workers': r'Active Workers: (\d+)',
            'seniority': r'Seniority: (\d+)',
            'owned_balance': r'Owned balance: ([\d.]+) QUIL'
        }
        
        for key, pattern in patterns.items():
            match = re.search(pattern, result.stdout)
            value = float(match.group(1)) if match else 0
            info[key] = int(value) if key != 'owned_balance' else value

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
        """Get today's earnings data"""
        today = datetime.now().strftime('%Y-%m-%d')
        result = subprocess.run(
            [self.qclient_binary, 'token', 'coins', 'metadata', '--public-rpc'],
            capture_output=True, text=True
        )
        
        total_earnings = 0
        for line in result.stdout.splitlines():
            try:
                if 'QUIL' not in line or 'Timestamp' not in line:
                    continue
                    
                amount_match = re.search(r'([\d.]+)\s*QUIL', line)
                timestamp_match = re.search(r'Timestamp\s*([\d-]+T[\d:]+Z)', line)
                
                if amount_match and timestamp_match:
                    timestamp = datetime.strptime(timestamp_match.group(1), 
                                               '%Y-%m-%dT%H:%M:%SZ')
                    if timestamp.strftime('%Y-%m-%d') == today:
                        amount = float(amount_match.group(1))
                        if amount <= 30:  # Only count mining rewards
                            total_earnings += amount
            except:
                continue

        self.history['daily_earnings'][today] = total_earnings
        self._save_history()
        return total_earnings

    def get_earnings_history(self, days=7):
        earnings_data = []
        today = datetime.now().date()
        
        for i in range(days):
            date = (today - timedelta(days=i)).strftime('%Y-%m-%d')
            earnings = self.history.get('daily_earnings', {}).get(date, 0)
            earnings_data.append((date, earnings))
        
        return earnings_data

    def process_logs(self):
        """Process logs using fast text processing"""
        today = datetime.now().strftime('%Y-%m-%d')
        
        # Get creation and submission times
        cmd_create = "journalctl -u ceremonyclient.service --since today --no-hostname | grep 'creating data shard ring proof' | sed -E 's/.*\"frame_age\":([0-9]+\\.[0-9]+).*/\\1/'"
        cmd_submit = "journalctl -u ceremonyclient.service --since today --no-hostname | grep 'submitting data proof' | sed -E 's/.*\"frame_age\":([0-9]+\\.[0-9]+).*/\\1/'"
        cmd_frames = "journalctl -u ceremonyclient.service --since today --no-hostname | grep -E 'creating data shard ring proof|submitting data proof' | sed -E 's/.*\"frame_number\":([0-9]+).*\"frame_age\":([0-9]+\\.[0-9]+).*/\\1 \\2/'"

        # Run commands
        result = subprocess.run(cmd_create, shell=True, capture_output=True, text=True)
        creation_times = [float(t) for t in result.stdout.splitlines() if t]

        result = subprocess.run(cmd_submit, shell=True, capture_output=True, text=True)
        submission_times = [float(t) for t in result.stdout.splitlines() if t]

        result = subprocess.run(cmd_frames, shell=True, capture_output=True, text=True)
        
        # Process CPU times and frame tracking
        creation_data = {}
        cpu_times = []
        frames = set()
        transactions = set()
        
        for line in result.stdout.splitlines():
            try:
                frame, age = line.split()
                frame = int(frame)
                age = float(age)
                
                if frame not in creation_data:
                    creation_data[frame] = age
                    frames.add(frame)
                else:
                    cpu_time = age - creation_data[frame]
                    if cpu_time > 0:
                        cpu_times.append(cpu_time)
                    transactions.add(frame)
            except:
                continue

        # Calculate metrics
        metrics = {
            'creation': self.calculate_stats(creation_times, THRESHOLDS['creation']),
            'submission': self.calculate_stats(submission_times, THRESHOLDS['submission']),
            'cpu': self.calculate_stats(cpu_times, THRESHOLDS['cpu']),
            'landing_rate': {
                'rate': (len(transactions) / len(frames) * 100) if frames else 0,
                'transactions': len(transactions),
                'frames': len(frames)
            }
        }

        return metrics

    def display_stats(self):
        metrics = self.process_logs()
        if not metrics:
            print("No data available")
            return

        node_info = self.get_node_info()
        if not node_info:
            print("Failed to get node info")
            return

        quil_price = self.get_quil_price()
        today_earnings = self.get_coin_data()
        earnings_data = self.get_earnings_history(7)
        
        # Calculate averages
        daily_avg = sum(earning for _, earning in earnings_data) / len(earnings_data) if earnings_data else 0
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
        
        print(f"\nToday's Earnings: {today_earnings:.6f} QUIL // ${today_earnings * quil_price:.2f}")
        
        landing = metrics['landing_rate']
        print(f"\nCurrent Performance:")
        print(f"Landing Rate: {landing['rate']:.2f}% ({landing['transactions']}/{landing['frames']} frames)")

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
        for date, earnings in earnings_data:
            print(f"{date}: {earnings:.6f} QUIL // ${earnings * quil_price:.2f}")

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
