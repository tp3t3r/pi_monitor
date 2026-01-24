#!/usr/bin/env python3

import json
import os

# Load config
def load_config():
    config = {}
    try:
        with open('/etc/pi_monitor.conf') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    key, val = line.split('=', 1)
                    config[key] = val
    except:
        pass
    return config

config = load_config()

DATA_FILE = config.get('monitoring', {}).get('data_file', os.getenv('DATA_FILE', '/opt/tmp/collected_data.json'))

try:
    with open(DATA_FILE) as f:
        last_line = None
        for line in f:
            last_line = line
        
        if last_line:
            data = json.loads(last_line)
            print(f"Timestamp: {data['timestamp']}")
            print(f"CPU Usage: {data['cpu_usage']}%")
            print(f"CPU Temp: {data['cpu_temp']}°C")
            print(f"Memory Usage: {data.get('memory_usage', 'N/A')}%")
            print(f"Disk Usage:")
            for path, usage in data['disk_usage'].items():
                print(f"  {path}: {usage}%" if usage else f"  {path}: N/A")
            print(f"Network:")
            for iface, stats in data['network'].items():
                rx_mbps = stats.get('rx_speed', 0) / 1024 / 1024
                tx_mbps = stats.get('tx_speed', 0) / 1024 / 1024
                print(f"  {iface}: RX {rx_mbps:.2f}MB/s TX {tx_mbps:.2f}MB/s")
            print(f"Disk I/O:")
            for device, stats in data.get('disk_io', {}).items():
                read_mbps = stats.get('read_speed', 0) / 1024 / 1024
                write_mbps = stats.get('write_speed', 0) / 1024 / 1024
                print(f"  {device}: Read {read_mbps:.2f}MB/s Write {write_mbps:.2f}MB/s")
        else:
            print("No data available")
except FileNotFoundError:
    print(f"Log file not found: {DATA_FILE}")
except Exception as e:
    print(f"Error: {e}")
