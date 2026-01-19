#!/usr/bin/env python3

import os
import time
import json
import signal
import cProfile
import pstats
from datetime import datetime, timedelta

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

# Configuration
INTERVAL = int(config.get('INTERVAL', os.getenv('INTERVAL', '60')))
LOG_FILE = config.get('LOG_FILE', os.getenv('LOG_FILE', 'pi_monitor.json'))
RETENTION_DAYS = int(config.get('RETENTION_DAYS', os.getenv('RETENTION_DAYS', '7')))
DISK_PATHS = config.get('DISK_PATHS', os.getenv('DISK_PATHS', '/mnt/cam1,/mnt/cam2,/opt')).split(',')
DISK_IO_DEVICES = config.get('DISK_IO_DEVICES', os.getenv('DISK_IO_DEVICES', 'sda,mmcblk0')).split(',')
NETWORK_INTERFACES = config.get('NETWORK_INTERFACES', os.getenv('NETWORK_INTERFACES', '')).split(',') if config.get('NETWORK_INTERFACES', os.getenv('NETWORK_INTERFACES', '')) else []

def get_cpu_usage():
    with open('/proc/stat') as f:
        fields = f.readline().split()[1:]
    idle = int(fields[3])
    total = sum(int(x) for x in fields)
    return round(100 - (idle * 100 / total), 1)

def get_cpu_temp():
    with open('/sys/class/thermal/thermal_zone0/temp') as f:
        return round(int(f.read()) / 1000, 1)

prev_net_stats = {}
memory_buffer = []

def get_net_stats():
    global prev_net_stats
    stats = {}
    current = {}
    
    with open('/proc/net/dev') as f:
        for line in f:
            if ':' in line and 'lo:' not in line:
                parts = line.split()
                iface = parts[0].rstrip(':')
                if not NETWORK_INTERFACES or iface in NETWORK_INTERFACES:
                    current[iface] = {'rx': int(parts[1]), 'tx': int(parts[9])}
    
    if prev_net_stats:
        for iface, curr in current.items():
            if iface in prev_net_stats:
                prev = prev_net_stats[iface]
                stats[iface] = {
                    'rx_speed': (curr['rx'] - prev['rx']) / INTERVAL,
                    'tx_speed': (curr['tx'] - prev['tx']) / INTERVAL
                }
    
    prev_net_stats = current
    return stats if stats else {iface: {'rx_speed': 0, 'tx_speed': 0} for iface in current}

def get_disk_usage():
    usage = {}
    for path in DISK_PATHS:
        try:
            st = os.statvfs(path)
            usage[path] = round(100 - (st.f_bavail * 100 / st.f_blocks), 1)
        except:
            usage[path] = None
    return usage

def get_memory_usage():
    with open('/proc/meminfo') as f:
        lines = f.readlines()
    mem = {}
    for line in lines:
        if line.startswith('MemTotal:'):
            mem['total'] = int(line.split()[1])
        elif line.startswith('MemAvailable:'):
            mem['available'] = int(line.split()[1])
    used = mem['total'] - mem['available']
    return round(used * 100 / mem['total'], 1)

prev_disk_io = {}

def get_disk_io():
    global prev_disk_io
    io_stats = {}
    current = {}
    
    with open('/proc/diskstats') as f:
        for line in f:
            parts = line.split()
            device = parts[2]
            if device in DISK_IO_DEVICES:
                current[device] = {
                    'read': int(parts[5]) * 512,
                    'write': int(parts[9]) * 512
                }
    
    if prev_disk_io:
        for device, curr in current.items():
            if device in prev_disk_io:
                prev = prev_disk_io[device]
                io_stats[device] = {
                    'read_speed': (curr['read'] - prev['read']) / INTERVAL,
                    'write_speed': (curr['write'] - prev['write']) / INTERVAL
                }
    
    prev_disk_io = current
    return io_stats if io_stats else {device: {'read_speed': 0, 'write_speed': 0} for device in current}

def cleanup_old_data():
    if not os.path.exists(LOG_FILE):
        return
    cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).isoformat()
    with open(LOG_FILE) as f:
        data = [json.loads(l) for l in f if json.loads(l)['timestamp'] >= cutoff]
    with open(LOG_FILE, 'w') as f:
        for entry in data:
            f.write(json.dumps(entry) + '\n')

def flush_buffer():
    global memory_buffer
    if memory_buffer:
        with open(LOG_FILE, 'a') as f:
            for entry in memory_buffer:
                f.write(json.dumps(entry) + '\n')
        memory_buffer = []

print(f"Starting monitoring (interval: {INTERVAL}s, retention: {RETENTION_DAYS} days)")

ENABLE_PROFILING = os.getenv('ENABLE_PROFILING', 'false').lower() == 'true'
profiler = cProfile.Profile() if ENABLE_PROFILING else None

buffer = []
last_write = datetime.now()

def flush_buffer(signum=None, frame=None):
    global buffer, last_write
    if buffer:
        with open(LOG_FILE, 'a') as f:
            for e in buffer:
                f.write(json.dumps(e) + '\n')
        buffer = []
        last_write = datetime.now()
        
        if profiler:
            profiler.disable()
            stats = pstats.Stats(profiler)
            stats.dump_stats('/var/log/pi_monitor_profile.stats')
            profiler.clear()
            profiler.enable()

signal.signal(signal.SIGUSR1, flush_buffer)

if ENABLE_PROFILING:
    profiler.enable()

# Collect initial data immediately
entry = {
    'timestamp': datetime.now().isoformat(),
    'cpu_usage': get_cpu_usage(),
    'cpu_temp': get_cpu_temp(),
    'memory_usage': get_memory_usage(),
    'network': get_net_stats(),
    'disk_usage': get_disk_usage(),
    'disk_io': get_disk_io()
}
buffer.append(entry)
with open('/dev/shm/pi_monitor_buffer.json', 'w') as f:
    json.dump(buffer, f)

while True:
    entry = {
        'timestamp': datetime.now().isoformat(),
        'cpu_usage': get_cpu_usage(),
        'cpu_temp': get_cpu_temp(),
        'memory_usage': get_memory_usage(),
        'network': get_net_stats(),
        'disk_usage': get_disk_usage(),
        'disk_io': get_disk_io()
    }
    
    buffer.append(entry)
    
    # Write buffer to shared memory for web service
    with open('/dev/shm/pi_monitor_buffer.json', 'w') as f:
        json.dump(buffer, f)
    
    # Write to disk every hour
    if (datetime.now() - last_write).total_seconds() >= 3600:
        with open(LOG_FILE, 'a') as f:
            for e in buffer:
                f.write(json.dumps(e) + '\n')
        buffer = []
        last_write = datetime.now()
        
        # Cleanup old data after writing
        cleanup_old_data()
    
    time.sleep(INTERVAL)
