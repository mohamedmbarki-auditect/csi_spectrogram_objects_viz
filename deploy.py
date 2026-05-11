import paramiko
import os
import time

def deploy_hierarchical():
    host = "10.221.179.235"
    user = "auditect"
    password = "auditect"
    remote_dir = "/home/auditect/wifi-csi-dashboard-new"

    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(host, username=user, password=password)
        
        print(f"Ensuring remote directories exist in {remote_dir}...")
        client.exec_command(f"mkdir -p {remote_dir}/static")
        client.exec_command(f"mkdir -p {remote_dir}/ml_data")
        
        print("Uploading hierarchical pipeline files...")
        sftp = client.open_sftp()
        
        # Local base path
        local_base = "C:/Users/mbark/Desktop/Wifi_CSI_Cockpit"
        
        # Core Files (Renamed to match user's remote filenames)
        sftp.put(f"{local_base}/light_csi_server_ml.py", f"{remote_dir}/light_csi_server.py")
        sftp.put(f"{local_base}/ml_manager.py", f"{remote_dir}/ml_manager.py")
        sftp.put(f"{local_base}/train_pipeline.py", f"{remote_dir}/train_pipeline.py")
        sftp.put(f"{local_base}/index_modified.html", f"{remote_dir}/static/index.html")
        
        sftp.close()
        
        print("Patching remote configuration (Port 8083, Serial ttyACM0, Baud 921600)...")
        # Ensure port 8083
        client.exec_command(f"sed -i 's/port=8082/port=8083/g' {remote_dir}/light_csi_server.py")
        # Ensure ttyACM0 (found on your device)
        client.exec_command(f"sed -i 's/ttyACM1/ttyACM0/g' {remote_dir}/light_csi_server.py")
        # Ensure baud 921600
        client.exec_command(f"sed -i 's/baud_rate\": 115200/baud_rate\": 921600/g' {remote_dir}/light_csi_server.py")
        
        print("NUCLEAR RESTART: Killing ALL python processes and freeing ports...")
        client.exec_command("echo 'auditect' | sudo -S pkill -9 python")
        client.exec_command("echo 'auditect' | sudo -S pkill -9 python3")
        client.exec_command("echo 'auditect' | sudo -S fuser -k 8082/tcp")
        client.exec_command("echo 'auditect' | sudo -S fuser -k 8083/tcp")
        time.sleep(2)
        
        print(f"Starting server in {remote_dir}...")
        client.exec_command(f"cd {remote_dir} && nohup python3 light_csi_server.py > dashboard.log 2>&1 &")
        
        print("\nDEPLOYMENT COMPLETE!")
        print(f"Go to http://{host}:8083 to see the new ML Center tab.")
        
        client.close()
    except Exception as e:
        print(f"Deployment Error: {e}")

if __name__ == "__main__":
    deploy_hierarchical()
