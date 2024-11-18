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
    'bot_token': 'bot_token',    # Get from @BotFather
    'chat_id': 'chat_id',        # Get from @userinfobot
    'node_name': 'Node-1',            # Identifier for this node
    'enabled': True,
    'daily_report_hour': 0,           # Hour to send daily report (0-23)
    'daily_report_minute': 5          # Minute to send report (0-59)
}

# Alert Thresholds
ALERT_THRESHOLDS = {
    'processing_time_warning': 45,    # Daily average seconds
    'processing_time_critical': 60,   # Daily average seconds
    'earnings_deviation': 25         # Percentage below daily average
}

# ANSI Colors
COLORS = {
    'green': '\033[92m',
    'yellow': '\033[93m',
    'red': '\033[91m',
    'reset': '\033[0m',
    'bold': '\033[1m'
}

def check_sudo():
    if os.geteuid() != 0:
        print("This script requires sudo privileges")
        sys.exit(1)

class TelegramNotifier:
    def __init__(self, config: Dict[str, str]):
        self.config = config
        self.base_url = f"https://api.telegram.org/bot{config['bot_token']}"
        self.last_alert_time = {}
        self.last_report_date = None

    def send_message(self, message: str, alert_type: str = 'info') -> None:
        if not self.config['enabled'] or not self.is_valid_config():
            return

        # Check alert cooldown (4 hours for same alert type)
        current_time = datetime.now()
        if alert_type in self.last_alert_time:
            time_diff = current_time - self.last_alert_time[alert_type]
            if time_diff.total_seconds() < 14400:  # 4 hours
                return

        try:
            url = f"{self.base_url}/sendMessage"
            is_alert = alert_type != 'info'
            prefix = "🚨 ALERT: " if is_alert else "ℹ️ Info: "
            if alert_type == 'daily_report':
                prefix = ""  # No prefix for daily reports
            
            data = {
                'chat_id': self.config['chat_id'],
                'text': f"{prefix}{message}",
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
            
            # Prevent duplicate reports
            if self.last_report_date == current_date:
                return
                
            # Calculate earnings comparison
            earn_diff_pct = ((earnings - avg_earnings) / avg_earnings * 100) if avg_earnings > 0 else 0
            comparison = "higher" if earn_diff_pct > 0 else "lower"
            
            # Calculate shard percentages
            total_shards = metrics['total_shards']
            if total_shards > 0:
                fast_pct = (metrics['fast_shards'] / total_shards) * 100
                med_pct = (metrics['medium_shards'] / total_shards) * 100
                slow_pct = (metrics['slow_shards'] / total_shards) * 100
            else:
                fast_pct = med_pct = slow_pct = 0

            message = (
                f"📊 Daily Summary for {self.config['node_name']}\n"
                f"Date: {current_date}\n\n"
                f"💰 Balance: {balance:.6f} QUIL (${balance * quil_price:.2f})\n"
                f"📈 Daily Earnings: {earnings:.6f} QUIL (${earnings * quil_price:.2f})\n"
                f"🔄 {abs(earn_diff_pct):.1f}% {comparison} than average\n\n"
                f"⚙️ Shard Processing:\n"
                f"Total Shards: {total_shards}\n"
                f"0-30s: {fast_pct:.1f}%\n"
                f"30-60s: {med_pct:.1f}%\n"
                f"60s+: {slow_pct:.1f}%\n"
                f"Avg Time: {metrics['avg_frame_age']:.1f}s"
            )
            
            self.send_message(message, alert_type='daily_report')
            self.last_report_date = current_date
            
        except Exception as e:
            print(f"Failed to send daily summary: {e}")

    def is_valid_config(self) -> bool:
        return (self.config['bot_token'] and 
                self.config['bot_token'] != 'YOUR_BOT_TOKEN' and
                self.config['chat_id'])

class CSVExporter:
    def __init__(self, monitor):
        self.monitor = monitor
        self.data_dir = "quil_data"
        Path(self.data_dir).mkdir(exist_ok=True)
        self.daily_file = f"{self.data_dir}/quil_daily.csv"
        self.shards_file = f"{self.data_dir}/quil_shards.csv"

    def export_daily_data(self):
        headers = ['Date', 'Balance', 'Earnings', 'USD Value', 'Total Shards', 
                  'Avg Processing Time', 'Daily Earnings Rate']
        rows = []
        
        dates = sorted(self.monitor.history['daily_balance'].keys())
        quil_price = self.monitor.get_quil_price()
        
        for date in dates:
            balance = self.monitor.history['daily_balance'][date]
            earnings = self.monitor.get_daily_earnings(date)
            usd_value = earnings * quil_price
            
            metrics = self.monitor.history['shard_metrics'].get(date, {})
            total_shards = metrics.get('total_shards', 0)
            avg_time = metrics.get('avg_frame_age', 0)
            
            if date == datetime.now().strftime('%Y-%m-%d'):
                hours_passed = datetime.now().hour + datetime.now().minute / 60
                daily_rate = (earnings / hours_passed * 24) if hours_passed > 0 else 0
            else:
                daily_rate = earnings
                
            rows.append([
                date, balance, earnings, usd_value, total_shards, 
                avg_time, daily_rate
            ])

        with open(self.daily_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)
        print(f"Daily data exported to {self.daily_file}")

    def export_shard_metrics(self):
        headers = ['Date', 'Total Shards', 'Avg Time', 
                  'Fast Shards (0-30s)', 'Medium Shards (30-60s)', 'Slow Shards (60s+)']
        rows = []
        
        dates = sorted(self.monitor.history['shard_metrics'].keys())
        for date in dates[-7:]:  # Last 7 days
            metrics = self.monitor.history['shard_metrics'][date]
            if metrics['total_shards'] > 0:
                rows.append([
                    date,
                    metrics['total_shards'],
                    metrics['avg_frame_age'],
                    metrics['fast_shards'],
                    metrics['medium_shards'],
                    metrics['slow_shards']
                ])

        with open(self.shards_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)
        print(f"Shard metrics exported to {self.shards_file}")

class QuilNodeMonitor:
    def __init__(self, log_file="quil_metrics.json"):
        self.log_file = log_file
        self.history = {'daily_balance': {}, 'shard_metrics': {}}
        self.load_history()
        self.node_binary = self._get_latest_node_binary()
        self.telegram = TelegramNotifier(TELEGRAM_CONFIG)
        self.csv_exporter = CSVExporter(self)

    def load_history(self):
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, 'r') as f:
                    saved_data = json.load(f)
                    if 'daily_balance' in saved_data:
                        self.history['daily_balance'].update(saved_data['daily_balance'])
                    if 'shard_metrics' in saved_data:
                        self.history['shard_metrics'].update(saved_data['shard_metrics'])
            except Exception as e:
                print(f"Error loading history (will start fresh): {e}")

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

    def _save_history(self):
        try:
            with open(self.log_file, 'w') as f:
                json.dump(self.history, f, indent=2)
        except Exception as e:
            print(f"Error saving history: {e}")
            self.telegram.send_message(f"Error saving history: {e}", alert_type='error')

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

            try:
                workers_cmd = 'journalctl -u ceremonyclient.service --since "1 minute ago" --no-hostname -o cat | grep -i shard | tail -n 1'
                workers_result = subprocess.run(workers_cmd, shell=True, capture_output=True, text=True)
                if workers_result.stdout.strip():
                    workers_data = json.loads(workers_result.stdout.strip())
                    active_workers = workers_data.get('active_workers', 1024)
                else:
                    active_workers = 1024
            except:
                active_workers = 1024

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

    def get_shard_metrics(self, date=None):
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        
        try:
            start_time = f"{date} 00:00:00"
            end_time = f"{date} 23:59:59"
            
            cmd = f'journalctl -u ceremonyclient.service --since "{start_time}" --until "{end_time}" --no-hostname -o cat | grep -i shard'
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            frame_ages = []
            fast_shards = 0
            medium_shards = 0
            slow_shards = 0
            
            for line in result.stdout.splitlines():
                try:
                    data = json.loads(line)
                    frame_age = data.get('frame_age', 0)
                    frame_ages.append(frame_age)
                    
                    if frame_age <= 30:
                        fast_shards += 1
                    elif frame_age <= 60:
                        medium_shards += 1
                    else:
                        slow_shards += 1
                except:
                    continue
            
            total_shards = len(frame_ages)
            if total_shards > 0:
                avg_frame_age = sum(frame_ages) / total_shards
                hours_passed = datetime.now().hour + datetime.now().minute / 60
                shards_per_hour = total_shards / (hours_passed if date == datetime.now().strftime('%Y-%m-%d') else 24)
            else:
                avg_frame_age = 0
                shards_per_hour = 0

            metrics = {
                'total_shards': total_shards,
                'shards_per_hour': shards_per_hour,
                'avg_frame_age': avg_frame_age,
                'fast_shards': fast_shards,
                'medium_shards': medium_shards,
                'slow_shards': slow_shards
            }

            self.history['shard_metrics'][date] = metrics
            return metrics
            
        except Exception as e:
            print(f"Error getting shard metrics: {e}")
            return {
                'total_shards': 0,
                'shards_per_hour': 0,
                'avg_frame_age': 0,
                'fast_shards': 0,
                'medium_shards': 0,
                'slow_shards': 0
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
            earnings = current_balance - yesterday_balance
            return earnings
            
        except Exception as e:
            print(f"Error calculating earnings for {date}: {e}")
            return 0

    def calculate_average_earnings(self):
        try:
            dates = sorted(self.history['daily_balance'].keys())
            if len(dates) < 2:
                return 0
            
            total_earnings = 0
            count = 0
            
            for i in range(len(dates)-1):
                current_date = dates[i+1]
                prev_date = dates[i]
                
                if current_date in self.history['daily_balance'] and prev_date in self.history['daily_balance']:
                    current_balance = self.history['daily_balance'][current_date]
                    prev_balance = self.history['daily_balance'][prev_date]
                    daily_earn = current_balance - prev_balance
                    total_earnings += daily_earn
                    count += 1
            
            return total_earnings / count if count > 0 else 0
        except Exception as e:
            print(f"Error calculating average earnings: {e}")
            return 0

    def display_stats(self):
        print("\n=== QUIL Node Statistics ===")
        current_time = datetime.now()
        print(f"Time: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        node_info = self.get_node_info()
        quil_price = self.get_quil_price()
        
        earnings_data = []
        total_weekly = 0
        total_monthly = 0
        
        dates = sorted(self.history['daily_balance'].keys())
        for i in range(len(dates)-1):
            current_date = dates[i+1]
            daily_earn = self.get_daily_earnings(current_date)
            earnings_data.append((current_date, daily_earn))
            
            days_ago = (datetime.strptime(dates[-1], '%Y-%m-%d') - datetime.strptime(current_date, '%Y-%m-%d')).days
            if days_ago < 7:
                total_weekly += daily_earn
            if days_ago < 30:
                total_monthly += daily_earn

        daily_avg = total_weekly / min(7, len(earnings_data)) if earnings_data else 0
        weekly_avg = total_weekly
        monthly_avg = total_monthly
        
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
        print(f"\nShard Processing:")
        print(f"  Total Shards:    {today_metrics['total_shards']}")
        print(f"  Shards/Hour:     {today_metrics['shards_per_hour']:.2f}")
        
        avg_time = today_metrics['avg_frame_age']
        if avg_time > ALERT_THRESHOLDS['processing_time_critical']:
            color = COLORS['red']
        elif avg_time > ALERT_THRESHOLDS['processing_time_warning']:
            color = COLORS['yellow']
        else:
            color = COLORS['green']
        print(f"  Average Time:    {color}{avg_time:.2f}{COLORS['reset']} seconds")
        
        total = today_metrics['total_shards']
        if total > 0:
            fast_pct = (today_metrics['fast_shards'] / total) * 100
            med_pct = (today_metrics['medium_shards'] / total) * 100
            slow_pct = (today_metrics['slow_shards'] / total) * 100
            
            print(f"  0-30 sec:        {COLORS['green']}{today_metrics['fast_shards']} shards ({fast_pct:.1f}%){COLORS['reset']}")
            print(f"  30-60 sec:       {COLORS['yellow']}{today_metrics['medium_shards']} shards ({med_pct:.1f}%){COLORS['reset']}")
            print(f"  60+ sec:         {COLORS['red']}{today_metrics['slow_shards']} shards ({slow_pct:.1f}%){COLORS['reset']}")

        print(f"\nEarnings History:")
        earnings_data.sort(reverse=True)
        for date, earn in earnings_data[:7]:
            metrics = self.history['shard_metrics'].get(date, {'total_shards': 0})
            print(f"{date}: {earn:.6f} QUIL // ${earn * quil_price:.2f} // Shards: {metrics['total_shards']}")
        
        # Check if it's time for daily report
        if (current_time.hour == TELEGRAM_CONFIG['daily_report_hour'] and 
            current_time.minute == TELEGRAM_CONFIG['daily_report_minute']):
            self.telegram.send_daily_summary(
                balance=node_info['total'],
                earnings=today_earnings,
                avg_earnings=daily_avg,
                metrics=today_metrics,
                quil_price=quil_price
            )

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
