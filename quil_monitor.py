import subprocess
import json
from datetime import datetime, timedelta
import re
import os
import requests
import glob
import sys
import argparse
import csv
import platform
from typing import Dict, List, Tuple, Any
from pathlib import Path

# Configuration
TELEGRAM_CONFIG = {
    'bot_token': 'YOUR_BOT_TOKEN',    # Get from @BotFather
    'chat_id': 'YOUR_CHAT_ID',        # Get from @userinfobot
    'node_name': 'Node-1',            # Identifier for this node
    'enabled': True,
    'daily_report_hour': 0,           # Hour to send daily report (0-23)
    'daily_report_minute': 5          # Minute to send report (0-59)
}

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

def check_sudo():
    if os.geteuid() != 0:
        print("This script requires sudo privileges")
        sys.exit(1)

class ProcessingMetrics:
    def __init__(self):
        self.creation_times = []
        self.submission_times = []
        self.cpu_times = []
        
    def add_creation(self, time):
        self.creation_times.append(float(time))
        
    def add_submission(self, time):
        self.submission_times.append(float(time))
        
    def add_cpu_time(self, time):
        if time > 0:  # Only add positive CPU times
            self.cpu_times.append(float(time))
        
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
        
    def get_stats(self):
        return {
            'creation': self.calculate_stats(self.creation_times, THRESHOLDS['creation']),
            'submission': self.calculate_stats(self.submission_times, THRESHOLDS['submission']),
            'cpu': self.calculate_stats(self.cpu_times, THRESHOLDS['cpu'])
        }

class TelegramNotifier:
    def __init__(self, config: Dict[str, str]):
        self.config = config
        self.base_url = f"https://api.telegram.org/bot{config['bot_token']}"
        self.last_alert_time = {}
        self.last_report_date = None

    def send_message(self, message: str, alert_type: str = 'info') -> None:
        if not self.config['enabled'] or not self.is_valid_config():
            return

        current_time = datetime.now()
        if alert_type in self.last_alert_time:
            time_diff = current_time - self.last_alert_time[alert_type]
            if time_diff.total_seconds() < 14400:  # 4 hours cooldown
                return

        try:
            url = f"{self.base_url}/sendMessage"
            prefix = "🚨 ALERT: " if alert_type != 'info' else ""
            if alert_type == 'daily_report':
                prefix = ""
            
            data = {
                'chat_id': self.config['chat_id'],
                'text': f"{prefix}{message}\n\nTime: {current_time.strftime('%Y-%m-%d %H:%M:%S')}",
                'parse_mode': 'HTML'
            }
            response = requests.post(url, data=data)
            response.raise_for_status()
            self.last_alert_time[alert_type] = current_time
        except Exception as e:
            print(f"Failed to send Telegram message: {e}")

    def send_daily_summary(self, balance: float, earnings: float, avg_earnings: float, 
                         metrics: dict, quil_price: float):
        if not self.config['enabled'] or not self.is_valid_config():
            return

        try:
            current_time = datetime.now()
            current_date = current_time.strftime('%Y-%m-%d')
            
            if self.last_report_date == current_date:
                return
                
            earn_diff_pct = ((earnings - avg_earnings) / avg_earnings * 100) if avg_earnings > 0 else 0
            comparison = "higher" if earn_diff_pct > 0 else "lower"

            message = (
                f"📊 Daily Summary for {self.config['node_name']}\n"
                f"Date: {current_date}\n\n"
                f"💰 Balance: {balance:.6f} QUIL (${balance * quil_price:.2f})\n"
                f"📈 Daily Earnings: {earnings:.6f} QUIL (${earnings * quil_price:.2f})\n"
                f"🔄 {abs(earn_diff_pct):.1f}% {comparison} than average\n\n"
                f"⚡ Processing Performance:\n"
                f"Creation: {metrics['creation']['avg_time']:.2f}s avg ({metrics['creation']['total']} proofs)\n"
                f"Submission: {metrics['submission']['avg_time']:.2f}s avg\n"
                f"CPU Time: {metrics['cpu']['avg_time']:.2f}s avg"
            )
            
            self.send_message(message, alert_type='daily_report')
            self.last_report_date = current_date
            
        except Exception as e:
            print(f"Failed to send daily summary: {e}")

    def is_valid_config(self) -> bool:
        return (self.config['bot_token'] and 
                self.config['bot_token'] != 'YOUR_BOT_TOKEN' and
                self.config['chat_id'])

