#!/usr/bin/env python3

import os
import sys
import json
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
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

# Y-axis limits for graphs
YLIM_CPU = config.get('metrics', {}).get('cpu', {}).get('graph_limits', [0, 100])
YLIM_TEMP = config.get('metrics', {}).get('temp', {}).get('graph_limits', [30, 70])
YLIM_MEMORY = config.get('metrics', {}).get('memory', {}).get('graph_limits', [0, 100])
YLIM_DISK = config.get('metrics', {}).get('disk', {}).get('graph_limits', [0, 100])
YLIM_NETWORK = config.get('metrics', {}).get('network', {}).get('graph_limits', [0, 500])
YLIM_DISKIO = config.get('metrics', {}).get('diskio', {}).get('graph_limits', [0, 2000])

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
        # For hourly view, show last 60 records (1 hour at 1-min intervals)
        # This ensures we show data even if there's a gap/restart
        return data[-60:] if len(data) >= 60 else data
    
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
    def plot_with_gaps(ax, ts, vals, **kwargs):
        """Plot data with dotted lines across gaps"""
        # Detect gaps in the actual data being plotted
        # Use median interval to handle varying sample rates
        if len(ts) < 2:
            ax.plot(ts, vals, linewidth=1.5, **kwargs)
            return
            
        intervals = [(ts[i] - ts[i-1]).total_seconds() for i in range(1, len(ts))]
        intervals.sort()
        median_interval = intervals[len(intervals) // 2]
        gap_threshold = max(median_interval * 3, 300)  # At least 5 minutes
        
        gaps = set()
        for i in range(1, len(ts)):
            delta = (ts[i] - ts[i-1]).total_seconds()
            if delta > gap_threshold:
                gaps.add(i)
        
        if not gaps:
            ax.plot(ts, vals, linewidth=1.5, **kwargs)
            return
        
        # Split into segments
        segments = []
        start = 0
        for gap_idx in sorted(gaps):
            if gap_idx > start:
                segments.append((start, gap_idx))
            start = gap_idx
        if start < len(ts):
            segments.append((start, len(ts)))
        
        # Plot segments with consistent color
        label = kwargs.pop('label', None)
        color = kwargs.pop('color', None)
        
        # Plot first segment to get color
        s, e = segments[0]
        line = ax.plot(ts[s:e], vals[s:e], linewidth=1.5, label=label, color=color, **kwargs)[0]
        plot_color = line.get_color()
        
        # Plot remaining segments with same color
        for i in range(1, len(segments)):
            s, e = segments[i]
            ax.plot(ts[s:e], vals[s:e], linewidth=1.5, color=plot_color, **kwargs)
            
            # Add dotted line across gap
            prev_e = segments[i-1][1]
            ax.plot([ts[prev_e-1], ts[s]], [vals[prev_e-1], vals[s]], 
                   linestyle=':', linewidth=1.5, color=plot_color)
    
    try:
        data = read_logs(limit=limit, hours=hours)
        if not data:
            print(f"DEBUG: No data for metric={metric}, limit={limit}, hours={hours}", file=sys.stderr)
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
            plot_with_gaps(ax, timestamps, values, label='CPU Usage %')
            ax.set_ylabel('CPU Usage (%)')
            ax.set_title('CPU Usage Over Time')
            ax.set_ylim(*YLIM_CPU)
        elif metric == 'temp':
            values = [d['cpu_temp'] for d in data]
            if should_downsample:
                timestamps, values = downsample_data(timestamps, values)
            plot_with_gaps(ax, timestamps, values, label='CPU Temp °C', color='red')
            ax.set_ylabel('Temperature (°C)')
            ax.set_title('CPU Temperature Over Time')
            ax.set_ylim(*YLIM_TEMP)
        elif metric == 'memory':
            values = [d.get('memory_usage', 0) for d in data]
            if should_downsample:
                timestamps, values = downsample_data(timestamps, values)
            plot_with_gaps(ax, timestamps, values, label='Memory Usage %', color='green')
            ax.set_ylabel('Memory Usage (%)')
            ax.set_title('Memory Usage Over Time')
            ax.set_ylim(*YLIM_MEMORY)
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
                plot_with_gaps(ax, ts, values, label=path)
            ax.set_ylabel('Disk Usage (%)')
            ax.set_title('Disk Usage Over Time')
            ax.set_ylim(*YLIM_DISK)
            ax.legend(loc='upper right')
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
                plot_with_gaps(ax, ts, values, label=label)
            ax.set_ylabel('Speed (MB/s)')
            ax.set_title('Network Speed Over Time')
            ax.set_ylim(*YLIM_NETWORK)
            ax.legend(loc='upper right')
        elif metric == 'diskio':
            io_data = {}
            for d in data:
                for device, stats in d.get('disk_io', {}).items():
                    io_data.setdefault(f"{device}_read", []).append(stats.get('read_count', 0))
                    io_data.setdefault(f"{device}_write", []).append(stats.get('write_count', 0))
            for label, values in io_data.items():
                ts = timestamps[:len(values)]
                if should_downsample:
                    ts, values = downsample_data(ts, values)
                plot_with_gaps(ax, ts, values, label=label)
            ax.set_ylabel('Operations per minute (#)')
            ax.set_title('Disk I/O Operations Over Time')
            ax.set_ylim(*YLIM_DISKIO)
            ax.legend(loc='upper right')
        
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
    
    except Exception as e:
        print(f"ERROR generating graph for {metric}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return None

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Override to add more detail
        sys.stderr.write(f"{self.address_string()} - {format%args}\n")
    
    def do_GET(self):
        print(f"DEBUG: Received request for {self.path}", file=sys.stderr)
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
            print(f"DEBUG: view={view}, metric={metric}, valid={metric in ['cpu', 'temp', 'memory', 'disk', 'network', 'diskio']}", file=sys.stderr)
            
            if metric in ['cpu', 'temp', 'memory', 'disk', 'network', 'diskio']:
                if view == 'all':
                    img = generate_graph(metric)
                else:
                    img = generate_graph(metric, hours=1)
                
                print(f"DEBUG: Generated image: {len(img) if img else 0} bytes", file=sys.stderr)
                
                if img:
                    self.send_response(200)
                    self.send_header('Content-type', 'image/png')
                    self.end_headers()
                    self.wfile.write(img)
                else:
                    print(f"DEBUG: No image generated, sending 404", file=sys.stderr)
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
