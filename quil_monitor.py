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
                         metrics: dict, quil_price: float, landing_rate: dict):
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
                f"🔄 {abs(earn_diff_pct):.1f}% {comparison} than average\n"
                f"🎯 Landing Rate: {landing_rate['rate']:.2f}% "
                f"({landing_rate['transactions']}/{landing_rate['frames']} frames)\n\n"
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

    def _get_latest_qclient_binary(self):
        try:
            qclient_binaries = glob.glob('./qclient-*-linux-amd64')
            if not qclient_binaries:
                raise Exception("No qclient binary found")
            
            def get_version_tuple(binary):
                version_match = re.search(r'qclient-(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?-linux-amd64', binary)
                if version_match:
                    parts = list(version_match.groups())
                    parts[3] = parts[3] if parts[3] is not None else '0'
                    return tuple(int(x) for x in parts)
                return (0, 0, 0, 0)

            qclient_binaries.sort(key=get_version_tuple, reverse=True)
            latest_binary = qclient_binaries[0]
            
            if not os.path.exists(latest_binary):
                raise Exception(f"Binary {latest_binary} not found")
            if not os.access(latest_binary, os.X_OK):
                os.chmod(latest_binary, 0o755)
            
            print(f"Using qclient binary: {latest_binary}")
            return latest_binary
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
            'daily_metrics': {},
            'daily_earnings': {},
            'landing_rates': {}
        }
        
    def _save_history(self):
        with open(self.history_file, 'w') as f:
            json.dump(self.history, f, indent=2)

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

        return {
            'creation': self.calculate_stats(creation_times, THRESHOLDS['creation']),
            'submission': self.calculate_stats(submission_times, THRESHOLDS['submission']),
            'cpu': self.calculate_stats(cpu_times, THRESHOLDS['cpu']),
            'frames': len(frames),
            'submitted': len(transactions)
        }
        
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

        return info

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

        self.history['daily_earnings'][today] = earnings
        self._save_history()
        return coins, earnings

    def get_daily_earnings(self, date=None):
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        
        today = datetime.now().strftime('%Y-%m-%d')
        
        # Return stored value for past days
        if date != today:
            return self.history.get('daily_earnings', {}).get(date, 0)
        
        # Calculate fresh for today
        start_time = datetime.strptime(f"{date} 00:00:00", '%Y-%m-%d %H:%M:%S')
        end_time = datetime.now()
        coins = self.get_coin_data(start_time, end_time)
        
        total_earnings = sum(coins)
        # Store just the total
        if 'daily_earnings' not in self.history:
            self.history['daily_earnings'] = {}
        self.history['daily_earnings'][today] = total_earnings
        
        return total_earnings

    def get_processing_metrics(self, date=None):
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
            
        today = datetime.now().strftime('%Y-%m-%d')
        
        # Return cached data for historical dates
        if date != today:
            return self.history.get('processing_metrics', {}).get(date, {
                'creation': {'total': 0, 'avg_time': 0},
                'submission': {'total': 0, 'avg_time': 0},
                'cpu': {'total': 0, 'avg_time': 0}
            })
        
        # For today's metrics
        metrics = ProcessingMetrics()
        
        # Single journalctl query for today's logs
        cmd = f'journalctl -u ceremonyclient.service --since "{date} 00:00:00" --until "{date} 23:59:59" --no-hostname -o cat | grep -E "creating data shard ring proof|submitting data proof"'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        
        creation_data = {}
        for line in result.stdout.splitlines():
            try:
                if "creating data shard ring proof" in line:
                    data = json.loads(line)
                    frame_number = data.get('frame_number')
                    frame_age = float(data.get('frame_age', 0))
                    creation_data[frame_number] = {'age': frame_age}
                    metrics.add_creation(frame_age)
                elif "submitting data proof" in line:
                    data = json.loads(line)
                    frame_number = data.get('frame_number')
                    frame_age = float(data.get('frame_age', 0))
                    if frame_number in creation_data:
                        cpu_time = frame_age - creation_data[frame_number]['age']
                        metrics.add_cpu_time(cpu_time)
                    metrics.add_submission(frame_age)
            except:
                continue

        stats = metrics.get_stats()
        self.history['processing_metrics'][date] = stats
        return stats

    def calculate_landing_rate(self, date=None):
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
            
        today = datetime.now().strftime('%Y-%m-%d')
        
        # Return stored rates for past days
        if date != today:
            return self.history.get('landing_rates', {}).get(date, {'rate': 0, 'transactions': 0, 'frames': 0})
        
        # Calculate fresh for today
        metrics = self.get_processing_metrics(date)
        total_frames = metrics['creation']['total'] if metrics else 0
        
        if total_frames == 0:
            return {'rate': 0, 'transactions': 0, 'frames': 0}
        
        start_time = datetime.strptime(f"{date} 00:00:00", '%Y-%m-%d %H:%M:%S')
        end_time = datetime.now()
        coins = self.get_coin_data(start_time, end_time)
        
        transactions = len(coins)  # Coins already filtered to ≤ 30 QUIL
        landing_rate = min((transactions / total_frames * 100), 100)
        
        result = {
            'rate': landing_rate,
            'transactions': transactions,
            'frames': total_frames
        }
        
        # Store just the summary
        if 'landing_rates' not in self.history:
            self.history['landing_rates'] = {}
        self.history['landing_rates'][date] = result
        
        return result

    def get_earnings_history(self, days=7):
        earnings_data = []
        today = datetime.now().date()
        
        for i in range(days):
            date = (today - timedelta(days=i)).strftime('%Y-%m-%d')
            earnings = self.get_daily_earnings(date)
            earnings_data.append((date, earnings))
        
        return earnings_data

    def check_daily_report_time(self):
        current_time = datetime.now()
        last_run_file = "last_report.txt"
        
        try:
            if os.path.exists(last_run_file):
                with open(last_run_file, 'r') as f:
                    last_run = datetime.strptime(f.read().strip(), '%Y-%m-%d')
            else:
                last_run = current_time - timedelta(days=1)

            if (current_time.date() > last_run.date() and 
                current_time.hour == TELEGRAM_CONFIG['daily_report_hour'] and 
                current_time.minute >= TELEGRAM_CONFIG['daily_report_minute']):
                
                with open(last_run_file, 'w') as f:
                    f.write(current_time.strftime('%Y-%m-%d'))
                return True
                
        except Exception as e:
            print(f"Error checking daily report time: {e}")
        
        return False

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

        print("\n=== QUIL Node Statistics ===")
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        print(f"\nNode Information:")
        print(f"Ring: {node_info['ring']}")
        print(f"Active Workers: {node_info['active_workers']}")
        print(f"Seniority: {node_info['seniority']}")
        print(f"QUIL Price: ${quil_price:.4f}")
        print(f"Balance: {node_info['owned_balance']:.6f} QUIL (${node_info['owned_balance'] * quil_price:.2f})")
        
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
    if os.geteuid() != 0:
        print("This script requires sudo privileges")
        sys.exit(1)

    monitor = QuilNodeMonitor()
    monitor.display_stats()

if __name__ == "__main__":
    main()
