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
                'avg_time': 0,
                'times': []  # Add storage for raw times
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
            'avg_time': sum(times)/total if total > 0 else 0,
            'times': times  # Store raw times
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
                f"🎯 Landing Rate: {landing_rate['landing_rate']:.2f}% "
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
    def __init__(self, log_file="quil_metrics.json"):
        self.log_file = log_file
        self.history = {
            'daily_balance': {},
            'processing_metrics': {},
            'landing_rates': {},
            'coin_data': {},  # Add coin storage to history
            'last_coin_update': None
        }
        self.coin_cache = None
        self.load_history()
        self.fix_history_timestamps()
        self.node_binary = self._get_latest_node_binary()
        self.qclient_binary = self._get_latest_qclient_binary()
        self.telegram = TelegramNotifier(TELEGRAM_CONFIG)
        self.last_report_check = datetime.now().replace(hour=0, minute=0, second=0)

    def get_earnings_history(self, days=7):
        earnings_data = []
        today = datetime.now().date()
        
        for i in range(days):
            date = (today - timedelta(days=i)).strftime('%Y-%m-%d')
            earnings = self.get_daily_earnings(date)
            earnings_data.append((date, earnings))
            
        return earnings_data

    def fix_history_timestamps(self):
        try:
            if 'coin_data' in self.history:
                for date in self.history['coin_data']:
                    for coin in self.history['coin_data'][date]:
                        if 'timestamp' in coin and isinstance(coin['timestamp'], datetime):
                            coin['timestamp'] = coin['timestamp'].strftime('%Y-%m-%dT%H:%M:%SZ')
            
            if 'last_coin_update' in self.history and isinstance(self.history['last_coin_update'], datetime):
                self.history['last_coin_update'] = self.history['last_coin_update'].strftime('%Y-%m-%dT%H:%M:%SZ')
            
            self._save_history()
            print("History timestamps fixed successfully")
        except Exception as e:
            print(f"Error fixing history timestamps: {e}")

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

    def load_history(self):
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, 'r') as f:
                    saved_data = json.load(f)
                    if 'daily_balance' in saved_data:
                        self.history['daily_balance'].update(saved_data['daily_balance'])
                    if 'processing_metrics' in saved_data:
                        self.history['processing_metrics'].update(saved_data['processing_metrics'])
                    if 'landing_rates' in saved_data:
                        self.history['landing_rates'].update(saved_data['landing_rates'])
                    if 'coin_data' in saved_data:
                        self.history['coin_data'].update(saved_data['coin_data'])
                    if 'last_coin_update' in saved_data:
                        self.history['last_coin_update'] = saved_data['last_coin_update']
                    if 'daily_earnings' in saved_data:
                        self.history['daily_earnings'] = saved_data['daily_earnings']
            except Exception as e:
                print(f"Error loading history (will start fresh): {e}")
                
    def _save_history(self):
        start_time = datetime.now()
        try:
            with open(self.log_file, 'w') as f:
                json.dump(self.history, f, indent=2)
            save_time = (datetime.now() - start_time).total_seconds()
            print(f"History save took: {save_time:.2f}s")
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
        # Check if we have recent cache (less than 60 seconds old)
        now = datetime.now().timestamp()
        last_node_check = self.history.get('last_node_check', 0)
        
        if now - last_node_check < 60 and 'node_info' in self.history:
            return self.history['node_info']
            
        try:
            result = subprocess.run([self.node_binary, '--node-info'], 
                                 capture_output=True, text=True)
            
            if result.returncode != 0:
                return None

            ring_match = re.search(r'Prover Ring: (\d+)', result.stdout)
            ring = int(ring_match.group(1)) if ring_match else 0

            seniority_match = re.search(r'Seniority: (\d+)', result.stdout)
            seniority = int(seniority_match.group(1)) if seniority_match else 0

            workers_match = re.search(r'Active Workers: (\d+)', result.stdout)
            active_workers = int(workers_match.group(1)) if workers_match else 0

            owned_balance_match = re.search(r'Owned balance: ([\d.]+) QUIL', result.stdout)
            owned_balance = float(owned_balance_match.group(1)) if owned_balance_match else 0

            node_info = {
                'ring': ring,
                'active_workers': active_workers,
                'owned': owned_balance,
                'total': owned_balance,
                'seniority': seniority
            }
            
            # Cache the result
            self.history['node_info'] = node_info
            self.history['last_node_check'] = now
            
            return node_info
            
        except Exception as e:
            print(f"Error getting node info: {e}")
            return None

    def get_coin_data(self, start_time, end_time):
        query_start = datetime.now()
        try:
            result = subprocess.run(
                [self.qclient_binary, 'token', 'coins', 'metadata', '--public-rpc'],
                capture_output=True, text=True,
                encoding='utf-8'
            )
            
            if result.returncode != 0:
                return []

            process_start = datetime.now()
            coins = []
            for line in result.stdout.splitlines():
                try:
                    amount_match = re.search(r'([\d.]+)\s*QUIL', line)
                    frame_match = re.search(r'Frame\s*(\d+)', line)
                    timestamp_match = re.search(r'Timestamp\s*([\d-]+T[\d:]+Z)', line)
                    
                    if amount_match and frame_match and timestamp_match:
                        timestamp_str = timestamp_match.group(1)
                        if timestamp_str:
                            timestamp = datetime.strptime(timestamp_str, '%Y-%m-%dT%H:%M:%SZ')
                            if start_time <= timestamp <= end_time:
                                coin = {
                                    'amount': float(amount_match.group(1)),
                                    'frame': int(frame_match.group(1)),
                                    'timestamp': timestamp
                                }
                                coins.append(coin)
                except:
                    continue

            total_time = (datetime.now() - query_start).total_seconds()
            process_time = (datetime.now() - process_start).total_seconds()
            print(f"\nCoin Data Performance:")
            print(f"Query time: {(process_start - query_start).total_seconds():.2f}s")
            print(f"Processing time: {process_time:.2f}s")
            print(f"Total time: {total_time:.2f}s")

            return coins
            
        except Exception as e:
            print(f"Error getting coin data: {e}")
            return []

    def get_coin_data_for_date(self, date):
        """Helper method to get coin data for a specific date"""
        if 'coin_data' in self.history and date in self.history['coin_data']:
            return self.history['coin_data'][date]
        return []
        
    def calculate_landing_rate(self, date=None):
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
            
        today = datetime.now().strftime('%Y-%m-%d')
        
        # Use cached data for historical dates
        if date != today and date in self.history.get('landing_rates', {}):
            return self.history['landing_rates'][date]
            
        try:
            metrics = self.get_processing_metrics(date)
            total_frames = metrics['creation']['total'] if metrics else 0
            
            if total_frames == 0:
                return {'landing_rate': 0, 'transactions': 0, 'frames': 0}
            
            start_time = datetime.strptime(f"{date} 00:00:00", '%Y-%m-%d %H:%M:%S')
            end_time = datetime.strptime(f"{date} 23:59:59", '%Y-%m-%d %H:%M:%S')
            coins = self.get_coin_data(start_time, end_time)
            
            transactions = sum(1 for coin in coins if coin['amount'] <= 30)
            landing_rate = min((transactions / total_frames * 100), 100)
            
            result = {
                'landing_rate': landing_rate,
                'transactions': transactions,
                'frames': total_frames
            }
            
            if date == today:
                self.history['landing_rates'][date] = result
                
            return result
            
        except Exception as e:
            print(f"Error calculating landing rate: {e}")
            return {'landing_rate': 0, 'transactions': 0, 'frames': 0}
            
    def get_processing_metrics(self, date=None):
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')

        # For historical dates, return from cache
        today = datetime.now().strftime('%Y-%m-%d')
        if date != today:
            return self.history.get('processing_metrics', {}).get(date, {
                'creation': {'total': 0, 'avg_time': 0},
                'submission': {'total': 0, 'avg_time': 0},
                'cpu': {'total': 0, 'avg_time': 0}
            })

        # For today, get stored metrics
        metrics = ProcessingMetrics()
        stored_metrics = self.history.get('processing_metrics', {}).get(date, None)
        if stored_metrics:
            for t in stored_metrics.get('creation', {}).get('times', []):
                metrics.add_creation(t)
            for t in stored_metrics.get('submission', {}).get('times', []):
                metrics.add_submission(t)
            for t in stored_metrics.get('cpu', {}).get('times', []):
                metrics.add_cpu_time(t)

        # Get last processed time
        last_processed = self.history.get('last_processed_time', '00:00:00')
        
        # Only get new logs since last processed
        cmd = f'journalctl -u ceremonyclient.service --since "today {last_processed}" --until "now" --no-hostname -o cat | grep -E "creating data shard ring proof|submitting data proof"'
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
        # Cache the raw times for later
        stats['creation']['times'] = metrics.creation_times
        stats['submission']['times'] = metrics.submission_times
        stats['cpu']['times'] = metrics.cpu_times
        self.history['processing_metrics'][date] = stats
        self.history['last_processed_time'] = datetime.now().strftime('%H:%M:%S')
        
        return stats

    def get_coin_data(self, start_time, end_time):
        date = start_time.strftime('%Y-%m-%d')
        today = datetime.now().strftime('%Y-%m-%d')
        
        # For historical dates, use cached data
        if date != today:
            return self.history.get('coin_data', {}).get(date, [])
            
        # For today, get fresh data
        result = subprocess.run(
            [self.qclient_binary, 'token', 'coins', 'metadata', '--public-rpc'],
            capture_output=True, text=True,
            encoding='utf-8'
        )
        
        if result.returncode != 0:
            return []

        coins = []
        for line in result.stdout.splitlines():
            try:
                amount_match = re.search(r'([\d.]+)\s*QUIL', line)
                frame_match = re.search(r'Frame\s*(\d+)', line)
                timestamp_match = re.search(r'Timestamp\s*([\d-]+T[\d:]+Z)', line)
                
                if amount_match and frame_match and timestamp_match:
                    timestamp_str = timestamp_match.group(1)
                    if timestamp_str:
                        timestamp = datetime.strptime(timestamp_str, '%Y-%m-%dT%H:%M:%SZ')
                        if start_time <= timestamp <= end_time:
                            coin = {
                                'amount': float(amount_match.group(1)),
                                'frame': int(frame_match.group(1)),
                                'timestamp': timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')
                            }
                            coins.append(coin)
            except:
                continue
                
        self.history['coin_data'][date] = coins
        return coins
            
    def get_daily_earnings(self, date):
        """Calculate earnings for a specific date"""
        try:
            TRANSFER_THRESHOLD = 30  # QUIL
            
            start_time = datetime.strptime(f"{date} 00:00:00", '%Y-%m-%d %H:%M:%S')
            end_time = datetime.strptime(f"{date} 23:59:59", '%Y-%m-%d %H:%M:%S')
            
            coins = self.get_coin_data(start_time, end_time)
            
            if not coins:
                coins = self.get_coin_data_for_date(date)
            
            if not coins:
                return 0
            
            daily_earnings = sum(
                coin['amount'] 
                for coin in coins 
                if coin['amount'] <= TRANSFER_THRESHOLD
            )
            
            if not hasattr(self, 'history'):
                self.history = {}
            if 'daily_earnings' not in self.history:
                self.history['daily_earnings'] = {}
            self.history['daily_earnings'][date] = daily_earnings
            
            return daily_earnings
            
        except Exception as e:
            print(f"Error calculating earnings for {date}: {e}")
            return 0

    def get_coin_data(self, start_time, end_time):
        """Get fresh coin data from the node"""
        try:
            if isinstance(start_time, datetime) and start_time.date() == datetime.now().date():
                self.coin_cache = None
                self.history['last_coin_update'] = None
            
            result = subprocess.run(
                [self.qclient_binary, 'token', 'coins', 'metadata', '--public-rpc'],
                capture_output=True, text=True,
                encoding='utf-8'
            )
            
            if result.returncode != 0:
                return []

            new_coins = []
            for line in result.stdout.splitlines():
                try:
                    amount_match = re.search(r'([\d.]+)\s*QUIL', line)
                    frame_match = re.search(r'Frame\s*(\d+)', line)
                    timestamp_match = re.search(r'Timestamp\s*([\d-]+T[\d:]+Z)', line)
                    
                    if amount_match and frame_match and timestamp_match:
                        timestamp_str = timestamp_match.group(1)
                        if timestamp_str:
                            timestamp = datetime.strptime(timestamp_str, '%Y-%m-%dT%H:%M:%SZ')
                            if start_time <= timestamp <= end_time:
                                coin = {
                                    'amount': float(amount_match.group(1)),
                                    'frame': int(frame_match.group(1)),
                                    'timestamp': timestamp
                                }
                                new_coins.append(coin)
                except Exception:
                    continue

            return new_coins
            
        except Exception as e:
            print(f"Error getting fresh coin data: {e}")
            return []
    
    def get_daily_earnings_history(self, days=7):
        """Get historical earnings data and calculate average"""
        earnings_data = []
        total_earnings = 0
        days_with_data = 0
        
        today = datetime.now().date()
        for i in range(days):
            date = (today - timedelta(days=i)).strftime('%Y-%m-%d')
            earnings = self.get_daily_earnings(date)
            if earnings > 0:
                total_earnings += earnings
                days_with_data += 1
            earnings_data.append((date, earnings))
        
        # Calculate daily average only from days with earnings
        daily_avg = total_earnings / days_with_data if days_with_data > 0 else 0
        
        return earnings_data, daily_avg
    
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

    # New method for getting landing rate history
    def get_landing_rate_history(self, days=7):
        landing_data = []
        today = datetime.now().date()
        
        for i in range(days):
            date = (today - timedelta(days=i)).strftime('%Y-%m-%d')
            metrics = self.get_processing_metrics(date)
            if metrics and metrics['creation']['total'] > 0:
                if date in self.history.get('daily_earnings', {}):
                    frames = metrics['creation']['total']
                    coins = sum(1 for coin in self.get_coin_data_for_date(date) if coin['amount'] <= 30)
                    landing_rate = min((coins / frames * 100), 100) if frames > 0 else 0
                    landing_data.append(landing_rate)

        return landing_data

    # Updated display_stats method
    def display_stats(self):
        print("\n=== QUIL Node Statistics ===")
        current_time = datetime.now()
        today = current_time.strftime('%Y-%m-%d')
        print(f"Time: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        node_info = self.get_node_info()
        quil_price = self.get_quil_price()
        
        # Calculate today's data
        today_earnings = self.get_daily_earnings(today)
        today_metrics = self.get_processing_metrics(today)
        today_landing = self.calculate_landing_rate(today)
        
        if node_info:
            earnings_data = self.get_earnings_history(7)
            daily_avg = sum(earning for _, earning in earnings_data) / 7
            
            week_rates = []
            month_rates = []
            today_date = datetime.now().date()
            
            # Get all rates in one pass
            for i in range(30):
                date = (today_date - timedelta(days=i)).strftime('%Y-%m-%d')
                data = self.calculate_landing_rate(date)
                if data['frames'] > 0:
                    if i < 7:
                        week_rates.append(data['landing_rate'])
                    month_rates.append(data['landing_rate'])
            
            week_avg = sum(week_rates) / len(week_rates) if week_rates else 0
            month_avg = sum(month_rates) / len(month_rates) if month_rates else 0

            # Color coding for different time periods
            daily_color = (COLORS['green'] if today_landing['landing_rate'] >= THRESHOLDS['landing_rate']['good']
                         else COLORS['yellow'] if today_landing['landing_rate'] >= THRESHOLDS['landing_rate']['warning']
                         else COLORS['red'])
            
            weekly_color = (COLORS['green'] if week_avg >= THRESHOLDS['landing_rate']['good']
                          else COLORS['yellow'] if week_avg >= THRESHOLDS['landing_rate']['warning']
                          else COLORS['red'])
            
            monthly_color = (COLORS['green'] if month_avg >= THRESHOLDS['landing_rate']['good']
                           else COLORS['yellow'] if month_avg >= THRESHOLDS['landing_rate']['warning']
                           else COLORS['red'])

            print(f"\nNode Information:")
            print(f"Ring:            {node_info['ring']}")
            print(f"Active Workers:  {node_info['active_workers']}")
            print(f"Seniority:      {node_info['seniority']}")
            print(f"QUIL Price:      ${quil_price:.4f}")
            print(f"QUIL on Node:    {node_info['total']:.6f}")
            
            print(f"\nDaily Average:   {daily_avg:.6f} QUIL // ${daily_avg * quil_price:.2f} // "
                  f"{daily_color}{today_landing['landing_rate']:.2f}%{COLORS['reset']}")
            print(f"Weekly Average:  {daily_avg * 7:.6f} QUIL // ${daily_avg * 7 * quil_price:.2f} // "
                  f"{weekly_color}{week_avg:.2f}%{COLORS['reset']}")
            print(f"Monthly Average: {daily_avg * 30:.6f} QUIL // ${daily_avg * 30 * quil_price:.2f} // "
                  f"{monthly_color}{month_avg:.2f}%{COLORS['reset']}")

        print(f"\nToday's Stats ({today}):")
        print(f"Earnings:        {today_earnings:.6f} QUIL // ${today_earnings * quil_price:.2f}")
        
        # Color code the landing rate
        landing_color = (COLORS['green'] if today_landing['landing_rate'] >= THRESHOLDS['landing_rate']['good']
                        else COLORS['yellow'] if today_landing['landing_rate'] >= THRESHOLDS['landing_rate']['warning']
                        else COLORS['red'])
        print(f"Landing Rate:    {landing_color}{today_landing['landing_rate']:.2f}%{COLORS['reset']} "
              f"({today_landing['transactions']} transactions / {today_landing['frames']} frames)")
        
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

        print("\nHistory (Last 7 Days):")
        for date, earnings in earnings_data:
            metrics = self.get_processing_metrics(date)
            landing_data = self.calculate_landing_rate(date)
            cpu_info = metrics.get('cpu', {})
            avg_cpu = cpu_info.get('avg_time', 0)
            
            landing_color = (COLORS['green'] if landing_data['landing_rate'] >= THRESHOLDS['landing_rate']['good']
                           else COLORS['yellow'] if landing_data['landing_rate'] >= THRESHOLDS['landing_rate']['warning']
                           else COLORS['red'])
            
            print(f"{date}: {earnings:.6f} QUIL // ${earnings * quil_price:.2f} "
                  f"(Landing Rate: {landing_color}{landing_data['landing_rate']:.2f}%{COLORS['reset']}, "
                  f"{landing_data['transactions']}/{landing_data['frames']} frames, "
                  f"Avg Process: {avg_cpu:.2f}s)")

        # Single history save at the end
        self._save_history()

        # Check for daily report
        if self.check_daily_report_time():
            self.telegram.send_daily_summary(
                balance=node_info['total'],
                earnings=today_earnings,
                avg_earnings=daily_avg,
                metrics=today_metrics,
                quil_price=quil_price,
                landing_rate=today_landing
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
    
    # Add overall timing
    start_time = datetime.now()
    
    monitor = QuilNodeMonitor()
    monitor.display_stats()
    
    # Show total runtime
    total_time = (datetime.now() - start_time).total_seconds()
    print(f"\nTotal runtime: {total_time:.2f} seconds")

if __name__ == "__main__":
    main()
