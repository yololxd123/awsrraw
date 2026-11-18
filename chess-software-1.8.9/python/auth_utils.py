import os
import queue
import threading
import sys
import time
import uuid
import math
import random
import hashlib
import secrets
import platform
import requests
import ctypes
from ctypes import wintypes
import tkinter as tk
from tkinter import messagebox

user32 = kernel32 = None
if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32")
    kernel32 = ctypes.WinDLL("kernel32")
    if hasattr(user32, "GetAncestor"):
        user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
        user32.GetAncestor.restype = wintypes.HWND

WDA_EXCLUDEFROMCAPTURE = 0x00000011
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW  = 0x00040000
GA_ROOT = 2
GWL_EXSTYLE = -20
API_BASE_URL = "https://chess-auth-backend.onrender.com"

class HardwareID:
    # Secret keys - Change these to your own unique values
    # These keys make your HWID algorithm unique and non-reversible
    _SECRET_KEY_1 = b"ChessGUI_Primary_Secret_2025_v1"
    _SECRET_KEY_2 = b"AntiSpoof_Secondary_Key_Master"
    _SECRET_KEY_3 = b"HWID_Protection_Layer_Final"
    
    @staticmethod
    def _generate_device_salt():
        """Generate a unique salt based on multiple hardware factors"""
        import hmac
        
        # Collect multiple hardware identifiers
        mac = str(uuid.getnode())
        processor = platform.processor()
        system = platform.system()
        machine = platform.machine()
        
        # Windows-specific identifiers (more stable than WMIC)
        win_identifiers = []
        if sys.platform == 'win32':
            try:
                # Get Windows product ID (stable across reboots)
                import winreg
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 
                                    r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
                product_id = winreg.QueryValueEx(key, "ProductId")[0]
                win_identifiers.append(product_id)
                winreg.CloseKey(key)
            except:
                pass
            
            try:
                # Get machine GUID (very stable)
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 
                                    r"SOFTWARE\Microsoft\Cryptography")
                machine_guid = winreg.QueryValueEx(key, "MachineGuid")[0]
                win_identifiers.append(machine_guid)
                winreg.CloseKey(key)
            except:
                pass
        
        # Combine all identifiers
        base_string = f"{mac}{processor}{system}{machine}{''.join(win_identifiers)}"
        
        # Generate device-specific salt using HMAC
        device_salt = hmac.new(
            HardwareID._SECRET_KEY_1,
            base_string.encode(),
            hashlib.sha256
        ).digest()
        
        return device_salt
    
    @staticmethod
    def _apply_time_rotation(hwid_bytes, creation_time):
        """Apply time-based rotation to prevent pattern analysis"""
        import hmac
        
        # Use creation time as additional entropy (rounded to day)
        time_component = str(int(creation_time // 86400))  # Days since epoch
        
        # Rotate HWID with time component
        rotated = hmac.new(
            HardwareID._SECRET_KEY_2 + time_component.encode(),
            hwid_bytes,
            hashlib.sha256
        ).digest()
        
        return rotated
    
    @staticmethod
    def _final_obfuscation(data):
        """Final obfuscation layer with multiple hash rounds"""
        import hmac
        
        # Round 1: HMAC with secret key 3
        round1 = hmac.new(HardwareID._SECRET_KEY_3, data, hashlib.sha512).digest()
        
        # Round 2: SHA256 of round 1
        round2 = hashlib.sha256(round1).digest()
        
        # Round 3: HMAC again with rotated key
        rotated_key = hashlib.sha256(HardwareID._SECRET_KEY_3[::-1]).digest()
        round3 = hmac.new(rotated_key, round2, hashlib.sha256).digest()
        
        # Convert to integer and take 18 digits
        final_int = int.from_bytes(round3[:8], byteorder='big')
        hwid = str(final_int)[:18].zfill(18)
        
        return hwid
    
    @staticmethod
    def get_hwid():
        """
        Generate cryptographically secure, non-reversible HWID
        Uses HMAC with multiple secret keys and time-based rotation
        """
        cache_file = os.path.join(os.environ.get('APPDATA', '.'), '.sys_ident')
        
        # Try to load cached HWID with timestamp
        try:
            if os.path.exists(cache_file):
                with open(cache_file, 'r') as f:
                    data = f.read().strip().split('|')
                    if len(data) == 2:
                        cached_hwid, timestamp = data
                        if cached_hwid and len(cached_hwid) == 18:
                            # Verify HWID hasn't been tampered with
                            verification = HardwareID._verify_cached_hwid(
                                cached_hwid, float(timestamp)
                            )
                            if verification:
                                return cached_hwid
        except:
            pass
        
        try:
            # Step 1: Generate device-specific salt from hardware
            device_salt = HardwareID._generate_device_salt()
            
            # Step 2: Get current timestamp
            creation_time = time.time()
            
            # Step 3: Apply time-based rotation
            rotated_data = HardwareID._apply_time_rotation(device_salt, creation_time)
            
            # Step 4: Final multi-round obfuscation
            final_hwid = HardwareID._final_obfuscation(rotated_data)
            
            # Step 5: Cache the HWID with timestamp
            try:
                with open(cache_file, 'w') as f:
                    f.write(f"{final_hwid}|{creation_time}")
                
                # Hide the cache file (Windows)
                if sys.platform == 'win32':
                    import ctypes
                    ctypes.windll.kernel32.SetFileAttributesW(cache_file, 2)
            except:
                pass
            
            return final_hwid
            
        except Exception as e:
            print(f"HWID generation error: {e}")
            # Ultimate fallback with same security
            fallback_data = hashlib.sha256(str(uuid.getnode()).encode()).digest()
            return HardwareID._final_obfuscation(fallback_data)
    
    @staticmethod
    def _verify_cached_hwid(cached_hwid, timestamp):
        """Verify cached HWID hasn't been tampered with"""
        import hmac
        
        try:
            # Regenerate the HWID to verify it matches
            device_salt = HardwareID._generate_device_salt()
            rotated_data = HardwareID._apply_time_rotation(device_salt, timestamp)
            regenerated = HardwareID._final_obfuscation(rotated_data)
            
            # Constant-time comparison to prevent timing attacks
            return hmac.compare_digest(cached_hwid, regenerated)
        except:
            return False

# ============================================================================
# SECURITY LAYER 3: ONLINE AUTHENTICATION
# ============================================================================
class OnlineAuth:
    def __init__(self):
        self.hwid = HardwareID.get_hwid()
        self.session_token = None
        self.username = None
        self.expiry_date = None
    
    def login(self, username, password):
        try:
            response = requests.post(
                f"{API_BASE_URL}/api/login",
                json={
                    "username": username,
                    "password": password,
                    "hwid": self.hwid
                },
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    self.username = data.get("username")
                    self.expiry_date = data.get("expiry")
                    self.session_token = secrets.token_hex(32)
                    
                    if data.get("first_time"):
                        return True, "HWID registered! This device is now linked to your account.", True
                    else:
                        return True, "Login successful", False
                else:
                    return False, data.get("message", "Login failed"), False
            else:
                data = response.json()
                return False, data.get("message", "Authentication failed"), False
        
        except requests.exceptions.Timeout:
            return False, "Connection timeout. Check your internet connection.", False
        except requests.exceptions.ConnectionError:
            return False, "Cannot connect to authentication server.", False
        except Exception as e:
            return False, f"Error: {str(e)}", False

# ============================================================================
# SECURITY LAYER 5: INPUT VALIDATION
# ============================================================================
class InputValidator:
    @staticmethod
    def validate_fen(fen):
        """Validate a complete single-line FEN before it reaches a UCI engine."""
        if not isinstance(fen, str) or len(fen) > 256 or "\n" in fen or "\r" in fen:
            return False
        parts = fen.split()
        if len(parts) != 6 or parts[1] not in ("w", "b"):
            return False
        ranks = parts[0].split("/")
        if len(ranks) != 8:
            return False
        pieces = set("rnbqkpRNBQKP")
        for rank in ranks:
            files = 0
            for char in rank:
                if char in pieces:
                    files += 1
                elif char in "12345678":
                    files += int(char)
                else:
                    return False
            if files != 8:
                return False
        castling, target, halfmove, fullmove = parts[2:]
        if castling != "-" and any(c not in "KQkq" for c in castling):
            return False
        if target != "-" and (len(target) != 2 or target[0] not in "abcdefgh" or target[1] not in "36"):
            return False
        try:
            return int(halfmove) >= 0 and int(fullmove) >= 1
        except ValueError:
            return False
    
    @staticmethod
    def validate_depth(depth):
        try:
            d = int(depth)
            return 1 <= d <= 50
        except:
            return False

    # ============================================================================
# SCREEN CAPTURE PROTECTION & TASKBAR HIDING
# ============================================================================
def get_root_hwnd(hwnd):
    """Helper to get root owner window from any HWND"""
    try:
        return user32.GetAncestor(hwnd, GA_ROOT)
    except:
        return hwnd

def hide_from_screen_capture(hwnd, enable=True):
    """Hides or shows window from OBS, screen recording, and screenshots (no black box)"""
    try:
        hwnd = get_root_hwnd(hwnd)
        if enable:
            result = user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
            if result:
                print(f"[OK] Window hidden from screen capture (HWND: {hwnd})")
                return True
            else:
                print(f"[WARN] Screen capture protection failed")
                return False
        else:
            # Disable capture protection (default affinity)
            result = user32.SetWindowDisplayAffinity(hwnd, 0)
            if result:
                print(f"[OK] Window visible to screen capture (HWND: {hwnd})")
                return True
            return False
    except Exception as e:
        print(f"[ERROR] Error: {e}")
        return False

def remove_from_taskbar(hwnd, enable=True, keep_minimized=False):
    """Removes or restores window to Windows taskbar"""
    try:
        hwnd = get_root_hwnd(hwnd)
        current_style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        if enable:
            new_style = current_style | WS_EX_TOOLWINDOW
            new_style = new_style & ~WS_EX_APPWINDOW
            print(f"[OK] Window removed from taskbar (HWND: {hwnd})")
        else:
            new_style = current_style & ~WS_EX_TOOLWINDOW
            new_style = new_style | WS_EX_APPWINDOW
            print(f"[OK] Window restored to taskbar (HWND: {hwnd})")
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, new_style)
        # Tell Windows to immediately refresh the non-client frame/taskbar entry.
        # Without this, a withdrawn and restored Tk window can reappear in the
        # taskbar even though it has the TOOLWINDOW style.
        try:
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                                0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"[ERROR] Taskbar error: {e}")
        return False

def apply_stealth_mode(root, enable=True):
    """Apply or remove both OBS hiding and taskbar removal"""
    try:
        root.update_idletasks()
        hwnd = user32.GetParent(root.winfo_id())
        if hwnd == 0:
            hwnd = root.winfo_id()
        
        if enable:
            print(f"\n[+] Applying stealth mode (HWND: {hwnd})...")
        else:
            print(f"\n[-] Disabling stealth mode (HWND: {hwnd})...")
        
        capture_protected = hide_from_screen_capture(hwnd, enable)
        taskbar_removed = remove_from_taskbar(hwnd, enable)
        
        if enable and capture_protected and taskbar_removed:
            print("[OK] Full stealth mode activated!")
        elif not enable:
            print("[OK] Stealth mode deactivated!")
        return capture_protected or taskbar_removed
    except Exception as e:
        print(f"[ERROR] Stealth mode error: {e}")
        return False

# ============================================================================
# ONLINE LOGIN DIALOG
# ============================================================================
class LoginDialog:
    def __init__(self):
        self.authenticated = False
        self._closed = False
        self._login_in_progress = False
        self._login_results = queue.Queue()
        self.online_auth = OnlineAuth()
        self._security_check_id = None
        
        self.root = tk.Tk()
        self.root.title("Online Authentication")
        self.root.geometry("400x300")
        self.root.resizable(False, False)
        
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = (screen_width - 400) // 2
        y = (screen_height - 300) // 2
        self.root.geometry(f"400x300+{x}+{y}")
        self.root.attributes('-topmost', True)
        
        frame = tk.Frame(self.root, padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)
        
        tk.Label(frame, text="Chess Engine - Online Login", 
                font=("Arial", 12, "bold")).pack(pady=(0, 10))
        
        hwid_label = tk.Label(frame, text=f"Device ID: {self.online_auth.hwid[:15]}...",
                             font=("Courier", 8), fg="gray")
        hwid_label.pack(pady=(0, 15))
        
        tk.Label(frame, text="Username:", font=("Arial", 10)).pack(anchor="w", pady=(0, 5))
        self.username_entry = tk.Entry(frame, width=35, font=("Arial", 10))
        self.username_entry.pack(pady=(0, 10))
        self.username_entry.focus()
        
        tk.Label(frame, text="Password:", font=("Arial", 10)).pack(anchor="w", pady=(0, 5))
        self.password_entry = tk.Entry(frame, show="●", width=35, font=("Arial", 10))
        self.password_entry.pack(pady=(0, 10))
        
        self.status_label = tk.Label(frame, text="", fg="red", font=("Arial", 9))
        self.status_label.pack(pady=(0, 10))
        
        button_frame = tk.Frame(frame)
        button_frame.pack(fill=tk.X)
        
        tk.Button(button_frame, text="Login", command=self.verify_login,
                 bg="#4CAF50", fg="white", font=("Arial", 9, "bold")).pack(side=tk.RIGHT, padx=5)
        
        tk.Button(button_frame, text="Cancel", command=self.cancel,
                 bg="#f44336", fg="white", font=("Arial", 9)).pack(side=tk.RIGHT)
        
        self.root.bind('<Return>', lambda e: self.verify_login())
        self.root.protocol("WM_DELETE_WINDOW", self.cancel)
        
        self.run_periodic_checks()
    
    def run_periodic_checks(self):
        if not self.root.winfo_exists():
            return
        
        try:
            self._security_check_id = self.root.after(5000, self.run_periodic_checks)
        except tk.TclError:
            pass
    
    def verify_login(self):
        if self._closed or self._login_in_progress:
            return
        username = self.username_entry.get().strip()
        password = self.password_entry.get()
        if not username or not password:
            self.status_label.config(text="Please enter username and password", fg="red")
            return
        self._login_in_progress = True
        self.status_label.config(text="Authenticating...", fg="blue")
        def request():
            try:
                result = self.online_auth.login(username, password)
            except Exception as exc:
                result = (False, str(exc), False)
            self._login_results.put((username, result))
        threading.Thread(target=request, daemon=True, name="authentication").start()
        self.root.after(50, self._poll_login_result)

    def _poll_login_result(self):
        if self._closed:
            return
        try:
            username, result = self._login_results.get_nowait()
        except queue.Empty:
            self.root.after(50, self._poll_login_result)
            return
        self._login_in_progress = False
        success, message, first_time = result
        if not success:
            self.status_label.config(text=f"✗ {message}", fg="red")
            self.password_entry.delete(0, tk.END)
            return
        self.status_label.config(text="✓ Authentication successful", fg="green")
        if first_time:
            messagebox.showinfo("HWID Registered",
                                f"Account '{username}' is linked to this device.")
        else:
            messagebox.showinfo("Welcome Back",
                                f"Login successful!\nLicense valid until:\n{self.online_auth.expiry_date}")
        self.authenticated = True
        self._closed = True
        if self._security_check_id:
            self.root.after_cancel(self._security_check_id)
        self.root.after(500, self.root.destroy)

    
    def cancel(self):
        self._closed = True
        if self._security_check_id:
            try:
                self.root.after_cancel(self._security_check_id)
            except tk.TclError:
                pass
        self.root.destroy()
    
    def run(self):
        self.root.mainloop()
        return self.authenticated

# ============================================================================
# CONTROL PANEL WITH SCROLLABLE SIDEBAR
# ============================================================================

