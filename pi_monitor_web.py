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
from matplotlib.colors import LinearSegmentedColormap
import matplotlib.colors as mcolors
import numpy as np
import io

# Load config
def load_config():
    try:
        with open('/etc/pi_monitor.json') as f:
            return json.load(f)
    except:
        return {}

config = load_config()

PORT = config.get('web', {}).get('port', 9000)
LOG_FILE = config.get('monitoring', {}).get('log_file', '/var/log/pi_monitor.json')
RESOURCE_DIR = config.get('web', {}).get('resource_dir', '/usr/share/pi_monitor')
PAGE_TITLE = config.get('web', {}).get('title', 'RPi monitoring')


def read_logs(hours=None):
    data = []
    try:
        with open(LOG_FILE) as f:
            for line in f:
                data.append(json.loads(line))
    except:
        pass
    
    try:
        with open('/dev/shm/pi_monitor_buffer.json', 'r') as f:
            data.extend(json.load(f))
    except:
        pass
    
    if hours:
        return data[-60:] if len(data) >= 60 else data
    
    return data


def downsample_data(timestamps, values, max_points=200):
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


def create_gradient_cmap(color):
    h, s, v = mcolors.rgb_to_hsv(mcolors.to_rgb(color))
    s = min(s * 2, 1.0)
    top_color = mcolors.hsv_to_rgb([h, s, v])
    return LinearSegmentedColormap.from_list('grad', ['white', top_color])


def plot_with_gaps(ax, ts, vals, **kwargs):
    if len(ts) < 2:
        line = ax.plot(ts, vals, linewidth=1.5, **kwargs)[0]
        y_min, y_max = ax.get_ylim()
        cmap = create_gradient_cmap(line.get_color())
        for i in range(len(ts)):
            ax.fill_between([ts[i]], [vals[i]], color=cmap(vals[i]/y_max if y_max > 0 else 0), alpha=0.6)
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
        plot_color = line.get_color()
        y_min, y_max = ax.get_ylim()
        cmap = create_gradient_cmap(plot_color)
        
        vals_array = np.array(vals)
        norm_vals = vals_array / y_max if y_max > 0 else vals_array
        colors = [cmap(v) for v in norm_vals]
        
        for i in range(len(ts)-1):
            ax.fill_between(ts[i:i+2], vals[i:i+2], color=colors[i], alpha=0.6, linewidth=0)
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
    cmap = create_gradient_cmap(plot_color)
    
    for seg_idx, (s, e) in enumerate(segments):
        if seg_idx > 0:
            ax.plot(ts[s:e], vals[s:e], linewidth=1.5, color=plot_color, **kwargs)
        
        seg_vals = np.array(vals[s:e])
        norm_vals = seg_vals / y_max if y_max > 0 else seg_vals
        colors = [cmap(v) for v in norm_vals]
        
        for i in range(len(ts[s:e])-1):
            ax.fill_between(ts[s+i:s+i+2], vals[s+i:s+i+2], color=colors[i], alpha=0.6, linewidth=0)
        
        if seg_idx < len(segments) - 1:
            prev_e = e
            next_s = segments[seg_idx + 1][0]
            gap_ts = [ts[prev_e-1], ts[next_s]]
            gap_vals = [vals[prev_e-1], vals[next_s]]
            ax.plot(gap_ts, gap_vals, linestyle=':', linewidth=1.5, color=plot_color)
            
            gap_norm = [gap_vals[0]/y_max if y_max > 0 else 0, gap_vals[1]/y_max if y_max > 0 else 0]
            gap_color = cmap(sum(gap_norm)/2)
            ax.fill_between(gap_ts, gap_vals, color=gap_color, alpha=0.3, linewidth=0)


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
                net_data.setdefault(f{iface}_rx, []).append(stats.get('rx_speed', 0) / 1024 / 1024)
                net_data.setdefault(f{iface}_tx, []).append(stats.get('tx_speed', 0) / 1024 / 1024)
        
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
                io_data.setdefault(f{device}_read, []).append(stats.get('read_count', 0))
                io_data.setdefault(f{device}_write, []).append(stats.get('write_count', 0))
        
        all_values = [v for values in io_data.values() for v in values]
        self.validate_limits(max(all_values) if all_values else 0)
        
        for label, values in io_data.items():
            ts = timestamps[:len(values)]
            if should_downsample:
                ts, values = downsample_data(ts, values)
            plot_with_gaps(ax, ts, values, label=label)
        ax.legend(loc='upper right')


graphs = {
    'cpu': CPUGraph(config.get('metrics', {}).get('cpu', {})),
    'temp': TempGraph(config.get('metrics', {}).get('temp', {})),
    'memory': MemoryGraph(config.get('metrics', {}).get('memory', {})),
    'disk': DiskGraph(config.get('metrics', {}).get('disk', {})),
    'network': NetworkGraph(config.get('metrics', {}).get('network', {})),
    'diskio': DiskIOGraph(config.get('metrics', {}).get('diskio', {}))
}


def generate_graph(metric, hours=None, mobile=False):
    try:
        data = read_logs(hours=hours)
        if not data:
            return None
    
        timestamps = [datetime.fromisoformat(d['timestamp']) for d in data]
        
        figsize = (12, 5) if mobile else (24, 6)
        fig, ax = plt.subplots(figsize=figsize)
        
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
        ax.set_ylabel(graph.ylabel)
        ax.set_title(graph.title)
        graph.set_limits(ax)
        
        from matplotlib.dates import HourLocator, MinuteLocator, DateFormatter
        from matplotlib.ticker import MaxNLocator
        
        if hours:
            ax.xaxis.set_major_locator(MinuteLocator(interval=10))
            ax.xaxis.set_major_formatter(DateFormatter('%H:%M'))
            ax.xaxis.set_minor_locator(MinuteLocator(interval=1))
        else:
            ax.xaxis.set_major_locator(HourLocator(byhour=range(0,24,3)))
            ax.xaxis.set_major_formatter(DateFormatter('%m-%d\n%H:%M'))
            ax.xaxis.set_minor_locator(HourLocator(byhour=range(0,24,1)))
        
        if timestamps:
            ax.set_xlim(left=timestamps[0])
        
        plt.xticks(rotation=0)
        plt.tight_layout()
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
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
                img = generate_graph(metric, hours=1 if view == 'hour' else None, mobile=mobile)
                
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
