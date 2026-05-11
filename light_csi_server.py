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
from scipy.ndimage import gaussian_filter1d
import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
import serial

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("csi_pose_skeleton")

CONFIG = {
    "serial_port": "/dev/ttyACM0",
    "baud_rate": 921600,
    "sample_rate": 50,
    "calibration_samples": 300,
    "smoothing_alpha": 0.05
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
        self.subcarrier_ema = None
        self.ema_alpha = 0.03
        self.heatmap_ema = np.zeros(64)
        
        self.score_buffer = deque(maxlen=10)
        self.smoothed_score = 0.0
        
        self.is_calibrated = False
        self.baseline_noise = 0.0
        self.calibration_buffer = []
        self.frame_count = 0

    def extract_skeleton(self, bins):
        # POSE ESTIMATION HEURISTICS
        # We find the 'mass' of joints based on subcarrier intensity
        if np.sum(bins) < 2.0: return None
        
        indices = np.arange(64)
        moving_indices = indices[bins > 0.15]
        if len(moving_indices) < 5: return None
        
        # Head: The highest subcarrier with significant movement
        head_y = float(np.max(moving_indices))
        # Feet: The lowest subcarrier
        feet_y = float(np.min(moving_indices))
        # Torso: Centroid of the mass
        torso_y = float(np.average(moving_indices, weights=bins[moving_indices]))
        
        # Hands: Looking for outlier peaks (limbs extending)
        # We assume hands are the peaks furthest from the torso
        diff_from_torso = np.abs(moving_indices - torso_y)
        hand_idx = moving_indices[np.argmax(diff_from_torso)]
        hand_y = float(hand_idx)

        return {
            "head": head_y,
            "torso": torso_y,
            "feet": feet_y,
            "hand": hand_y
        }

    def process(self, line):
        if not line or not line.startswith("CSI_DATA,"): return None
        try:
            parts = line.split(",")
            rssi = float(parts[3])
            
            bracket_start = line.find("\"[")
            bracket_end = line.find("]", bracket_start)
            csi_raw = line[bracket_start+2:bracket_end]
            csi_values = [int(v.strip()) for v in csi_raw.split(",") if v.strip()]
            
            if len(csi_values) < 120: return None
            
            imag = np.array(csi_values[0::2][:64], dtype=np.float32)
            real = np.array(csi_values[1::2][:64], dtype=np.float32)
            amplitude = np.sqrt(real**2 + imag**2)
            
            if self.subcarrier_ema is None:
                self.subcarrier_ema = amplitude.copy()
                return None
            
            diff = np.abs(amplitude - self.subcarrier_ema)
            self.subcarrier_ema = (1 - self.ema_alpha) * self.subcarrier_ema + self.ema_alpha * amplitude
            
            smooth_diff = gaussian_filter1d(diff, sigma=1.0)
            
            # SCORE
            global_diff = float(np.sum(diff[10:54]))
            self.score_buffer.append(global_diff)
            median_score = float(np.median(list(self.score_buffer)))
            alpha = self.config["smoothing_alpha"]
            self.smoothed_score = alpha * median_score + (1 - alpha) * self.smoothed_score
            self.frame_count += 1
            
            if not self.is_calibrated:
                self.calibration_buffer.append(self.smoothed_score)
                if len(self.calibration_buffer) >= self.config["calibration_samples"]:
                    self.baseline_noise = float(np.percentile(self.calibration_buffer, 90))
                    self.is_calibrated = True
                return {"type": "calibrating", "progress": len(self.calibration_buffer)/self.config["calibration_samples"], "calibrating": True}

            movement_index = self.smoothed_score / (self.baseline_noise + 1e-9)
            is_moving = movement_index > 2.0
            
            # MAP & SKELETON
            target_heatmap = np.clip(smooth_diff * 0.1, 0, 1) if is_moving else np.zeros(64)
            self.heatmap_ema = (1 - 0.2) * self.heatmap_ema + 0.2 * target_heatmap
            
            skeleton = self.extract_skeleton(self.heatmap_ema) if is_moving else None

            return {
                "type": "update",
                "timestamp": time.time(),
                "presence_value": float(max(0, round(movement_index - 1.0, 4))),
                "movement_value": float(max(0, round(movement_index - 1.0, 4))),
                "rssi": float(round(rssi, 1)),
                "frame_count": self.frame_count,
                "movement_type": "MOVE" if is_moving else "STILL",
                "subcarrier_map": [float(round(x, 4)) for x in self.heatmap_ema],
                "skeleton": skeleton
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
    processor.subcarrier_ema = None
    processor.heatmap_ema = np.zeros(64)
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
