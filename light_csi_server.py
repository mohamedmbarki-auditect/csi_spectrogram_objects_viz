import asyncio
import sqlite3
import time
import json
import logging
import queue
import threading
from collections import deque
from pathlib import Path
import os

import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
import serial

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("csi_ultimate")

CONFIG = {
    "serial_port": "/dev/ttyACM0",
    "baud_rate": 921600,
    "sample_rate": 50,
    "calibration_samples": 300,
    "smoothing_alpha": 0.06,
    "glitch_threshold": 0.4,
    "fft_window": 64
}

class CSIReader:
    def __init__(self, port, baud, output_queue):
        self.port = port
        self.baud = baud
        self.output_queue = output_queue
        self.ser = None

    def run(self):
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0.1)
            logger.info(f"Connected to {self.port}")
            while True:
                line = self.ser.readline().decode('utf-8', errors='replace').strip()
                if line:
                    self.output_queue.put(line)
        except Exception as e:
            logger.error(f"Serial error: {e}")

class CSIProcessor:
    def __init__(self, config):
        self.config = config
        self.last_normalized = None
        self.last_mean_amp = None
        
        # Buffers
        self.score_buffer = deque(maxlen=10)
        self.smoothed_score = 0.0
        self.fft_buffer = deque(maxlen=config["fft_window"])
        self.rssi_history = deque(maxlen=50)
        
        # State
        self.is_calibrated = False
        self.baseline_noise = 0.0
        self.calibration_buffer = []
        self.frame_count = 0

    def get_spectrogram(self, current_val):
        self.fft_buffer.append(current_val)
        if len(self.fft_buffer) < self.config["fft_window"]:
            return [0.0] * 32
        data = np.array(self.fft_buffer)
        windowed = data * np.hanning(len(data))
        fft_res = np.abs(np.fft.rfft(windowed))
        fft_res = np.log1p(fft_res) / 5.0
        return [float(round(x, 4)) for x in fft_res[:32]]

    def get_proximity(self, current_ssd):
        if len(self.rssi_history) < 30: return "Analyzing..."
        rssi_var = np.var(self.rssi_history)
        ratio = current_ssd / (rssi_var + 1e-6)
        if ratio < 500: return "Near"
        return "Far"

    def process(self, line):
        if not line or not line.startswith("CSI_DATA,"): return None
        try:
            parts = line.split(",")
            rssi = float(parts[3])
            self.rssi_history.append(rssi)
            
            bracket_start = line.find("\"[")
            bracket_end = line.find("]", bracket_start)
            csi_raw = line[bracket_start+2:bracket_end]
            
            csi_values = []
            for v in csi_raw.split(","):
                v = v.strip()
                if v:
                    try: csi_values.append(int(v))
                    except: continue
            
            if len(csi_values) < 120: return None
            
            # 1. Extraction & Glitch Rejection
            imag = np.array(csi_values[0::2][:64], dtype=np.float32)
            real = np.array(csi_values[1::2][:64], dtype=np.float32)
            amplitude = np.sqrt(real**2 + imag**2)
            mean_amp = np.mean(amplitude)
            
            if self.last_mean_amp is not None:
                if abs(mean_amp - self.last_mean_amp) / (self.last_mean_amp + 1e-6) > self.config["glitch_threshold"]:
                    self.last_mean_amp = mean_amp
                    return None
            self.last_mean_amp = mean_amp
            
            # 2. Normalization & SSD
            normalized = amplitude / (mean_amp + 1e-6)
            if self.last_normalized is None:
                self.last_normalized = normalized
                return None
            
            diff = normalized[10:54] - self.last_normalized[10:54]
            raw_ssd = float(np.sum(diff**2))
            self.last_normalized = normalized
            
            # 3. Filtering
            self.score_buffer.append(raw_ssd)
            median_score = float(np.median(list(self.score_buffer)))
            alpha = self.config["smoothing_alpha"]
            self.smoothed_score = alpha * median_score + (1 - alpha) * self.smoothed_score
            self.frame_count += 1
            
            # 4. Calibration
            if not self.is_calibrated:
                self.calibration_buffer.append(self.smoothed_score)
                if len(self.calibration_buffer) >= self.config["calibration_samples"]:
                    self.baseline_noise = float(np.percentile(self.calibration_buffer, 90))
                    self.is_calibrated = True
                    logger.info(f"Ultimate Calibrated! Baseline: {self.baseline_noise:.6f}")
                return {"type": "calibrating", "progress": len(self.calibration_buffer)/self.config["calibration_samples"], "calibrating": True}

            # 5. Build Ultimate Frame
            movement_index = self.smoothed_score / (self.baseline_noise + 1e-9)
            is_moving = movement_index > 2.0 
            
            return {
                "type": "update",
                "timestamp": time.time(),
                "presence_value": float(max(0, round(movement_index - 1.0, 4))),
                "movement_value": float(max(0, round(movement_index - 1.0, 4))),
                "rssi": float(round(rssi, 1)),
                "frame_count": self.frame_count,
                "movement_type": "MOVE" if is_moving else "STILL",
                "spectrogram": self.get_spectrogram(movement_index),
                "spatial_profile": [float(x) for x in normalized[::8]],
                "proximity": self.get_proximity(raw_ssd) if is_moving else "--"
            }
        except Exception as e:
            logger.error(f"Process error: {e}")
            return None

app = FastAPI()
ws_manager = []
processor = CSIProcessor(CONFIG)

@app.get("/")
async def index(): return FileResponse("static/index.html")

@app.post("/calibrate")
async def calibrate():
    processor.is_calibrated = False
    processor.calibration_buffer = []
    processor.smoothed_score = 0.0
    processor.fft_buffer.clear()
    return {"message": "Calibration restarted"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    ws_manager.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except:
        if websocket in ws_manager: ws_manager.remove(websocket)

async def collection_loop(serial_queue):
    while True:
        try:
            line = serial_queue.get_nowait()
            status = processor.process(line)
            if status:
                to_remove = []
                for ws in ws_manager:
                    try:
                        await asyncio.wait_for(ws.send_json(status), timeout=0.2)
                    except:
                        to_remove.append(ws)
                for ws in to_remove:
                    if ws in ws_manager: ws_manager.remove(ws)
        except queue.Empty:
            await asyncio.sleep(0.01)

@app.on_event("startup")
async def startup():
    serial_queue = queue.Queue()
    reader = CSIReader(CONFIG["serial_port"], CONFIG["baud_rate"], serial_queue)
    threading.Thread(target=reader.run, daemon=True).start()
    asyncio.create_task(collection_loop(serial_queue))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8083)
