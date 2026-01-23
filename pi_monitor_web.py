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
LOG_FILE = config.get('monitoring', {}).get('log_file', '/opt/tmp/collected_data.json')
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
    label = kwargs.pop('label', None)
    color = kwargs.pop('color', None)
    line = ax.plot(ts, vals, linewidth=1.5, label=label, color=color, **kwargs)[0]
    ax.fill_between(ts, vals, color=line.get_color(), alpha=0.2)


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
        ax.set_ylabel(graph.ylabel)
        ax.set_title(graph.title)
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
            ax.xaxis.set_major_locator(MinuteLocator(byminute=[0,10,20,30,40,50]))
            ax.xaxis.set_major_formatter(DateFormatter('%H:%M'))
            ax.xaxis.set_minor_locator(MinuteLocator(byminute=range(0,60,1)))
        else:
            ax.xaxis.set_major_locator(HourLocator(byhour=range(0,24,3)))
            ax.xaxis.set_major_formatter(DateFormatter('%m-%d\n%H:%M'))
            ax.xaxis.set_minor_locator(HourLocator(byhour=range(0,24,1)))
        
        if timestamps:
            if hours:
                # Round down to previous :00 minute
                start_time = timestamps[0].replace(second=0, microsecond=0)
                start_time = start_time.replace(minute=(start_time.minute // 10) * 10)
                ax.set_xlim(left=start_time)
            else:
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