class QuilNodeMonitor:
    def __init__(self, log_file="quil_metrics.json"):
        self.log_file = log_file
        self.history = {'daily_balance': {}, 'processing_metrics': {}}
        self.load_history()
        self.node_binary = self._get_latest_node_binary()
        self.telegram = TelegramNotifier(TELEGRAM_CONFIG)
        self.last_report_check = datetime.now().replace(hour=0, minute=0, second=0)

    def _get_latest_node_binary(self):
        try:
            node_binaries = glob.glob('./node-*-linux-amd64')
            if not node_binaries:
                raise Exception("No node binary found")
            
            def get_version_tuple(binary):
                version_match = re.search(r'node-(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?-linux-amd64', binary)
                if version_match:
                    parts = list(version_match.groups())
                    parts[3] = parts[3] if parts[3] is not None else '0'
                    return tuple(int(x) for x in parts)
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

    def load_history(self):
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, 'r') as f:
                    saved_data = json.load(f)
                    if 'daily_balance' in saved_data:
                        self.history['daily_balance'].update(saved_data['daily_balance'])
                    if 'processing_metrics' in saved_data:
                        self.history['processing_metrics'].update(saved_data['processing_metrics'])
            except Exception as e:
                print(f"Error loading history (will start fresh): {e}")

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
                return None

            ring_match = re.search(r'Prover Ring: (\d+)', result.stdout)
            ring = int(ring_match.group(1)) if ring_match else 0

            workers_cmd = 'journalctl -u ceremonyclient.service --since "1 minute ago" --until "now" --no-hostname -o cat | grep -i "active_workers" | tail -n 1'
            try:
                workers_result = subprocess.run(workers_cmd, shell=True, capture_output=True, text=True)
                if workers_result.stdout.strip():
                    data = json.loads(workers_result.stdout.strip())
                    active_workers = data.get('active_workers', 0)
                else:
                    active_workers = 0
            except:
                active_workers = 0

            owned_balance_match = re.search(r'Owned balance: ([\d.]+) QUIL', result.stdout)
            owned_balance = float(owned_balance_match.group(1)) if owned_balance_match else 0

            date = datetime.now().strftime('%Y-%m-%d')
            self.history['daily_balance'][date] = owned_balance
            self._save_history()

            return {
                'ring': ring,
                'active_workers': active_workers,
                'owned': owned_balance,
                'total': owned_balance
            }
        except Exception as e:
            print(f"Error getting node info: {e}")
            return None

    def get_processing_metrics(self, date=None):
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        
        metrics = ProcessingMetrics()
        start_time = f"{date} 00:00:00"
        end_time = f"{date} 23:59:59"

        try:
            # Get creation times first and store them
            cmd = f'journalctl -u ceremonyclient.service --since "{start_time}" --until "{end_time}" --no-hostname -o cat | grep -i "creating data shard ring proof"'
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            creation_data = {}
            for line in result.stdout.splitlines():
                try:
                    data = json.loads(line)
                    frame_number = data.get('frame_number')
                    frame_age = float(data.get('frame_age', 0))
                    creation_data[frame_number] = {'age': frame_age, 'timestamp': float(data.get('ts', 0))}
                    metrics.add_creation(frame_age)
                except:
                    continue

            # Get submission times and calculate CPU times
            cmd = f'journalctl -u ceremonyclient.service --since "{start_time}" --until "{end_time}" --no-hostname -o cat | grep -i "submitting data proof"'
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            for line in result.stdout.splitlines():
                try:
                    data = json.loads(line)
                    frame_number = data.get('frame_number')
                    frame_age = float(data.get('frame_age', 0))
                    
                    if frame_number in creation_data:
                        creation_age = creation_data[frame_number]['age']
                        cpu_time = frame_age - creation_age
                        metrics.add_cpu_time(cpu_time)
                    
                    metrics.add_submission(frame_age)
                except:
                    continue

            stats = metrics.get_stats()
            self.history['processing_metrics'][date] = stats
            self._save_history()
            return stats
            
        except Exception as e:
            print(f"Error getting processing metrics: {e}")
            return {
                'creation': {'total': 0, 'avg_time': 0},
                'submission': {'total': 0, 'avg_time': 0},
                'cpu': {'total': 0, 'avg_time': 0}
            }

