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
from typing import Dict, List, Tuple, Any
from pathlib import Path

# Configuration
TELEGRAM_CONFIG = {
    'bot_token': 'YOUR_BOT_TOKEN',    
    'chat_id': 'YOUR_CHAT_ID',        
    'node_name': 'Node-1',            
    'enabled': True,
    'daily_report_hour': 0,           
    'daily_report_minute': 5          
}

# Processing Time Thresholds (seconds)
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
    },
    'landing_rate': {
        'good': 80,      
        'warning': 70    
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

class ProcessingMetrics:
    def __init__(self):
        self.creation_times = []
        self.submission_times = []
        self.cpu_times = []
        self.transactions = set()
        self.frames = set()
        
    def add_metrics(self, frame_number, creation_time=None, submission_time=None):
        if creation_time is not None:
            self.creation_times.append(creation_time)
            self.frames.add(frame_number)
        if submission_time is not None:
            self.submission_times.append(submission_time)
            self.transactions.add(frame_number)
            
    def calculate_cpu_time(self, frame_number, submission_age, creation_age):
        cpu_time = submission_age - creation_age
        if cpu_time > 0:
            self.cpu_times.append(cpu_time)
        
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
            'cpu': self.calculate_stats(self.cpu_times, THRESHOLDS['cpu']),
            'landing_rate': self.calculate_landing_rate()
        }
    
    def calculate_landing_rate(self):
        total_frames = len(self.frames)
        if total_frames == 0:
            return {'rate': 0, 'transactions': 0, 'frames': 0}
            
        transactions = len(self.transactions)
        return {
            'rate': (transactions / total_frames * 100) if total_frames > 0 else 0,
            'transactions': transactions,
            'frames': total_frames
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

    def send_daily_summary(self, node_info: Dict, metrics: Dict, quil_price: float,
                         daily_earnings: float, avg_earnings: float):
        if not self.config['enabled'] or not self.is_valid_config():
            return

        try:
            current_date = datetime.now().strftime('%Y-%m-%d')
            
            if self.last_report_date == current_date:
                return
                
            earn_diff_pct = ((daily_earnings - avg_earnings) / avg_earnings * 100) if avg_earnings > 0 else 0
            comparison = "higher" if earn_diff_pct > 0 else "lower"

            message = (
                f"📊 Daily Summary for {self.config['node_name']}\n"
                f"Date: {current_date}\n\n"
                f"💰 Balance: {node_info['owned_balance']:.6f} QUIL "
                f"(${node_info['owned_balance'] * quil_price:.2f})\n"
                f"📈 Daily Earnings: {daily_earnings:.6f} QUIL "
                f"(${daily_earnings * quil_price:.2f})\n"
                f"🔄 {abs(earn_diff_pct):.1f}% {comparison} than average\n"
                f"🎯 Landing Rate: {metrics['landing_rate']['rate']:.2f}% "
                f"({metrics['landing_rate']['transactions']}/{metrics['landing_rate']['frames']} frames)\n\n"
                f"⚡ Processing Performance:\n"
                f"Creation: {metrics['creation']['avg_time']:.2f}s avg\n"
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
    def __init__(self):
        self.history_file = "quil_history.json"
        self.history = self._load_history()
        self.metrics = ProcessingMetrics()
        self.node_binary = self._get_latest_node_binary()
        self.qclient_binary = self._get_latest_qclient_binary()
        self.telegram = TelegramNotifier(TELEGRAM_CONFIG)
        self._last_log_check = None

    def _get_latest_node_binary(self):
        try:
            node_binaries = glob.glob('./node-*-linux-amd64')
            if not node_binaries:
                raise Exception("No node binary found")
            
            latest = max(node_binaries, 
                        key=lambda x: [int(n) for n in re.findall(r'\d+', x)])
            
            if not os.access(latest, os.X_OK):
                os.chmod(latest, 0o755)
            return latest
        except Exception as e:
            print(f"Error finding node binary: {e}")
            sys.exit(1)

    def _get_latest_qclient_binary(self):
        try:
            qclient_binaries = glob.glob('./qclient-*-linux-amd64')
            if not qclient_binaries:
                raise Exception("No qclient binary found")
            
            latest = max(qclient_binaries, 
                        key=lambda x: [int(n) for n in re.findall(r'\d+', x)])
            
            if not os.access(latest, os.X_OK):
                os.chmod(latest, 0o755)
            return latest
        except Exception as e:
            print(f"Error finding qclient binary: {e}")
            sys.exit(1)

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
            'daily_balance': {},
            'daily_earnings': {},
            'last_log_timestamp': None
        }

    def _save_history(self):
        with open(self.history_file, 'w') as f:
            json.dump(self.history, f, indent=2)

    def get_quil_price(self):
        try:
            response = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "wrapped-quil", "vs_currencies": "usd"}
            )
            return response.json().get("wrapped-quil", {}).get("usd", 0)
        except:
            return 0

    def get_node_info(self):
        try:
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

            today = datetime.now().strftime('%Y-%m-%d')
            self.history['daily_balance'][today] = info['owned_balance']
            return info
        except Exception as e:
            print(f"Error getting node info: {e}")
            return None

    def get_coin_data(self):
        """Get today's earnings data"""
        today = datetime.now().strftime('%Y-%m-%d')
        start_time = f"{today} 00:00:00"
        
        result = subprocess.run(
            [self.qclient_binary, 'token', 'coins', 'metadata', '--public-rpc'],
            capture_output=True, text=True
        )
        
        if result.returncode != 0:
            return 0

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
        return total_earnings

    def process_logs(self):
    """Process today's logs with correct timestamp format"""
    today = datetime.now().strftime('%Y-%m-%d')
    cmd = f'journalctl -u ceremonyclient.service --since "{today} 00:00:00" -o json | grep -E "creating data shard ring proof|submitting data proof"'
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    
    creation_data = {}
    
    for line in result.stdout.splitlines():
        try:
            entry = json.loads(line)
            msg = entry.get('MESSAGE', '')
            if not msg:
                continue
                
            msg_data = json.loads(msg)
            frame_number = msg_data.get('frame_number')
            frame_age = float(msg_data.get('frame_age', 0))
            
            if "creating data shard ring proof" in msg:
                creation_data[frame_number] = frame_age
                self.metrics.add_creation(frame_age)
            elif "submitting data proof" in msg:
                if frame_number in creation_data:
                    cpu_time = frame_age - creation_data[frame_number]
                    if cpu_time > 0:
                        self.metrics.add_cpu_time(cpu_time)
                self.metrics.add_submission(frame_age)
        except:
            continue

    def get_earnings_history(self, days=7):
        earnings_data = []
        today = datetime.now().date()
        
        for i in range(days):
            date = (today - timedelta(days=i)).strftime('%Y-%m-%d')
            earnings = self.history.get('daily_earnings', {}).get(date, 0)
            earnings_data.append((date, earnings))
        
        return earnings_data

    def check_daily_report_time(self):
        current_time = datetime.now()
        if (current_time.hour == TELEGRAM_CONFIG['daily_report_hour'] and 
            current_time.minute >= TELEGRAM_CONFIG['daily_report_minute']):
            today = current_time.strftime('%Y-%m-%d')
            if self.telegram.last_report_date != today:
                return True
        return False

    def display_processing_section(self, title, stats, thresholds):
        print(f"\n{title}:")
        print(f"  Total Proofs:    {stats['total']}")
        print(f"  Average Time:    {stats['avg_time']:.2f}s")
        
        color = COLORS['green'] if stats['good_pct'] > 50 else COLORS['reset']
        print(f"  0-{thresholds['good']}s:         "
              f"{color}{stats['good']} proofs ({stats['good_pct']:.1f}%){COLORS['reset']}")
        
        color = COLORS['yellow'] if stats['warning_pct'] > 50 else COLORS['reset']
        print(f"  {thresholds['good']}-{thresholds['warning']}s:     "
              f"{color}{stats['warning']} proofs ({stats['warning_pct']:.1f}%){COLORS['reset']}")
        
        color = COLORS['red'] if stats['critical_pct'] > 50 else COLORS['reset']
        print(f"  >{thresholds['warning']}s:         "
              f"{color}{stats['critical']} proofs ({stats['critical_pct']:.1f}%){COLORS['reset']}")

    def export_csv(self):
        try:
            # Export daily data
            fields = ['date', 'earnings', 'balance', 'landing_rate', 'frames', 'transactions']
            with open('quil_metrics.csv', 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                
                for date, metrics in self.history.items():
                    if isinstance(metrics, dict):
                        row = {
                            'date': date,
                            'earnings': metrics.get('earnings', 0),
                            'balance': metrics.get('balance', 0),
                            'landing_rate': metrics.get('landing_rate', {}).get('rate', 0),
                            'frames': metrics.get('frames', 0),
                            'transactions': metrics.get('transactions', 0)
                        }
                        writer.writerow(row)
            
            print("Data exported to quil_metrics.csv")
            
        except Exception as e:
            print(f"Error exporting CSV: {e}")

    def display_stats(self):
        node_info = self.get_node_info()
        if not node_info:
            print("Failed to get node info")
            return
    
        # Get core data
        self.process_logs()
        metrics = self.metrics.get_stats()
        quil_price = self.get_quil_price()
        today_earnings = self.get_coin_data()
        earnings_data = self.get_earnings_history(7)
        
        # Calculate averages properly
        daily_avg = sum(earning for _, earning in earnings_data) / len(earnings_data) if earnings_data else 0
        weekly_avg = daily_avg * 7
        monthly_avg = daily_avg * 30
    
        print("\n=== QUIL Node Statistics ===")
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        print(f"\nNode Information:")
        print(f"Ring: {int(node_info['ring'])}")
        print(f"Active Workers: {int(node_info['active_workers'])}")
        print(f"Seniority: {int(node_info['seniority'])}")
        print(f"QUIL Price: ${quil_price:.4f}")
        print(f"Balance: {node_info['owned_balance']:.6f} QUIL (${node_info['owned_balance'] * quil_price:.2f})")
        
        print(f"\nEarnings Averages:")
        print(f"Daily Average:   {daily_avg:.6f} QUIL // ${daily_avg * quil_price:.2f}")
        print(f"Weekly Average:  {weekly_avg:.6f} QUIL // ${weekly_avg * quil_price:.2f}")
        print(f"Monthly Average: {monthly_avg:.6f} QUIL // ${monthly_avg * quil_price:.2f}")
        
        print(f"\nToday's Earnings: {today_earnings:.6f} QUIL // ${today_earnings * quil_price:.2f}")
        
        print(f"\nCurrent Performance:")
        landing_rate = metrics['landing_rate']
        print(f"Landing Rate: {landing_rate['rate']:.2f}% ({landing_rate['transactions']}/{landing_rate['frames']} frames)")
    
        self.display_processing_section("Creation Stage (Network Latency)", 
                                     metrics['creation'], 
                                     THRESHOLDS['creation'])
        self.display_processing_section("Submission Stage (Total Time)", 
                                     metrics['submission'], 
                                     THRESHOLDS['submission'])
        self.display_processing_section("CPU Processing Time", 
                                     metrics['cpu'], 
                                     THRESHOLDS['cpu'])
    
        print("\nHistory (Last 7 Days):")
        for date, earnings in earnings_data:
            rate_data = self.history.get('landing_rates', {}).get(date, {})
            print(f"{date}: {earnings:.6f} QUIL // ${earnings * quil_price:.2f} "
                  f"(Landing Rate: {rate_data.get('rate', 0):.2f}%)")

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
    
    with open("telegram_config.json", 'w') as f:
        json.dump(config, f, indent=2)
    
    print("\nConfiguration saved to telegram_config.json")
    print("Add these values to the TELEGRAM_CONFIG in the script")
    
    notifier = TelegramNotifier(config)
    notifier.send_message("Test message from QUIL Monitor")

def main():
    parser = argparse.ArgumentParser(description='QUIL Node Monitor')
    parser.add_argument('--export-csv', action='store_true', help='Export data to CSV')
    parser.add_argument('--setup-telegram', action='store_true', help='Setup Telegram notifications')
    args = parser.parse_args()

    if args.setup_telegram:
        setup_telegram()
        return

    if os.geteuid() != 0:
        print("This script requires sudo privileges")
        sys.exit(1)

    monitor = QuilNodeMonitor()
    
    if args.export_csv:
        monitor.export_csv()
        return

    monitor.display_stats()

if __name__ == "__main__":
    main()
