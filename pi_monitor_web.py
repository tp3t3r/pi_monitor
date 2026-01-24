#!/usr/bin/env python3

import os
import json
import sys
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from abc import ABC, abstractmethod
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np
import io
import time

# Load config
def load_config():
    try:
        with open('/etc/pi_monitor.json') as f:
            return json.load(f)
    except:
        return {}

config = load_config()

PORT = config.get('web', {}).get('port', 9000)
DATA_FILE = config.get('monitoring', {}).get('data_file', '/opt/tmp/collected_data.json')
INTERVAL = config.get("monitoring", {}).get("interval", 60)
RESOURCE_DIR = config.get('web', {}).get('resource_dir', '/usr/share/pi_monitor')
PAGE_TITLE = config.get('web', {}).get('title', 'RPi monitoring')
LISTEN_ADDR = config.get("web", {}).get("listen")
MAX_POINTS = config.get("web", {}).get("max_points")


def read_logs(hours=None):
    data = []
    max_lines = 100 if hours else 1000
    
    try:
        with open(DATA_FILE, "r") as f:
            all_lines = f.readlines()
            for line in all_lines[-max_lines:]:
                if line.strip():
                    data.append(json.loads(line))
    except:
        pass
    
    try:
        with open("/dev/shm/pi_monitor_buffer.json", "r") as f:
            data.extend(json.load(f))
    except:
        pass
    
    if hours:
        now = datetime.now()
        cutoff = now - timedelta(hours=1)
        filtered = [d for d in data if datetime.fromisoformat(d['timestamp']) >= cutoff]
        
        minute_data = {}
        for d in filtered:
            ts = datetime.fromisoformat(d['timestamp'])
            minute_key = ts.replace(second=0, microsecond=0)
            if minute_key not in minute_data:
                minute_data[minute_key] = d
        
        return [minute_data[k] for k in sorted(minute_data.keys())]
    
    return data


def downsample_data(timestamps, values, max_points=MAX_POINTS):
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