def get_daily_earnings(self, date):
        try:
            yesterday = (datetime.strptime(date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
            
            if yesterday not in self.history['daily_balance']:
                return 0
                
            if date not in self.history['daily_balance']:
                if date == datetime.now().strftime('%Y-%m-%d'):
                    node_info = self.get_node_info()
                    if node_info is None:
                        return 0
                    current_balance = node_info['owned']
                else:
                    return 0
            else:
                current_balance = self.history['daily_balance'][date]
            
            yesterday_balance = self.history['daily_balance'][yesterday]
            earnings = current_balance - yesterday_balance
            return earnings
            
        except Exception as e:
            print(f"Error calculating earnings for {date}: {e}")
            return 0

    def check_daily_report_time(self):
        current_time = datetime.now()
        last_run_file = "last_report.txt"
        
        try:
            if os.path.exists(last_run_file):
                with open(last_run_file, 'r') as f:
                    last_run = datetime.strptime(f.read().strip(), '%Y-%m-%d')
            else:
                last_run = current_time - timedelta(days=1)

            # Check if it's a new day and within configured time
            if (current_time.date() > last_run.date() and 
                current_time.hour == TELEGRAM_CONFIG['daily_report_hour'] and 
                current_time.minute >= TELEGRAM_CONFIG['daily_report_minute']):
                
                with open(last_run_file, 'w') as f:
                    f.write(current_time.strftime('%Y-%m-%d'))
                return True
                
        except Exception as e:
            print(f"Error checking daily report time: {e}")
        
        return False

    def get_earnings_history(self, days=7):
        earnings_data = []
        today = datetime.now().date()
        
        for i in range(days):
            date = (today - timedelta(days=i)).strftime('%Y-%m-%d')
            earnings = self.get_daily_earnings(date)
            earnings_data.append((date, earnings))
            
        return earnings_data

def display_stats(self):
        print("\n=== QUIL Node Statistics ===")
        current_time = datetime.now()
        print(f"Time: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        node_info = self.get_node_info()
        quil_price = self.get_quil_price()
        
        # Calculate earnings data and averages
        today = current_time.strftime('%Y-%m-%d')
        today_earnings = self.get_daily_earnings(today)
        today_metrics = self.get_processing_metrics(today)
        
        if node_info:
            earnings_data = self.get_earnings_history(7)  # Get last 7 days
            daily_avg = sum(earn for _, earn in earnings_data) / len(earnings_data) if earnings_data else 0
            weekly_avg = daily_avg * 7
            monthly_avg = daily_avg * 30

            print(f"\nNode Information:")
            print(f"Ring:            {node_info['ring']}")
            print(f"Active Workers:  {node_info['active_workers']}")
            print(f"QUIL Price:      ${quil_price:.4f}")
            print(f"QUIL on Node:    {node_info['total']:.6f}")
            print(f"Daily Average:   {daily_avg:.6f} QUIL // ${daily_avg * quil_price:.2f}")
            print(f"Weekly Average:  {weekly_avg:.6f} QUIL // ${weekly_avg * quil_price:.2f}")
            print(f"Monthly Average: {monthly_avg:.6f} QUIL // ${monthly_avg * quil_price:.2f}")

        # Today's Stats and Processing Analysis
        print(f"\nToday's Stats ({today}):")
        print(f"Earnings:        {today_earnings:.6f} QUIL // ${today_earnings * quil_price:.2f}")
        
        print("\nProcessing Analysis:")
        self.display_processing_section("Creation Stage (Network Latency)", 
                                     today_metrics['creation'], 
                                     THRESHOLDS['creation'])
        self.display_processing_section("Submission Stage (Total Time)", 
                                     today_metrics['submission'], 
                                     THRESHOLDS['submission'])
        self.display_processing_section("CPU Processing Time", 
                                     today_metrics['cpu'], 
                                     THRESHOLDS['cpu'])

        # Earnings History
        print("\nEarnings History:")
        for date, earnings in self.get_earnings_history(7):
            metrics = self.history['processing_metrics'].get(date, {})
            cpu_info = metrics.get('cpu', {})
            total_proofs = metrics.get('creation', {}).get('total', 0)
            avg_cpu = cpu_info.get('avg_time', 0)
            print(f"{date}: {earnings:.6f} QUIL // ${earnings * quil_price:.2f} "
                  f"(Avg Process: {avg_cpu:.2f}s, {total_proofs} proofs)")

        # Check for daily report
        if self.check_daily_report_time():
            self.telegram.send_daily_summary(
                balance=node_info['total'],
                earnings=today_earnings,
                avg_earnings=daily_avg,
                metrics=today_metrics,
                quil_price=quil_price
            )

    def display_processing_section(self, title, stats, thresholds):
        print(f"\n{title}:")
        print(f"  Total Proofs:    {stats['total']}")
        print(f"  Average Time:    {stats['avg_time']:.2f}s")
        
        # Display categories with color coding
        color = COLORS['green'] if stats['good_pct'] > 50 else COLORS['reset']
        print(f"  0-{thresholds['good']}s:         "
              f"{color}{stats['good']} proofs ({stats['good_pct']:.1f}%){COLORS['reset']}")
        
        color = COLORS['yellow'] if stats['warning_pct'] > 50 else COLORS['reset']
        print(f"  {thresholds['good']}-{thresholds['warning']}s:     "
              f"{color}{stats['warning']} proofs ({stats['warning_pct']:.1f}%){COLORS['reset']}")
        
        color = COLORS['red'] if stats['critical_pct'] > 50 else COLORS['reset']
        print(f"  >{thresholds['warning']}s:         "
              f"{color}{stats['critical']} proofs ({stats['critical_pct']:.1f}%){COLORS['reset']}")

def setup_telegram():
    print("\nTelegram Bot Setup:")
    print("1. Message @BotFather on Telegram to create a new bot and get the token")
    print("2. Message @userinfobot to get your chat ID")
    
    token = input("\nEnter your bot token: ").strip()
    chat_id = input("Enter your chat ID: ").strip()
    node_name = input("Enter node identifier (e.g., Node-1): ").strip()
    
    config = {
        'bot_token': token,
        'chat_id': chat_id,
        'node_name': node_name,
        'enabled': True,
        'daily_report_hour': 0,
        'daily_report_minute': 5
    }
    
    notifier = TelegramNotifier(config)
    notifier.send_message("Test message from QUIL Monitor")
    
    config_file = "telegram_config.json"
    with open(config_file, 'w') as f:
        json.dump(config, f, indent=2)
    
    print(f"\nConfiguration saved to {config_file}")
    print("Add these values to the TELEGRAM_CONFIG in the script")

def main():
    parser = argparse.ArgumentParser(description='QUIL Node Monitor')
    parser.add_argument('--export-csv', action='store_true', help='Export data to CSV')
    parser.add_argument('--setup-telegram', action='store_true', help='Setup Telegram notifications')
    args = parser.parse_args()

    if args.setup_telegram:
        setup_telegram()
        return

    check_sudo()
    monitor = QuilNodeMonitor()
    
    if args.export_csv:
        monitor.csv_exporter.export_daily_data()
        monitor.csv_exporter.export_shard_metrics()
        print("Data exported to CSV files")
        return

    monitor.display_stats()

if __name__ == "__main__":
    main()
