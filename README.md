# QUIL Node Monitor

A Python script for monitoring QUIL node performance, earnings, and metrics. This tool provides real-time tracking and historical data of your node's earnings in both QUIL and USD, along with detailed performance metrics.

## Features

- 🔄 Automatic detection and use of latest node binary
- 💰 Real-time QUIL/USD price tracking via CoinGecko API
- 📊 Comprehensive node statistics:
  - Daily earnings
  - Weekly and monthly averages
  - Shard metrics and performance
- 📈 Historical tracking of earnings and performance
- 💱 Automatic USD conversion for all QUIL values
- 📅 Calendar day-based metrics
- 🔍 Detailed shard statistics

## Sample Output
```
=== QUIL Node Statistics ===
Time: 2024-11-14 23:25:18

Node Information:
Ring:            3
Active Workers:  1024
QUIL Price:      $0.1180
QUIL on Node:    15136.297105
Weekly Average:  78.565432 QUIL // $96.98
Monthly Average: 312.123456 QUIL // $385.42
Daily Average:   11.223633 QUIL // $13.85

Today's Stats (2024-11-14):
Earnings:        12.345678 QUIL // $15.24
Total Shards:    666
Avg Time Between: 82.45 seconds
Avg Frame Age:    65.32 seconds

Earnings History:
2024-11-14: 12.345678 QUIL // $15.24
2024-11-13: 11.223344 QUIL // $13.85
2024-11-12: 10.987654 QUIL // $13.56
...
```

## Prerequisites

- Python 3.6 or higher
- Running QUIL Node with ceremonyclient service
- sudo access for log reading
- Active internet connection for price data

## Installation

## Installation

Navigate to your node directory defalt being ceremonyclient/node
1. Download the script directly to your node directory:
```bash
curl -O https://raw.githubusercontent.com/wolfcubecho/quil-monitor/main/quil_monitor.py && chmod +x quil_monitor.py
```
or using wget:
```bash
wget https://raw.githubusercontent.com/wolfcubecho/quil-monitor/main/quil_monitor.py && chmod +x quil_monitor.py
```

2. Install required package:
```bash
sudo pip3 install requests
```

3. Run the script:
```bash
sudo python3 quil_monitor.py
```

## Data Storage

The script stores historical data in `quil_metrics.json` in the same directory as the script. This file tracks:
- Daily balances
- Shard metrics
- Historical performance data

## Error Handling

The script includes robust error handling for:
- Node binary detection and access
- Service log reading
- Price API connectivity
- Data parsing and calculations

## Automatic Updates

The script automatically detects and uses the latest node binary in your directory, ensuring compatibility with node updates.

## Troubleshooting

Common issues and solutions:

1. "Permission denied"
   ```bash
   Solution: Run the script with sudo
   ```

2. "No node binary found"
   ```bash
   Solution: Ensure script is in the same directory as your node binary
   ```

3. "Error getting QUIL price"
   ```bash
   Solution: Check internet connection and CoinGecko API access
   ```

4. "Error getting metrics"
   ```bash
   Solution: Verify ceremonyclient service is running
            Check journalctl access permissions
   ```

## Contributing

Contributions are welcome! Feel free to submit Issues or Pull Requests.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Security Note

This script requires sudo access to read node logs. Always review scripts requiring elevated privileges before running them.

## Disclaimer

This tool is provided as-is. Always verify earnings and metrics with official sources. Price data is provided by CoinGecko and may have slight variations from other sources.

---
Made with ❤️ for the QUIL community
