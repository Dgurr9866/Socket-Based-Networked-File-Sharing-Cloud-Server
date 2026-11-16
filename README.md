# CNT3004 Socket-Based File Sharing

Requires Python 3.10 or newer. The application uses TCP sockets and Python's standard library. The server creates one thread for each connected client. File transfers stream in 1 MiB chunks, and the server publishes completed uploads into its shared folder.

| File | Purpose |
| --- | --- |
| `server.py` | Runs the server, prints its IP and port, and manages the shared folder and active transfers. |
| `client.py` | Opens the Tkinter desktop client, manages server requests, and records performance metrics. |
| `protocol.py` | Defines JSON request/response framing and streams file data. |
| `analytics.py` | Writes client and server CSV logs and summarizes them offline. |

## Run on three computers

1. Copy the extracted project folder to three physical computers and join the same private Wi-Fi network or personal hotspot.
2. On the server computer, open a terminal in the extracted project folder and run `python server.py`. This uses port `5050` and creates `shared_files/`. Use `--port` or `--root` to change either setting. The terminal prints the address clients should use. If it lists several addresses, choose the server's address on the private network.
3. On each client computer, open a terminal in the extracted project folder and run `python client.py`. Enter the server IP and port printed by the server, then click **Connect**. Keep both clients connected for the demonstration.
4. Use `python3` where that is the system command for Python 3, or `py -3` if using the Windows launcher.

Any computer that can connect to the server can list, upload, download, and delete shared files. Use the project's private demo network with sample files. The assignment lists authentication and authorization as requirements. This version uses plain TCP and omits those features and the optional transfer-resume feature.

## Desktop file browser

Run `python client.py` to open the desktop window. To check your Python installation, run `python -m tkinter`. If the module is unavailable, use a Python installation with Tkinter support. On Debian or Ubuntu, the system Python uses the `python3-tk` package.

Double-click a folder or select it and click **Open Folder**. Use **Up** and **Refresh** to navigate. **Upload** opens a local file picker and places the file in the displayed server folder. Select a file and click **Download** to choose where to save it. Use a fresh local filename for downloads. You can create folders and delete selected files or empty folders. The status line shows the result and transfer statistics.

## Collect performance data

Bring the three files specified in the handout. Verify their sizes before the test.

| Sample | Minimum size in bytes |
| --- | ---: |
| Plain text (.txt) | 25,000,000 |
| Audio (.wav) | 1,000,000,000 |
| Video (.mp4) | 2,000,000,000 |

1. With one client, upload and download all three samples. Use different names for your local download copies.
2. With two clients connected, start transfers of different sample files at about the same time. Repeat each load condition three times and preserve the CSVs using different `--log` filenames for each run or condition.
3. Show directory listing and subfolder creation/deletion. Exercise both answers to the overwrite prompt and refresh the directory listing.
4. Copy the CSVs to one computer. Give the client logs different filenames and run:

   ```text
   python analytics.py server_metrics.csv client1_metrics.csv client2_metrics.csv
   ```

The server starts its CSV logger on launch. Each client records its own commands in a separate log. Use `--log FILE` to choose a different filename. The logs include the operation, outcome, bytes transferred, time to first response, total operation time, transfer time, and decimal MB/s. Response time is the interval from the start of endpoint handling to the first reply sent or received. The client therefore includes network delay, while the server measures local handling up to the first reply. Transfer timings are endpoint-local and include file I/O. A client upload also includes waiting for the server's completion reply. Total time includes upload overwrite confirmation if prompted. Compare the same endpoint and operation across load conditions.

For the report, graph client throughput and response time by file size and number of simultaneous clients. Use the logs and screenshots from your three-computer test. For the narrated video, show the threaded server, TCP framing, desktop client controls, CSV logging, and both clients completing operations on the private network.
