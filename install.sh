#!/bin/bash
# Pi Monitor Installation Script

set -e

echo "Installing Pi Monitor..."

# Copy scripts
sudo cp pi_monitor.py pi_monitor_web.py pi_status /usr/bin/
sudo chmod +x /usr/bin/pi_monitor.py /usr/bin/pi_monitor_web.py /usr/bin/pi_status

# Copy configuration
sudo cp pi_monitor.conf /etc/

# Copy systemd services
sudo cp pi-monitor.service pi-monitor-web.service /etc/systemd/system/

# Copy resources
sudo mkdir -p /usr/share/pi_monitor
sudo cp resources/* /usr/share/pi_monitor/

# Create log file with proper permissions
sudo touch /opt/tmp/pi_monitor.json
sudo chown peter:peter /opt/tmp/pi_monitor.json

# Reload and restart services
sudo systemctl daemon-reload
sudo systemctl enable pi-monitor pi-monitor-web
sudo systemctl restart pi-monitor pi-monitor-web

echo "Pi Monitor installed successfully!"
echo "Web interface: http://$(hostname -I | awk '{print $1}'):9000"
echo "CLI command: pi_status"
