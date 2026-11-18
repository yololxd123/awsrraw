"""Real Tk lifecycle; fake Win32 API. Requires a display, not a Windows host."""
import sys,tkinter as tk
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
import gui_overlay as g,auth_utils as auth
class Win:
 def __init__(self):self.affinity={};self.calls=[];self.styles={};self.parents={};self.succeed=True
 def GetAncestor(self,h,flag):
  assert flag==2
  if h in self.parents.values():return h
  return self.parents.setdefault(h,h+0x10000000)
 def GetParent(self,h):return self.GetAncestor(h,2)
 def SetWindowDisplayAffinity(self,h,flag):
  self.calls.append((h,flag))
  if self.succeed:self.affinity[h]=flag
  return int(self.succeed)
 def GetWindowLongW(self,h,index):return self.styles.get(h,0)
 GetWindowLongPtrW=GetWindowLongW
 def SetWindowLongW(self,h,index,value):self.styles[h]=value;return 1
 SetWindowLongPtrW=SetWindowLongW
 def SetWindowPos(self,*args):return 1
win=Win();root=tk.Tk();root.geometry('500x500');canvas=tk.Canvas(root);canvas.pack(fill='both',expand=True)
panel=tk.Toplevel(root);panel.withdraw();root.update()
s=g.ChessOverlay.__new__(g.ChessOverlay)
s.root=root;s.canvas=canvas;s.stealth_mode_enabled=True;s.hotkeys_locked=False
s.control_panel=NS(root=panel,stealth_mode_btn=Mock(),stealth_status_label=Mock())
s._save_all_settings=Mock();s._get_logging_terminal_handle=lambda:None
original=tk.Toplevel.attributes
# Only Windows color-key creation is substituted; real Tk map/withdraw/geometry
# and callbacks run, with real production affinity helper against fake Win32.
def attributes(self,*args):
 if args and args[0]=='-transparentcolor':return None
 return original(self,*args)
with patch.object(g,'user32',win),patch.object(auth,'user32',win),patch.object(tk.Toplevel,'attributes',attributes):
 s._build_arrow_layer();assert s._arrow_layer_ready
 arrow=s.arrow_layer;native=win.GetAncestor(arrow.winfo_id(),2)
 assert win.affinity[native]==0x11 and not arrow.winfo_ismapped()
 s._sync_arrow_layer();root.update()
 assert arrow.winfo_ismapped() and win.affinity[native]==0x11
 style=win.styles[native]
 assert style & g.WS_EX_TRANSPARENT and style & g.WS_EX_TOOLWINDOW and not style & g.WS_EX_APPWINDOW
 count=len(win.calls)
 for _ in range(5):s._sync_arrow_layer();root.update()
 assert len(win.calls)==count
 print('PASS enabled-at-creation before display; mapped arrow protected/click-through; no affinity churn on redraw')
 s.toggle_stealth_mode();assert not s.stealth_mode_enabled
 for widget in [root,panel,arrow]:assert win.affinity[win.GetAncestor(widget.winfo_id(),2)]==0
 assert win.styles[native] & g.WS_EX_TRANSPARENT
 s._hide_arrow_layer();root.update();assert not arrow.winfo_ismapped()
 s.toggle_stealth_mode();assert s.stealth_mode_enabled and win.affinity[native]==0x11
 s._sync_arrow_layer();root.update();assert win.affinity[native]==0x11
 # Simulate affinity lost on remapping; map event must reapply despite cache.
 s._hide_arrow_layer();root.update();win.affinity[native]=0
 s._sync_arrow_layer();root.update();assert win.affinity[native]==0x11
 print('PASS F5 ON/OFF matches GUI/visualizer, hidden toggle and remap recover policy without changing click-through')
 arrow.destroy();root.update();s._apply_arrow_layer_capture_protection(force=True)
 s._build_arrow_layer();assert s._arrow_layer_ready
 native2=win.GetAncestor(s.arrow_layer.winfo_id(),2)
 assert native2!=native and win.affinity[native2]==0x11
 s._sync_arrow_layer();root.update();assert win.affinity[native2]==0x11
 s.stealth_mode_enabled=False;win.succeed=False;s._apply_arrow_layer_capture_protection()
 assert s._arrow_capture_state[1] is True
 win.succeed=True;s._sync_arrow_layer();root.update();assert win.affinity[native2]==0
 print('PASS rebuilt layer inherits current policy; failed API call not cached as success and retries')
 s._arrow_layer_ready=False;s.toggle_stealth_mode();assert s.stealth_mode_enabled
 assert win.affinity[win.GetAncestor(root.winfo_id(),2)]==0x11
 assert win.affinity[win.GetAncestor(panel.winfo_id(),2)]==0x11
 before=len(win.calls);s.hotkeys_locked=True;s.toggle_stealth_mode();assert len(win.calls)==before
 print('PASS missing arrow layer preserves GUI/visualizer fallback; locked hotkey does not change policy')
root.destroy()