def plot_with_gaps(ax, ts, vals, **kwargs):
    if len(ts) < 2:
        line = ax.plot(ts, vals, linewidth=1.5, **kwargs)[0]
        y_min, y_max = ax.get_ylim()
        
        for i in range(len(ts)):
            ax.fill_between([ts[i]], [vals[i]], color=line.get_color(), alpha=0.2)
        return
        
    intervals = [(ts[i] - ts[i-1]).total_seconds() for i in range(1, len(ts))]
    intervals.sort()
    median_interval = intervals[len(intervals) // 2]
    gap_threshold = max(median_interval * 3, 300)
    
    gaps = set()
    for i in range(1, len(ts)):
        delta = (ts[i] - ts[i-1]).total_seconds()
        if delta > gap_threshold:
            gaps.add(i)
    
    label = kwargs.pop('label', None)
    color = kwargs.pop('color', None)
    
    if not gaps:
        line = ax.plot(ts, vals, linewidth=1.5, label=label, color=color, **kwargs)[0]
        ax.fill_between(ts, vals, color=line.get_color(), alpha=0.2)
        return
    
    segments = []
    start = 0
    for gap_idx in sorted(gaps):
        if gap_idx > start:
            segments.append((start, gap_idx))
        start = gap_idx
    if start < len(ts):
        segments.append((start, len(ts)))
    
    s, e = segments[0]
    line = ax.plot(ts[s:e], vals[s:e], linewidth=1.5, label=label, color=color, **kwargs)[0]
    plot_color = line.get_color()
    y_min, y_max = ax.get_ylim()
    
    
    for seg_idx, (s, e) in enumerate(segments):
        if seg_idx > 0:
            ax.plot(ts[s:e], vals[s:e], linewidth=1.5, color=plot_color, **kwargs)
        
        ax.fill_between(ts[s:e], vals[s:e], color=plot_color, alpha=0.2)
        
        if seg_idx < len(segments) - 1:
            prev_e = e
            next_s = segments[seg_idx + 1][0]
            gap_ts = [ts[prev_e-1], ts[next_s]]
            gap_vals = [vals[prev_e-1], vals[next_s]]
            ax.plot(gap_ts, gap_vals, linestyle=':', linewidth=1.5, color=plot_color)
            # Fill gap with 30% saturation
            rgb = mcolors.to_rgb(plot_color)
            h, s, v = mcolors.rgb_to_hsv(rgb)
            light_color = mcolors.hsv_to_rgb([h, s * 0.3, v])
            ax.fill_between(gap_ts, gap_vals, color=light_color, alpha=0.3)
            


class MetricGraph(ABC):
    def __init__(self, config):
        self.config = config
        self.limits = config.get('graph_limits', [0, 100])
        self.ylabel = ''
        self.title = ''
    
    @abstractmethod
    def plot(self, ax, data, timestamps, should_downsample):
        pass
    
    def validate_limits(self, data_max):
        if data_max > self.limits[1]:
            self.limits[1] = data_max * 1.1
    
    def set_limits(self, ax):
        ax.set_ylim(*self.limits)


class CPUGraph(MetricGraph):
    def __init__(self, config):
        super().__init__(config)
        self.ylabel = 'CPU Usage (%)'
        self.title = 'CPU Usage Over Time'
    
    def plot(self, ax, data, timestamps, should_downsample):
        values = [d['cpu_usage'] for d in data]
        if should_downsample:
            timestamps, values = downsample_data(timestamps, values)
        self.validate_limits(max(values) if values else 0)
        plot_with_gaps(ax, timestamps, values, label='CPU Usage %')


class TempGraph(MetricGraph):
    def __init__(self, config):
        super().__init__(config)
        self.ylabel = 'Temperature (°C)'
        self.title = 'CPU Temperature Over Time'
    
    def plot(self, ax, data, timestamps, should_downsample):
        values = [d['cpu_temp'] for d in data]
        if should_downsample:
            timestamps, values = downsample_data(timestamps, values)
        self.validate_limits(max(values) if values else 0)
        plot_with_gaps(ax, timestamps, values, label='CPU Temp °C', color='red')


class MemoryGraph(MetricGraph):
    def __init__(self, config):
        super().__init__(config)
        self.ylabel = 'Memory Usage (%)'
        self.title = 'Memory Usage Over Time'
    
    def plot(self, ax, data, timestamps, should_downsample):
        values = [d.get('memory_usage', 0) for d in data]
        if should_downsample:
            timestamps, values = downsample_data(timestamps, values)
        self.validate_limits(max(values) if values else 0)
        plot_with_gaps(ax, timestamps, values, label='Memory Usage %', color='green')


class DiskGraph(MetricGraph):
    def __init__(self, config):
        super().__init__(config)
        self.ylabel = 'Disk Usage (%)'
        self.title = 'Disk Usage Over Time'
    
    def plot(self, ax, data, timestamps, should_downsample):
        disk_data = {}
        for d in data:
            for path, usage in d['disk_usage'].items():
                if usage is not None:
                    disk_data.setdefault(path, []).append(usage)
        
        all_values = [v for values in disk_data.values() for v in values]
        self.validate_limits(max(all_values) if all_values else 0)
        
        for path, values in disk_data.items():
            ts = timestamps[:len(values)]
            if should_downsample:
                ts, values = downsample_data(ts, values)
            plot_with_gaps(ax, ts, values, label=path)
        ax.legend(loc='upper right')


class NetworkGraph(MetricGraph):
    def __init__(self, config):
        super().__init__(config)
        self.ylabel = 'Speed (MB/s)'
        self.title = 'Network Speed Over Time'
    
    def plot(self, ax, data, timestamps, should_downsample):
        net_data = {}
        for d in data:
            for iface, stats in d['network'].items():
                net_data.setdefault(f"{iface}_rx", []).append(stats.get('rx_speed', 0) / 1024 / 1024)
                net_data.setdefault(f"{iface}_tx", []).append(stats.get('tx_speed', 0) / 1024 / 1024)
        
        all_values = [v for values in net_data.values() for v in values]
        self.validate_limits(max(all_values) if all_values else 0)
        
        for label, values in net_data.items():
            ts = timestamps[:len(values)]
            if should_downsample:
                ts, values = downsample_data(ts, values)
            plot_with_gaps(ax, ts, values, label=label)
        ax.legend(loc='upper right')


class DiskIOGraph(MetricGraph):
    def __init__(self, config):
        super().__init__(config)
        self.ylabel = 'Operations per minute (#)'
        self.title = 'Disk I/O Operations Over Time'
    
    def plot(self, ax, data, timestamps, should_downsample):
        io_data = {}
        for d in data:
            for device, stats in d.get('disk_io', {}).items():
                io_data.setdefault(f"{device}_read", []).append(stats.get('read_count', 0))
                io_data.setdefault(f"{device}_write", []).append(stats.get('write_count', 0))
        
        all_values = [v for values in io_data.values() for v in values]
        self.validate_limits(max(all_values) if all_values else 0)
        
        for label, values in io_data.items():
            ts = timestamps[:len(values)]
            if should_downsample:
                ts, values = downsample_data(ts, values)
            plot_with_gaps(ax, ts, values, label=label)
        ax.legend(loc='upper right')



class GraphCache:
    def __init__(self, interval):
        self.interval = interval
        self.cache = {}
        self.timestamp = 0

    def is_expired(self):
        return time.time() - self.timestamp > self.interval

    def get(self, key):
        if self.is_expired():
            return None
        return self.cache.get(key)

    def set_all(self, data):
        self.cache = data
        self.timestamp = time.time()

graphs = {
    'cpu': CPUGraph(config.get('metrics', {}).get('cpu', {})),
    'temp': TempGraph(config.get('metrics', {}).get('temp', {})),
    'memory': MemoryGraph(config.get('metrics', {}).get('memory', {})),
    'disk': DiskGraph(config.get('metrics', {}).get('disk', {})),
    'network': NetworkGraph(config.get('metrics', {}).get('network', {})),
    'diskio': DiskIOGraph(config.get('metrics', {}).get('diskio', {}))
}



def generate_all_graphs(mobile=False):
    result = {}
    for view in ["hour", "all"]:
        for metric in graphs.keys():
            hours = 1 if view == "hour" else None
            img = generate_graph(metric, hours=hours, mobile=mobile)
            if img:
                result[f"{view}/{metric}"] = img
    return result

cache = GraphCache(INTERVAL)

def generate_graph(metric, hours=None, mobile=False):
    try:
        data = read_logs(hours=hours)
        if not data:
            return None
    
        timestamps = [datetime.fromisoformat(d['timestamp']) for d in data]
        
        figsize = (12, 5) if mobile else (18, 4.5)
        fig, ax = plt.subplots(figsize=figsize)
        fig.patch.set_facecolor("#1a1a1a")
        ax.set_facecolor("#1a1a1a")
        ax.spines["bottom"].set_color("#666")
        ax.spines["top"].set_color("#666")
        ax.spines["left"].set_color("#666")
        ax.spines["right"].set_color("#666")
        ax.tick_params(colors="#e0e0e0")
        ax.xaxis.label.set_color("#e0e0e0")
        ax.yaxis.label.set_color("#e0e0e0")
        ax.title.set_color("#ffffff")
        
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
        
        should_downsample = not hours
        
        graph = graphs.get(metric)
        if not graph:
            return None
        
        graph.plot(ax, data, timestamps, should_downsample)
        graph.set_limits(ax)
        
        legend = ax.get_legend()
        if legend:
            legend.get_frame().set_facecolor("#2a2a2a")
            legend.get_frame().set_edgecolor("#666")
            for text in legend.get_texts():
                text.set_color("#e0e0e0")
        
        from matplotlib.dates import HourLocator, MinuteLocator, DateFormatter
        from matplotlib.ticker import MaxNLocator
        
        if hours:
            ax.xaxis.set_major_locator(MaxNLocator(nbins=6))
            ax.xaxis.set_major_formatter(DateFormatter('%H:%M'))
        else:
            ax.xaxis.set_major_locator(MaxNLocator(nbins=10))
            ax.xaxis.set_major_formatter(DateFormatter('%m-%d\n%H:%M'))
        
        if timestamps:
            if hours:
                now = datetime.now()
                ax.set_xlim(left=now - timedelta(hours=1), right=now)
            else:
                ax.set_xlim(left=timestamps[0])
        
        plt.xticks(rotation=0)
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
        plt.close()
        buf.seek(0)
        return buf.read()
    
    except Exception as e:
        print(f"ERROR generating graph for {metric}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            pass
    
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
        elif self.path == '/uptime':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            with open('/proc/uptime') as f:
                uptime_seconds = int(float(f.read().split()[0]))
            days = uptime_seconds // 86400
            hours = (uptime_seconds % 86400) // 3600
            minutes = (uptime_seconds % 3600) // 60
            uptime_str = f"{days}d {hours}h {minutes}m"
            self.wfile.write(uptime_str.encode())
        elif self.path == '/info':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            with open('/proc/uptime') as f:
                uptime_seconds = int(float(f.read().split()[0]))
            days = uptime_seconds // 86400
            hours = (uptime_seconds % 86400) // 3600
            minutes = (uptime_seconds % 3600) // 60
            uptime_str = f"{days}d {hours}h {minutes}m"
            self.wfile.write(json.dumps({'uptime': uptime_str}).encode())
        elif self.path == '/config':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            config_data = {'title': PAGE_TITLE}
            self.wfile.write(json.dumps(config_data).encode())
        elif self.path.startswith('/all/') or self.path.startswith('/hour/'):
            parts = self.path.split('/')
            view = parts[1]
            metric = parts[2] if len(parts) > 2 else None
            
            user_agent = self.headers.get('User-Agent', '').lower()
            mobile = any(x in user_agent for x in ['mobile', 'android', 'iphone', 'ipad'])
            
            if metric in graphs:
                cache_key = f"{view}/{ metric}"

                img = cache.get(cache_key)
                if not img:
                    all_graphs = generate_all_graphs(mobile)
                    cache.set_all(all_graphs)
                    img = all_graphs.get(cache_key)
                
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
print(f"Starting web server on port {PORT}")
HTTPServer((LISTEN_ADDR, PORT), Handler).serve_forever()
