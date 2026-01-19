#!/usr/bin/env python3

import os
import json
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io

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

PORT = int(config.get('PORT', os.getenv('PORT', '9000')))
LOG_FILE = config.get('LOG_FILE', os.getenv('LOG_FILE', '/var/log/pi_monitor.json'))
MAX_RECORDS = int(config.get('MAX_RECORDS', os.getenv('MAX_RECORDS', '50')))
RESOURCE_DIR = config.get('RESOURCE_DIR', os.getenv('RESOURCE_DIR', '/usr/share/pi_monitor'))

def read_logs(limit=None, hours=None):
    data = []
    
    # Read from disk
    try:
        with open(LOG_FILE) as f:
            for line in f:
                data.append(json.loads(line))
    except:
        pass
    
    # Read from memory buffer
    try:
        with open('/dev/shm/pi_monitor_buffer.json', 'r') as f:
            data.extend(json.load(f))
    except:
        pass
    
    if hours:
        cutoff = datetime.now() - timedelta(hours=hours)
        data = [d for d in data if datetime.fromisoformat(d['timestamp']) >= cutoff]
    
    if limit:
        data = data[-limit:]
    
    return data

def downsample_data(timestamps, values, max_points=200):
    """Downsample data by averaging chunks while preserving trends"""
    if len(timestamps) <= max_points:
        return timestamps, values
    
    chunk_size = len(timestamps) // max_points
    downsampled_ts = []
    downsampled_vals = []
    
    for i in range(0, len(timestamps), chunk_size):
        chunk_ts = timestamps[i:i+chunk_size]
        chunk_vals = values[i:i+chunk_size]
        if chunk_ts and chunk_vals:
            downsampled_ts.append(chunk_ts[len(chunk_ts)//2])
            downsampled_vals.append(sum(chunk_vals) / len(chunk_vals))
    
    return downsampled_ts, downsampled_vals

def generate_graph(metric, limit=None, hours=None):
    data = read_logs(limit=limit, hours=hours)
    if not data:
        return None
    
    timestamps = [datetime.fromisoformat(d['timestamp']) for d in data]
    
    fig, ax = plt.subplots(figsize=(12, 6))
    
    # Add alternating day backgrounds only for "all" view
    if timestamps and not hours:
        start_date = timestamps[0].date()
        end_date = timestamps[-1].date()
        current_date = start_date
        color_toggle = True
        while current_date <= end_date:
            day_start = datetime.combine(current_date, datetime.min.time())
            day_end = datetime.combine(current_date, datetime.max.time())
            if color_toggle:
                ax.axvspan(day_start, day_end, alpha=0.1, color='gray')
            color_toggle = not color_toggle
            current_date = current_date + timedelta(days=1)
    
    # Downsample for "all" view
    should_downsample = not hours and not limit
    
    if metric == 'cpu':
        values = [d['cpu_usage'] for d in data]
        if should_downsample:
            timestamps, values = downsample_data(timestamps, values)
        ax.plot(timestamps, values, label='CPU Usage %')
        ax.set_ylabel('CPU Usage (%)')
        ax.set_title('CPU Usage Over Time')
    elif metric == 'temp':
        values = [d['cpu_temp'] for d in data]
        if should_downsample:
            timestamps, values = downsample_data(timestamps, values)
        ax.plot(timestamps, values, label='CPU Temp °C', color='red')
        ax.set_ylabel('Temperature (°C)')
        ax.set_title('CPU Temperature Over Time')
        ax.set_ylim(30, 70)
    elif metric == 'memory':
        values = [d.get('memory_usage', 0) for d in data]
        if should_downsample:
            timestamps, values = downsample_data(timestamps, values)
        ax.plot(timestamps, values, label='Memory Usage %', color='green')
        ax.set_ylabel('Memory Usage (%)')
        ax.set_title('Memory Usage Over Time')
    elif metric == 'disk':
        disk_data = {}
        for d in data:
            for path, usage in d['disk_usage'].items():
                if usage is not None:
                    disk_data.setdefault(path, []).append(usage)
        for path, values in disk_data.items():
            ts = timestamps[:len(values)]
            if should_downsample:
                ts, values = downsample_data(ts, values)
            ax.plot(ts, values, label=path)
        ax.set_ylabel('Disk Usage (%)')
        ax.set_title('Disk Usage Over Time')
        ax.legend()
    elif metric == 'network':
        net_data = {}
        for d in data:
            for iface, stats in d['network'].items():
                net_data.setdefault(f"{iface}_rx", []).append(stats.get('rx_speed', 0) / 1024 / 1024)
                net_data.setdefault(f"{iface}_tx", []).append(stats.get('tx_speed', 0) / 1024 / 1024)
        for label, values in net_data.items():
            ts = timestamps[:len(values)]
            if should_downsample:
                ts, values = downsample_data(ts, values)
            ax.plot(ts, values, label=label)
        ax.set_ylabel('Speed (MB/s)')
        ax.set_title('Network Speed Over Time')
        ax.legend()
    elif metric == 'diskio':
        io_data = {}
        for d in data:
            for device, stats in d.get('disk_io', {}).items():
                io_data.setdefault(f"{device}_read", []).append(stats.get('read_speed', 0) / 1024 / 1024)
                io_data.setdefault(f"{device}_write", []).append(stats.get('write_speed', 0) / 1024 / 1024)
        for label, values in io_data.items():
            ts = timestamps[:len(values)]
            if should_downsample:
                ts, values = downsample_data(ts, values)
            ax.plot(ts, values, label=label)
        ax.set_ylabel('Speed (MB/s)')
        ax.set_title('Disk I/O Speed Over Time')
        ax.legend()
    
    # Set x-axis formatting
    from matplotlib.dates import HourLocator, MinuteLocator, DateFormatter
    from matplotlib.ticker import MaxNLocator
    
    if hours:
        # For hourly view: show only 6 labels (every 10 minutes for 60 records)
        ax.xaxis.set_major_locator(MaxNLocator(nbins=6))
        ax.xaxis.set_major_formatter(DateFormatter('%H:%M'))
        # Add minor ticks for every minute
        ax.xaxis.set_minor_locator(MinuteLocator(interval=1))
    else:
        # For all records: show labels every 3 hours
        ax.xaxis.set_major_locator(HourLocator(interval=3))
        ax.xaxis.set_major_formatter(DateFormatter('%m-%d\n%H:%M'))
    
    # Start graph at the leftmost data point
    if timestamps:
        ax.set_xlim(left=timestamps[0])
    
    plt.xticks(rotation=0)
    plt.tight_layout()
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    plt.close()
    buf.seek(0)
    return buf.read()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            with open(f'{RESOURCE_DIR}/index.html') as f:
                self.wfile.write(f.read().encode())
        elif self.path == '/style.css':
            self.send_response(200)
            self.send_header('Content-type', 'text/css')
            self.end_headers()
            with open(f'{RESOURCE_DIR}/style.css') as f:
                self.wfile.write(f.read().encode())
        elif self.path.startswith('/all/') or self.path.startswith('/hour/'):
            parts = self.path.split('/')
            view = parts[1]
            metric = parts[2] if len(parts) > 2 else None
            
            if metric in ['cpu', 'temp', 'memory', 'disk', 'network', 'diskio']:
                if view == 'all':
                    img = generate_graph(metric)
                else:
                    img = generate_graph(metric, hours=1)
                
                if img:
                    self.send_response(200)
                    self.send_header('Content-type', 'image/png')
                    self.end_headers()
                    self.wfile.write(img)
                else:
                    self.send_response(404)
                    self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

print(f"Starting web server on port {PORT}")
HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
