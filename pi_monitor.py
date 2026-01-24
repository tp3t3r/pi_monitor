#!/usr/bin/env python3

import os
import time
import json
import signal
import cProfile
import pstats
from datetime import datetime, timedelta
from abc import ABC, abstractmethod

# Load config
def load_config():
    try:
        with open('/etc/pi_monitor.json') as f:
            return json.load(f)
    except:
        return {}

config = load_config()

# Configuration
INTERVAL = config.get('monitoring', {}).get('interval', 60)
DATA_FILE = config.get('monitoring', {}).get('data_file', '/opt/tmp/collected_data.json')
RETENTION_DAYS = config.get('monitoring', {}).get('retention_days', 7)
ENABLE_PROFILING = config.get('monitoring', {}).get('enable_profiling', False)


class Metric(ABC):
    """Base class for all metrics"""
    
    def __init__(self, config):
        self.config = config
        self.enabled = config.get('enabled', True)
    
    @abstractmethod
    def collect(self):
        """Collect metric data and return value"""
        pass


class CPUMetric(Metric):
    def __init__(self, config):
        super().__init__(config)
        self.prev_idle = 0
        self.prev_total = 0
    
    def collect(self):
        with open('/proc/stat') as f:
            fields = f.readline().split()[1:]
        idle = int(fields[3])
        total = sum(int(x) for x in fields)
        
        if self.prev_total == 0:
            self.prev_idle, self.prev_total = idle, total
            return 0.0
        
        idle_delta = idle - self.prev_idle
        total_delta = total - self.prev_total
        self.prev_idle, self.prev_total = idle, total
        
        if total_delta == 0:
            return 0.0
        
        return round(100 * (1 - idle_delta / total_delta), 1)


class TempMetric(Metric):
    def collect(self):
        with open('/sys/class/thermal/thermal_zone0/temp') as f:
            return round(int(f.read()) / 1000, 1)


class MemoryMetric(Metric):
    def collect(self):
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


class DiskMetric(Metric):
    def __init__(self, config):
        super().__init__(config)
        self.paths = config.get('paths', [])
    
    def collect(self):
        usage = {}
        for path in self.paths:
            try:
                st = os.statvfs(path)
                usage[path] = round(100 - (st.f_bavail * 100 / st.f_blocks), 1)
            except:
                usage[path] = None
        return usage


class NetworkMetric(Metric):
    def __init__(self, config):
        super().__init__(config)
        self.interfaces = config.get('interfaces', [])
        self.prev_stats = {}
    
    def collect(self):
        stats = {}
        current = {}
        
        with open('/proc/net/dev') as f:
            for line in f:
                if ':' in line and 'lo:' not in line:
                    parts = line.split()
                    iface = parts[0].rstrip(':')
                    if not self.interfaces or iface in self.interfaces:
                        current[iface] = {'rx': int(parts[1]), 'tx': int(parts[9])}
        
        if self.prev_stats:
            for iface, curr in current.items():
                if iface in self.prev_stats:
                    prev = self.prev_stats[iface]
                    stats[iface] = {
                        'rx_speed': (curr['rx'] - prev['rx']) / INTERVAL,
                        'tx_speed': (curr['tx'] - prev['tx']) / INTERVAL
                    }
        
        self.prev_stats = current
        return stats if stats else {iface: {'rx_speed': 0, 'tx_speed': 0} for iface in current}


class DiskIOMetric(Metric):
    def __init__(self, config):
        super().__init__(config)
        self.devices = config.get('devices', [])
        self.prev_io = {}
    
    def collect(self):
        io_stats = {}
        current = {}
        
        with open('/proc/diskstats') as f:
            for line in f:
                parts = line.split()
                device = parts[2]
                if device in self.devices:
                    current[device] = {
                        'read_count': int(parts[3]),
                        'write_count': int(parts[7])
                    }
        
        if self.prev_io:
            for device, curr in current.items():
                if device in self.prev_io:
                    prev = self.prev_io[device]
                    io_stats[device] = {
                        'read_count': curr['read_count'] - prev['read_count'],
                        'write_count': curr['write_count'] - prev['write_count']
                    }
        
        self.prev_io = current
        return io_stats if io_stats else {device: {'read_count': 0, 'write_count': 0} for device in current}


# Initialize metrics
metrics = {
    'cpu': CPUMetric(config.get('metrics', {}).get('cpu', {})),
    'temp': TempMetric(config.get('metrics', {}).get('temp', {})),
    'memory': MemoryMetric(config.get('metrics', {}).get('memory', {})),
    'disk': DiskMetric(config.get('metrics', {}).get('disk', {})),
    'network': NetworkMetric(config.get('metrics', {}).get('network', {})),
    'disk_io': DiskIOMetric(config.get('metrics', {}).get('diskio', {}))
}


def cleanup_old_data():
    if not os.path.exists(DATA_FILE):
        return
    cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).isoformat()
    with open(DATA_FILE) as f:
        data = [json.loads(l) for l in f if json.loads(l)['timestamp'] >= cutoff]
    with open(DATA_FILE, 'w') as f:
        for entry in data:
            f.write(json.dumps(entry) + '\n')


print(f"Starting monitoring (interval: {INTERVAL}s, retention: {RETENTION_DAYS} days)")

profiler = cProfile.Profile() if ENABLE_PROFILING else None

buffer = []
last_write = datetime.now()

def flush_buffer(signum=None, frame=None):
    global buffer, last_write
    if buffer:
        with open(DATA_FILE, 'a') as f:
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

while True:
    entry = {
        'timestamp': datetime.now().isoformat(),
        'cpu_usage': metrics['cpu'].collect(),
        'cpu_temp': metrics['temp'].collect(),
        'memory_usage': metrics['memory'].collect(),
        'network': metrics['network'].collect(),
        'disk_usage': metrics['disk'].collect(),
        'disk_io': metrics['disk_io'].collect()
    }
    
    buffer.append(entry)
    
    # Write buffer to shared memory for web service
    with open('/dev/shm/pi_monitor_buffer.json', 'w') as f:
        json.dump(buffer, f)
    
    # Write to disk every hour
    if (datetime.now() - last_write).total_seconds() >= 3600:
        with open(DATA_FILE, 'a') as f:
            for e in buffer:
                f.write(json.dumps(e) + '\n')
        buffer = []
        last_write = datetime.now()
        
        # Cleanup old data after writing
        cleanup_old_data()
    
    time.sleep(INTERVAL)
