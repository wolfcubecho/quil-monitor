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
    },
    'landing_rate': {
        'good': 80,      # >80%
        'warning': 70    # 70-80%, <70% critical
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

class CacheManager:
    def __init__(self, cache_file="quil_cache.json"):
        self.cache_file = cache_file
        self.cache = self._load_cache()
        
    def _load_cache(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r') as f:
                    return json.load(f)
            except:
                return self._init_cache()
        return self._init_cache()
    
    def _init_cache(self):
        return {
            'last_log_timestamp': None,
            'daily_metrics': {},
            'daily_earnings': {},
            'landing_rates': {},
            'last_price_check': None,
            'quil_price': 0,
            'last_report_date': None
        }
    
    def save(self):
        with open(self.cache_file, 'w') as f:
            json.dump(self.cache, f, indent=2)
            
    def get_last_log_time(self):
        return self.cache['last_log_timestamp']
    
    def update_last_log_time(self, timestamp):
        self.cache['last_log_timestamp'] = timestamp
        self.save()

class MetricsCollector:
    def __init__(self, cache_manager):
        self.cache = cache_manager
        self.current_metrics = {
            'creation_times': [],
            'submission_times': [],
            'cpu_times': [],
            'transactions': set(),
            'frames': set()
        }
    
    def _categorize_times(self, times: List[float], thresholds: Dict) -> Dict:
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

    def collect_new_logs(self):
        last_timestamp = self.cache.get_last_log_time()
        since_param = f"--since '{last_timestamp}'" if last_timestamp else ""
        
        cmd = f'journalctl -u ceremonyclient.service {since_param} --no-hostname -o json | grep -E "creating data shard ring proof|submitting data proof"'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        
        creation_data = {}
        latest_timestamp = last_timestamp
        
        for line in result.stdout.splitlines():
            try:
                data = json.loads(line)
                timestamp = data.get('__REALTIME_TIMESTAMP')
                if timestamp:
                    latest_timestamp = max(latest_timestamp, timestamp) if latest_timestamp else timestamp
                
                msg = data.get('MESSAGE', '')
                if "creating data shard ring proof" in msg:
                    msg_data = json.loads(msg)
                    frame_number = msg_data.get('frame_number')
                    frame_age = float(msg_data.get('frame_age', 0))
                    creation_data[frame_number] = {'age': frame_age}
                    self.current_metrics['creation_times'].append(frame_age)
                    self.current_metrics['frames'].add(frame_number)
                    
                elif "submitting data proof" in msg:
                    msg_data = json.loads(msg)
                    frame_number = msg_data.get('frame_number')
                    frame_age = float(msg_data.get('frame_age', 0))
                    if frame_number in creation_data:
                        cpu_time = frame_age - creation_data[frame_number]['age']
                        self.current_metrics['cpu_times'].append(cpu_time)
                    self.current_metrics['submission_times'].append(frame_age)
                    self.current_metrics['transactions'].add(frame_number)
            except:
                continue
        
        if latest_timestamp:
            self.cache.update_last_log_time(latest_timestamp)
        
        return len(self.current_metrics['frames']) > 0

    def get_metrics(self):
        return {
            'creation': self._categorize_times(self.current_metrics['creation_times'], THRESHOLDS['creation']),
            'submission': self._categorize_times(self.current_metrics['submission_times'], THRESHOLDS['submission']),
            'cpu': self._categorize_times(self.current_metrics['cpu_times'], THRESHOLDS['cpu']),
            'landing_rate': self._calculate_landing_rate()
        }
    
    def _calculate_landing_rate(self):
        total_frames = len(self.current_metrics['frames'])
        if total_frames == 0:
            return {'rate': 0, 'transactions': 0, 'frames': 0}
        
        transactions = len(self.current_metrics['transactions'])
        landing_rate = min((transactions / total_frames * 100), 100)
        
        return {
            'rate': landing_rate,
            'transactions': transactions,
            'frames': total_frames
        }

class TelegramNotifier:
    def __init__(self, config: Dict[str, Any], cache_manager: CacheManager):
        self.config = config
        self.cache = cache_manager
        self.base_url = f"https://api.telegram.org/bot{config['bot_token']}"
        self.last_alert_time = {}

    def send_message(self, message: str, alert_type: str = 'info') -> None:
        if not self.config['enabled'] or not self._is_valid_config():
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

    def _is_valid_config(self) -> bool:
        return (self.config['bot_token'] and 
                self.config['bot_token'] != 'YOUR_BOT_TOKEN' and
                self.config['chat_id'])

    def check_daily_report_time(self) -> bool:
        current_time = datetime.now()
        last_report_date = self.cache.cache.get('last_report_date')
        
        if (not last_report_date or 
            current_time.strftime('%Y-%m-%d') > last_report_date and 
            current_time.hour == self.config['daily_report_hour'] and 
            current_time.minute >= self.config['daily_report_minute']):
            
            self.cache.cache['last_report_date'] = current_time.strftime('%Y-%m-%d')
            self.cache.save()
            return True
        
        return False

    def send_daily_summary(self, node_info: Dict, metrics: Dict, quil_price: float,
                         daily_earnings: float, avg_earnings: float):
        if not self.config['enabled'] or not self._is_valid_config():
            return

        try:
            current_date = datetime.now().strftime('%Y-%m-%d')
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
            
        except Exception as e:
            print(f"Failed to send daily summary: {e}")

class QuilNodeMonitor:
    def __init__(self):
        self.cache_manager = CacheManager()
        self.metrics_collector = MetricsCollector(self.cache_manager)
        self.telegram = TelegramNotifier(TELEGRAM_CONFIG, self.cache_manager)
        self.node_binary = self._get_latest_binary('node')
        self.qclient_binary = self._get_latest_binary('qclient')
    
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
            info[key] = float(match.group(1)) if match else 0
            
        return info

    def get_quil_price(self):
        cache = self.cache_manager.cache
        now = datetime.now()
        
        if cache['last_price_check']:
            last_check = datetime.fromtimestamp(cache['last_price_check'])
            if (now - last_check).total_seconds() < 300:  # 5 minutes
                return cache['quil_price']
        
        try:
            response = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "wrapped-quil", "vs_currencies": "usd"}
            )
            price = response.json().get("wrapped-quil", {}).get("usd", 0)
            
            cache['quil_price'] = price
            cache['last_price_check'] = now.timestamp()
            self.cache_manager.save()
            
            return price
        except:
            return cache['quil_price']

    def get_earnings_history(self, days=7):
        cache = self.cache_manager.cache
        today = datetime.now().date()
        earnings_data = []
        
        for i in range(days):
            date = (today - timedelta(days=i)).strftime('%Y-%m-%d')
            earnings = cache.get('daily_earnings', {}).get(date, 0)
            earnings_data.append((date, earnings))
        
        return earnings_data

    def update_metrics(self):
        return self.metrics_collector.collect_new_logs()

    def display_processing_section(self, title: str, stats: Dict, thresholds: Dict):
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
            daily_fields = ['date', 'earnings', 'landing_rate', 'frames', 'transactions']
            with open('daily_metrics.csv', 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=daily_fields)
                writer.writeheader()
                
                for date, data in self.cache_manager.cache['daily_metrics'].items():
                    row = {
                        'date': date,
                        'earnings': data.get('earnings', 0),
                        'landing_rate': data.get('landing_rate', {}).get('rate', 0),
                        'frames': data.get('landing_rate', {}).get('frames', 0),
                        'transactions': data.get('landing_rate', {}).get('transactions', 0)
                    }
                    writer.writerow(row)
            
            print("Data exported to daily_metrics.csv")
            
        except Exception as e:
            print(f"Error exporting CSV: {e}")

    def display_stats(self):
        if not self.update_metrics():
            print("No new data since last update")
            return

        node_info = self.get_node_info()
        if not node_info:
            print("Failed to get node info")
            return

        metrics = self.metrics_collector.get_metrics()
        quil_price = self.get_quil_price()
        
        # Get earnings data
        earnings_data = self.get_earnings_history(7)
        daily_avg = sum(earning for _, earning in earnings_data) / len(earnings_data) if earnings_data else 0
        today_earnings = earnings_data[0][1] if earnings_data else 0

        print("\n=== QUIL Node Statistics ===")
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        print(f"\nNode Information:")
        print(f"Ring: {int(node_info['ring'])}")
        print(f"Active Workers: {int(node_info['active_workers'])}")
        print(f"Seniority: {int(node_info['seniority'])}")
        print(f"QUIL Price: ${quil_price:.4f}")
        print(f"Balance: {node_info['owned_balance']:.6f} QUIL (${node_info['owned_balance'] * quil_price:.2f})")
        
        # Earnings section
        print(f"\nEarnings:")
        print(f"Daily Average: {daily_avg:.6f} QUIL (${daily_avg * quil_price:.2f})")
        print(f"Today's Earnings: {today_earnings:.6f} QUIL (${today_earnings * quil_price:.2f})")
        
        # Performance metrics
        print(f"\nCurrent Performance:")
        print(f"Landing Rate: {metrics['landing_rate']['rate']:.2f}% "
              f"({metrics['landing_rate']['transactions']}/{metrics['landing_rate']['frames']} frames)")
        
        self.display_processing_section("Creation Stage (Network Latency)", 
                                     metrics['creation'], 
                                     THRESHOLDS['creation'])
        self.display_processing_section("Submission Stage (Total Time)", 
                                     metrics['submission'], 
                                     THRESHOLDS['submission'])
        self.display_processing_section("CPU Processing Time", 
                                     metrics['cpu'], 
                                     THRESHOLDS['cpu'])
        
        # History section
        print("\nHistory (Last 7 Days):")
        for date, earnings in earnings_data:
            print(f"{date}: {earnings:.6f} QUIL (${earnings * quil_price:.2f})")

        # Check and send daily report if needed
        if self.telegram.check_daily_report_time():
            self.telegram.send_daily_summary(
                node_info=node_info,
                metrics=metrics,
                quil_price=quil_price,
                daily_earnings=today_earnings,
                avg_earnings=daily_avg
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
    
    with open("telegram_config.json", 'w') as f:
        json.dump(config, f, indent=2)
    
    print("\nConfiguration saved to telegram_config.json")
    print("Add these values to the TELEGRAM_CONFIG in the script")
    
    # Test the configuration
    notifier = TelegramNotifier(config, CacheManager())
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
