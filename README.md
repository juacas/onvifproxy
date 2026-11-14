![ONVIF Proxy Logo](./logo.png)

# ONVIF Proxy - PTZ Enabler for TAPO Cameras

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-blue)](https://www.python.org)
[![Docker Support](https://img.shields.io/badge/Docker-Supported-2496ED?logo=docker)](https://www.docker.com)
[![Frigate Integration](https://img.shields.io/badge/Frigate-Compatible-FF6B35)](https://frigate.video)
[![ONVIF Standard](https://img.shields.io/badge/ONVIF-Compliant-00A0DF)](https://www.onvif.org)

## 🎯 Overview

**ONVIF Proxy** is a solution that empowers TAPO C210 and C211 cameras with complete **PTZ (Pan-Tilt-Zoom)** support for seamless integration with [Frigate's](https://frigate.video) autotracking feature.

### 🔴 The Problem

TAPO C210/C211 cameras expose limited ONVIF PTZ capabilities through their native implementation:
- **Incomplete ONVIF Support**: The cameras don't fully implement ONVIF PTZ standards
- **No Diagonal Movement**: Pan and tilt often execute sequentially, not simultaneously
- **FOV Inconsistency**: Limited field-of-view (FOV) calculations for proper autotracking
- **Frigate Incompatibility**: Frigate's autotracking expects full ONVIF PTZ compliance, which TAPO cameras can't provide natively

### ✅ The Solution

ONVIF Proxy acts as an **intelligent middleware** that:
1. **Emulates complete ONVIF PTZ support** with proper diagonal movement capability
2. **Calibrates FOV calculations** based on actual camera motion characteristics
3. **Translates Frigate's PTZ commands** into TAPO-compatible ONVIF requests
4. **Provides web-based calibration tools** for precise motor speed and rotation measurements
5. **Enables true autotracking** by making TAPO cameras appear as standard ONVIF PTZ devices

## 🚀 Features

- ✨ **Full ONVIF PTZ Emulation** - Complete PTZ protocol support for Frigate autotracking
- 📊 **Automatic FOV Calibration** - Web interface to calibrate pan/tilt speeds and field of view
- 🎯 **Optical Calibration** - ML-based calibration using fixed reference points
- 🔒 **Secure Authentication** - WS-Security SOAP authentication with credential protection
- 🐳 **Docker Ready** - Pre-configured Docker & Docker Compose setup
- 🔧 **Sequential Axis Support** - Handles cameras that can't move pan and tilt simultaneously
- 📡 **Real-time PTZ Status** - Mirrors camera position from GetStatus commands
- 🌐 **Multi-Camera Support** - Configure and manage multiple TAPO cameras

## 📋 Requirements

- **Docker** & **Docker Compose** (recommended)
  - OR Python 3.8+ with required packages
- **TAPO C210/C211** camera with ONVIF support
- **Network connectivity** between proxy host and camera
- **Frigate** (for autotracking feature)

### Optional Dependencies

- **NumPy, SciPy** - For optical calibration features
- **PyYAML** - For camera configuration

## 🔗 Integration with Frigate

ONVIF Proxy acts as a transparent ONVIF PTZ endpoint for Frigate. See the [Frigate PTZ Documentation](https://docs.frigate.video/configuration/cameras/#ptz) for detailed integration instructions.

### Key Concepts from Frigate

- **Autotracking**: Automatic camera movement to follow detected objects
- **PTZ API**: RESTful interface for programmatic camera control
- **ONVIF Support**: Standard protocol for IP camera communication

## 📦 Installation & Setup

### Option 1: Docker Compose (Recommended)

1. Clone the repository:
```bash
git clone https://github.com/juacas/onvifproxy.git
cd onvifproxy
```

2. Edit `cameras.yaml` with your camera details:
```yaml
cameras:
  c210:
    proxy_port: 8210
    ip: "192.168.1.100"      # Your C210 IP address
    onvif_port: 2020
    pan_seconds: 1.7          # Calibrate: seconds for full horizontal FOV
    tilt_seconds: 1.7         # Calibrate: seconds for full vertical FOV
    speed: 0.8                # Motor speed (0.1 to 1.0)
    sequential_axes: false    # Set to true if camera can't do diagonal moves
    snapshot_url: "http://frigate.local:5000/api/c210/latest.jpg"
```

3. Start the proxy:
```bash
docker compose up -d
```

The proxy will be available at:
- ONVIF PTZ endpoint: `http://localhost:8210/onvif/service` (for C210)
- Web calibration UI: `http://localhost:8210/calibration`

### Option 2: Manual Python Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Configure your cameras
# Edit cameras.yaml with your camera details

# Run the proxy
python3 onvif_fov_proxy.py
```

## ⚙️ Configuration

### Optical Calibration (Advanced)

For more precise results using a fixed reference point:

1. Move the camera to 8+ different positions
2. Mark the same fixed distant point in each frame
3. The system calculates exact FOV and rotation angles
4. Results are displayed but don't auto-update the config

## 🎮 Using with Frigate

### Configure PTZ Camera in Frigate

Edit your `frigate/config.yml`:

```yaml
cameras:
  bedroom:
    ffmpeg:
      inputs:
        - path: rtsp://camera-user:password@192.168.1.100/stream/profile/0
          roles:
            - detect
            - record
    
    # PTZ Configuration pointing to the ONVIF Proxy
    ptz:
      autotrack:
        enabled: true
        track_threshold: 0.5
      
      # Point to the ONVIF Proxy instead of the camera directly
      onvif:
        host: onvif-proxy  # or your proxy IP/hostname
        port: 8210         # Port configured in docker-compose
        user: camera_user  # Your TAPO camera credentials
        password: camera_password
```

### Enable Autotracking

Once configured, Frigate will:
1. Detect moving objects
2. Calculate where in the image the object appears
3. Send ONVIF PTZ commands to the proxy
4. The proxy translates and sends calibrated commands to your TAPO camera
5. The camera moves to keep the object centered

### Quick Verification - Test Before Deep Calibration

**🚀 Good news**: The default values in `cameras.yaml` often work out-of-the-box! Try them first before spending time on detailed calibration.

#### Step 1: Test Basic PTZ Response

1. Start the proxy: `docker compose up -d`
2. Reconfigure Frigate with the ONVIF proxy endpoint
3. In Frigate's web interface, you should see a **PTZ crosshair icon** next to the camera
4. **Click anywhere in the video stream** on a specific object
5. Observe if the camera moves towards the clicked position

#### Step 2: Verify Centering

- If the clicked object appears roughly **in the center of the view** after the camera moves, calibration is **already good enough** for practical use!
- Minor off-center positioning is acceptable for autotracking

#### Step 3: Fine-Tune by Trial and Error (If Needed)

If the camera overshoots or undershoots:

1. **Edit `cameras.yaml`** - Adjust calibration values:
   ```yaml
   cameras:
     c210:
       pan_seconds: 1.7    # Increase if panning too fast, decrease if too slow
       tilt_seconds: 1.7   # Increase if tilting too fast, decrease if too slow
   ```

2. **Restart the proxy** - Apply changes immediately:
   ```bash
   docker compose restart onvif-fov-proxy
   ```

3. **Test again** - Click the same object in Frigate and observe the result

4. **Iterate** - Repeat steps 1-3 until satisfied (usually 1-3 attempts)

**💡 Pro tip**: Change values in **0.1 second increments** for fine adjustments. The current calibration is visible in the proxy logs if needed.

## ⚙️ Calibrating Pan/Tilt Speed

Each camera needs precise calibration to provide accurate autotracking:

1. **Open the web calibration interface**: `http://localhost:8210/calibration`
2. **Manual Calibration**:
   - Send a single-axis movement for 0.5 seconds
   - Measure what fraction of the video width/height the camera panned/tilted
   - Use the formula: `seconds_per_fov = movement_seconds / fov_fraction`

3. **Update `cameras.yaml`**:
```yaml
cameras:
  c210:
    pan_seconds: 1.7   # Adjust based on calibration
    tilt_seconds: 1.7  # Adjust based on calibration
    speed: 0.8
```

## 🧪 Testing & Diagnostics

### Probe Camera Capabilities

```bash
python3 calibrate_ptz.py \
  --url http://192.168.1.100:2020/onvif/service \
  --user camera_user \
  probe
```

### Test Pan/Tilt Movements

```bash
# Pan only
python3 calibrate_ptz.py \
  --url http://192.168.1.100:2020/onvif/service \
  --user camera_user \
  move --x -0.8 --y 0 --seconds 0.5

# Tilt only
python3 calibrate_ptz.py \
  --url http://192.168.1.100:2020/onvif/service \
  --user camera_user \
  move --x 0 --y -0.8 --seconds 0.5

# Diagonal movement
python3 calibrate_ptz.py \
  --url http://192.168.1.100:2020/onvif/service \
  --user camera_user \
  move --x -0.8 --y -0.8 --seconds 0.88
```

### FOV Verification

```bash
python3 calibrate_ptz.py \
  --url http://192.168.1.100:2020/onvif/service \
  --user camera_user \
  fov --camera c210 --x -0.03 --y -0.97
```

### Inside Docker

```bash
docker compose exec -it onvif-fov-proxy python /app/calibrate_ptz.py \
  --url http://c210.local:2020/onvif/service \
  --user camera_user \
  probe
```

## 📊 System Architecture

```mermaid
graph TB
    Frigate["🎬 Frigate<br/>Video Analytics"]
    FrigateAPI["Frigate PTZ API"]
    Proxy["📡 ONVIF Proxy<br/>PTZ Emulator"]
    Camera["📹 TAPO C210/C211<br/>Camera"]
    
    Frigate -->|Autotrack Event| FrigateAPI
    FrigateAPI -->|ONVIF Commands<br/>ContinuousMove, RelativeMove| Proxy
    
    Proxy -->|Calibrated Commands| Camera
    Camera -->|GetStatus, Capabilities| Proxy
    Proxy -->|Status Response| FrigateAPI
    
    WebUI["🌐 Web Calibration UI<br/>Snapshot & Control"]
    WebUI -->|Configure Motor Speeds| Proxy
    WebUI -->|GetStatus Queries| Camera
    Camera -->|JPEG Snapshots| WebUI
    
    Config["⚙️ cameras.yaml<br/>Pan/Tilt Calibration"]
    Config -->|Motor Parameters| Proxy
    
    style Frigate fill:#FF6B35
    style Proxy fill:#0066CC
    style Camera fill:#FF9900
    style WebUI fill:#00CC99
    style FrigateAPI fill:#FF8855
    style Config fill:#666
```

## 📝 Configuration Reference

### cameras.yaml Structure

```yaml
cameras:
  c210:
    # Network Configuration
    proxy_port: 8210              # Port for this camera's ONVIF endpoint
    ip: "192.168.1.100"          # Camera IP address
    onvif_port: 2020             # TAPO ONVIF port (usually 2020)
    
    # Calibration Parameters (critical for autotracking accuracy)
    pan_seconds: 1.7             # Seconds to pan full field of view
    tilt_seconds: 1.7            # Seconds to tilt full field of view
    speed: 0.8                   # Motor speed multiplier (0.1-1.0)
    
    # Movement Behavior
    sequential_axes: false       # true = pan then tilt; false = simultaneous
    
    # Optional: Web Calibration Interface
    snapshot_url: "http://frigate.local:5000/api/c210/latest.jpg"
    ptz_path: "/onvif/service"   # ONVIF service path on camera
```

## 🛠️ Troubleshooting

### Camera Not Responding

1. **Verify network connectivity**:
   ```bash
   ping 192.168.1.100
   ```

2. **Check ONVIF port is open**:
   ```bash
   curl -i http://192.168.1.100:2020/onvif/service
   ```

3. **Test proxy connection**:
   ```bash
   curl -i http://localhost:8210/onvif/service
   ```

### Autotracking Not Working

- Verify camera responds to `probe` command
- Check calibration values are close to actual motor speeds
- Ensure Frigate can reach the proxy host and port
- Review proxy logs: `docker compose logs onvif-fov-proxy`

### Diagonal Movement Not Smooth

- Increase `pan_seconds` and `tilt_seconds` until diagonal motion looks natural
- Enable `sequential_axes: true` if simultaneous movement causes issues
- Use optical calibration for more precise results

### Web UI Snapshot Not Updating

- Verify `snapshot_url` is accessible from inside the Docker container
- Check Frigate snapshots endpoint is working: curl the `snapshot_url`
- Ensure camera credentials are correct

## 📚 Detailed Documentation

- **[Calibration Guide](./README.md)** - Comprehensive calibration procedures
- **[Frigate Documentation](https://docs.frigate.video)** - Frigate autotracking setup
- **[ONVIF Standard](https://www.onvif.org)** - Official ONVIF protocol specification
- **[TAPO C210/C211 Specifications](https://www.tp-link.com)** - Camera hardware specs

## 🔒 Security Considerations

### ⚠️ Important Security Notes

1. **Authentication**: The proxy uses the same security token negotiated from Frigate
   - No need to configure TAPO camera credentials separately in the proxy
   - Frigate handles the WS-Security authentication with the camera
   - The proxy transparently forwards the authenticated requests
   - Credentials are only stored in Frigate's configuration

2. **Web Interface**: The calibration UI is **unauthenticated**
   - Restrict access to trusted networks only
   - Use a reverse proxy with authentication for remote access
   - Never expose directly to the internet

3. **Credentials Best Practices**:
   - Store credentials securely in Frigate's configuration
   - Never hardcode credentials in environment or docker-compose files
   - Use unique usernames/passwords for each camera
   - Enable firewall rules to restrict access to proxy and camera ports

4. **Network Isolation**:
   - Keep proxy and cameras on the same network segment
   - Use VPN for remote access
   - Implement rate limiting for ONVIF endpoints
   - Don't expose ONVIF ports to the internet

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## ⭐ Acknowledgments

- **Frigate Project** - For the excellent open-source NVR system
- **ONVIF** - For the standardized IP camera protocol
- **TAPO** - For accessible smart cameras
- **Community Contributors** - For feedback and improvements

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/juacas/onvifproxy/issues)
- **Discussions**: [GitHub Discussions](https://github.com/juacas/onvifproxy/discussions)
- **Frigate Community**: [Frigate Discussions](https://discussions.frigate.video)

## 📈 Project Status

![Status](https://img.shields.io/badge/Status-Active-brightgreen)
![Last Updated](https://img.shields.io/badge/Last%20Updated-2024-blue)

---

**Made with ❤️ for the Frigate and Home Automation communities**

[⬆ Back to Top](#onvif-proxy---ptz-enabler-for-tapo-cameras)
