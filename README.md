# CSI Spectrogram & Object Viz 📡👁️🎼

A state-of-the-art Multimodal WiFi CSI Dashboard that combines frequency-domain analysis (Spectrograms) with spatial-domain visualization (2D Fresnel Imaging).

## ✨ Features
- **Real-Time Doppler Spectrogram**: Visualize the "texture" and speed of motion as a rolling color map.
- **2D Fresnel Field Imaging**: Watch radio waves warp and bend around a "Ghost Object" shadow in real-time.
- **High-Sensitivity SSD Engine**: Binary movement detection with hardware glitch rejection.
- **Proximity Estimation**: Uses RSSI-to-CSI ratios to distinguish near-field from far-field interactions.
- **Pure Statistical Logic**: High performance with zero heavy AI dependencies.

## 🚀 Setup

1. **Install dependencies**:
   ```bash
   pip install fastapi uvicorn numpy scipy pyserial paramiko
   ```
2. **Connect ESP32**: Plug your transmitter into the Raspberry Pi.
3. **Run**:
   ```bash
   python light_csi_server.py
   ```
4. **View**: Open `http://<pi_ip>:8083` in your browser.

## ⚖️ License
MIT
