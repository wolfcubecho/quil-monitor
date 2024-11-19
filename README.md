# QUIL Node Monitor

A comprehensive monitoring solution for QUIL nodes that tracks earnings, shard processing metrics, and sends daily reports via Telegram.

## Features

- 📊 Real-time monitoring of node performance and earnings
- 💰 Automatic QUIL/USD price tracking
- ⏱️ Detailed shard processing metrics and categorization
- 🚨 Telegram alerts for performance issues
- 📱 Daily summary reports via Telegram
- 📈 CSV export for historical data analysis
- 🎨 Color-coded terminal output
- 🤖 Automation support via cron

## Quick Start

1. Wget py script in your node folder:
```bash
wget https://raw.githubusercontent.com/wolfcubecho/quil-monitor/main/quil_monitor.py && chmod +x quil_monitor.py
```

2. Install requirements:
```bash
pip3 install requests
```

3. Set up Telegram notifications:
```bash
sudo python3 quil_monitor.py --setup-telegram
```

4. Run the monitor:
```bash
sudo python3 quil_monitor.py
```

## Telegram Integration

### Setup
1. Message @BotFather on Telegram to create a new bot
2. Get your chat ID from @userinfobot
3. Run the setup command or manually configure in the script:
```python
TELEGRAM_CONFIG = {
    'bot_token': 'YOUR_BOT_TOKEN',
    'chat_id': 'YOUR_CHAT_ID',
    'node_name': 'Node-1',
    'enabled': True,
    'daily_report_hour': 0,
    'daily_report_minute': 5
}
```

### Daily Reports
Receives a daily summary including:
- Current QUIL balance and USD value
- Daily earnings and comparison to average
- Shard processing statistics
- Performance metrics

## Monitoring Features

### Earnings Tracking
- Real-time QUIL earnings monitoring
- Automatic USD conversion
- Daily, weekly, and monthly averages
- Historical earnings data

### Shard Processing
- Total shard count
- Processing time categorization:
  - Fast (0-30s)
  - Medium (30-60s)
  - Slow (60s+)
- Average processing times
- Hourly shard rates

### Performance Alerts
Configurable alerts for:
```python
ALERT_THRESHOLDS = {
    'processing_time_warning': 45,    # seconds
    'processing_time_critical': 60,   # seconds
    'earnings_deviation': 25         # percentage
}
```

## Automation

### Cron Setup
Add to crontab (`sudo crontab -e`):
```bash
# Run monitor every hour
0 * * * * cd /root/ceremonyclient/node && sudo python3 quil_monitor.py

# Export data daily
0 0 * * * cd /root/ceremonyclient/node && sudo python3 quil_monitor.py --export-csv
```

## Data Export

### CSV Exports
The script generates two CSV files:

1. `quil_daily.csv`:
   - Date
   - Balance
   - Earnings
   - USD values
   - Shard counts
   - Processing times

2. `quil_shards.csv`:
   - Detailed shard metrics
   - Processing time categories
   - Performance statistics

## Requirements

- Python 3.6 or higher
- QUIL node with ceremonyclient service
- sudo access for log reading
- Internet connection for price data
- `requests` Python package

## Troubleshooting

### Common Issues

1. Permission denied
```bash
Solution: Run with sudo
```

2. No node binary found
```bash
Solution: Ensure script is in node directory
```

3. Telegram configuration
```bash
Solution: Run --setup-telegram or check config values
```

4. Missing historical data
```bash
Solution: Allow script to run for multiple days to build history
```

## Security Note

This script requires sudo access to read logs. Always review scripts requiring elevated privileges before running them.

## Contributing

Contributions are welcome! Please feel free to submit Issues or Pull Requests.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

---
Made with ❤️ for the QUIL community
