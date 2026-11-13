import time
import threading
import pandas as pd
from datetime import datetime

class NetworkAnalytics:
    def __init__(self, log_file="network_performance_metrics.csv"):
        self.log_file = log_file
        self.lock = threading.Lock()
        self.columns = [
            "timestamp",
            "source",           # "SERVER" or "CLIENT"
            "client_id",        # Client IP:Port or username
            "operation",        # UPLOAD, DOWNLOAD, DELETE, DIR, SUBFOLDER, AUTH
            "file_name",
            "file_type",        # TEXT, AUDIO, VIDEO, OTHER
            "file_size_mb",
            "transfer_time_sec",
            "data_rate_mbps",   # MB/s
            "response_time_ms", # Time to first byte / command acknowledgment
            "status"            # SUCCESS or ERROR
        ]
        self.df = pd.DataFrame(columns=self.columns)

    def _detect_file_type(self, filename):
        if not filename:
            return "NONE"
        ext = filename.lower().split('.')[-1] if '.' in filename else ""
        if ext in ['txt', 'csv', 'md', 'json', 'log']:
            return "TEXT"
        elif ext in ['mp3', 'wav', 'aac', 'flac', 'ogg']:
            return "AUDIO"
        elif ext in ['mp4', 'avi', 'mkv', 'mov', 'wmv']:
            return "VIDEO"
        return "OTHER"

    def log_metric(self, source, client_id, operation, file_name="", 
                   file_size_bytes=0, transfer_time_sec=0.0, 
                   response_time_sec=0.0, status="SUCCESS"):
        """Thread-safe method to record a single network operation."""
        file_size_mb = file_size_bytes / (1024 * 1024)
        data_rate_mbps = (file_size_mb / transfer_time_sec) if transfer_time_sec > 0 else 0.0
        response_time_ms = response_time_sec * 1000.0

        new_row = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": source,
            "client_id": str(client_id),
            "operation": operation.upper(),
            "file_name": file_name,
            "file_type": self._detect_file_type(file_name),
            "file_size_mb": round(file_size_mb, 4),
            "transfer_time_sec": round(transfer_time_sec, 4),
            "data_rate_mbps": round(data_rate_mbps, 4),
            "response_time_ms": round(response_time_ms, 2),
            "status": status
        }

        with self.lock:
            self.df = pd.concat([self.df, pd.DataFrame([new_row])], ignore_index=True)

    def ingest_client_report(self, client_id, metrics_list):
        """Receives a batch of client-side metrics over the network and merges them."""
        if not metrics_list:
            return
        with self.lock:
            for item in metrics_list:
                item["source"] = "CLIENT"
                item["client_id"] = str(client_id)
            client_df = pd.DataFrame(metrics_list)
            self.df = pd.concat([self.df, client_df], ignore_index=True)

    def save_to_csv(self):
        """Exports all collected server and client metrics for offline report analysis."""
        with self.lock:
            self.df.to_csv(self.log_file, index=False)
            print(f"[ANALYTICS] Saved {len(self.df)} records to {self.log_file}")

    def generate_report_summary(self):
        """Prints aggregated performance metrics grouped by Operation and File Type."""
        with self.lock:
            if self.df.empty:
                print("[ANALYTICS] No metrics recorded yet.")
                return

            print("\n" + "="*65)
            print("NETWORK PERFORMANCE EVALUATION SUMMARY")
            print("="*65)

            # 1. Summary by Operation and Source (Server vs Client)
            op_summary = self.df.groupby(["source", "operation"]).agg(
                Total_Ops=("status", "count"),
                Avg_Response_ms=("response_time_ms", "mean"),
                Avg_Transfer_sec=("transfer_time_sec", "mean"),
                Avg_Rate_MBs=("data_rate_mbps", "mean")
            ).round(3)
            print("\n--- Performance by Source & Operation ---")
            print(op_summary.to_string())

            # 2. File Transfer Rates by File Type (Uploads & Downloads only)
            transfers = self.df[self.df["operation"].isin(["UPLOAD", "DOWNLOAD"]) & (self.df["status"] == "SUCCESS")]
            if not transfers.empty:
                type_summary = transfers.groupby(["operation", "file_type"]).agg(
                    Count=("file_name", "count"),
                    Avg_Size_MB=("file_size_mb", "mean"),
                    Avg_Time_sec=("transfer_time_sec", "mean"),
                    Min_Rate_MBs=("data_rate_mbps", "min"),
                    Avg_Rate_MBs=("data_rate_mbps", "mean"),
                    Max_Rate_MBs=("data_rate_mbps", "max")
                ).round(3)
                print("\n--- Transfer Throughput by File Type ---")
                print(type_summary.to_string())
            print("="*65 + "\n")
