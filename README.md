# Pi Monitor

Raspberry Pi system monitoring with web interface and historical data tracking.

## Features

- CPU usage and temperature monitoring
- Memory usage tracking
- Disk usage for multiple mount points
- Network traffic speed (per interface)
- Disk I/O monitoring (read/write speeds)
- Web interface with graphs (hourly and historical views)
- 7-day rolling data retention
- Configurable monitoring parameters

## Installation

```bash
git clone https://github.com/tp3t3r/pi_monitor.git
cd pi_monitor
chmod +x install.sh
./install.sh
```

## Configuration

Edit `/etc/pi_monitor.conf` to customize:
- Monitoring interval
- Data retention period
- Disk paths to monitor
- Network interfaces to monitor
- Disk devices for I/O monitoring
- Web server port

After changing configuration:
```bash
sudo systemctl restart pi-monitor pi-monitor-web
```

## Usage

**Web Interface:** http://your-pi-ip:9000

**CLI Status:** `pi_status`

**Services:**
- `pi-monitor.service` - Data collection daemon
- `pi-monitor-web.service` - Web interface server

## Requirements

- Python 3
- matplotlib
- systemd
