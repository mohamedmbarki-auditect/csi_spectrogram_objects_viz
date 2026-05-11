import paramiko
import os

class RaspberryPi:
    def __init__(self, host="10.221.179.235", user="auditect", password="auditect"):
        self.host = host
        self.user = user
        self.password = password
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
    def connect(self):
        print(f"Connecting to {self.host}...")
        self.client.connect(self.host, username=self.user, password=self.password)
        print("Connected!")
        
    def execute(self, command):
        stdin, stdout, stderr = self.client.exec_command(command)
        return stdout.read().decode('utf-8', errors='replace')
        
    def upload(self, local_path, remote_path):
        sftp = self.client.open_sftp()
        sftp.put(local_path, remote_path)
        sftp.close()
        print(f"Uploaded {local_path} to {remote_path}")

    def close(self):
        self.client.close()

if __name__ == "__main__":
    pi = RaspberryPi()
    pi.connect()
    # Example: List files
    print(pi.execute("ls -la"))
    pi.close()
