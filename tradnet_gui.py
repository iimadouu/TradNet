"""
TradNet GUI Wrapper
Provides a simple Windows GUI interface for the trading bot
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
import threading
import queue
import sys
import os
from pathlib import Path
import json
import subprocess
import time

class LogRedirector:
    """Redirect stdout/stderr to GUI"""
    def __init__(self, queue):
        self.queue = queue
    
    def write(self, text):
        if text.strip():
            self.queue.put(('log', text))
    
    def flush(self):
        pass

class TradNetGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("TradNet - Trading Bot")
        self.root.geometry("1000x700")
        self.root.configure(bg='#2c3e50')
        
        # Bot process
        self.bot_process = None
        self.bot_thread = None
        self.running = False
        
        # Log queue for thread-safe GUI updates
        self.log_queue = queue.Queue()
        
        # Setup GUI
        self.setup_ui()
        
        # Start log processor
        self.process_logs()
        
        # Load config
        self.load_config()
    
    def setup_ui(self):
        """Setup the GUI interface"""
        # Header
        header_frame = tk.Frame(self.root, bg='#34495e', height=60)
        header_frame.pack(fill='x', padx=10, pady=10)
        header_frame.pack_propagate(False)
        
        title_label = tk.Label(
            header_frame, 
            text="🤖 TradNet Trading Bot", 
            font=('Arial', 18, 'bold'),
            bg='#34495e', 
            fg='#ecf0f1'
        )
        title_label.pack(pady=15)
        
        # Control Panel
        control_frame = tk.Frame(self.root, bg='#2c3e50')
        control_frame.pack(fill='x', padx=10, pady=5)
        
        # Start Button
        self.start_btn = tk.Button(
            control_frame,
            text="▶ Start Bot",
            command=self.start_bot,
            font=('Arial', 12, 'bold'),
            bg='#27ae60',
            fg='white',
            width=15,
            height=2,
            relief='flat',
            cursor='hand2'
        )
        self.start_btn.pack(side='left', padx=5)
        
        # Stop Button
        self.stop_btn = tk.Button(
            control_frame,
            text="⏹ Stop Bot",
            command=self.stop_bot,
            font=('Arial', 12, 'bold'),
            bg='#c0392b',
            fg='white',
            width=15,
            height=2,
            relief='flat',
            cursor='hand2',
            state='disabled'
        )
        self.stop_btn.pack(side='left', padx=5)
        
        # Config Button
        config_btn = tk.Button(
            control_frame,
            text="⚙ Config",
            command=self.open_config,
            font=('Arial', 12, 'bold'),
            bg='#f39c12',
            fg='white',
            width=12,
            height=2,
            relief='flat',
            cursor='hand2'
        )
        config_btn.pack(side='left', padx=5)
        
        # Clear Logs Button
        clear_btn = tk.Button(
            control_frame,
            text="🗑 Clear Logs",
            command=self.clear_logs,
            font=('Arial', 12, 'bold'),
            bg='#7f8c8d',
            fg='white',
            width=12,
            height=2,
            relief='flat',
            cursor='hand2'
        )
        clear_btn.pack(side='left', padx=5)
        
        # Status Label
        self.status_label = tk.Label(
            control_frame,
            text="● Status: Stopped",
            font=('Arial', 12),
            bg='#2c3e50',
            fg='#e74c3c'
        )
        self.status_label.pack(side='right', padx=20)
        
        # Log Viewer
        log_frame = tk.Frame(self.root, bg='#2c3e50')
        log_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        log_label = tk.Label(
            log_frame,
            text="📋 Bot Logs",
            font=('Arial', 12, 'bold'),
            bg='#2c3e50',
            fg='#ecf0f1'
        )
        log_label.pack(anchor='w')
        
        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            font=('Consolas', 10),
            bg='#1a1a1a',
            fg='#00ff00',
            insertbackground='white',
            wrap='word',
            height=20
        )
        self.log_text.pack(fill='both', expand=True)
        
        # Configure text tags for coloring
        self.log_text.tag_config('INFO', foreground='#00ff00')
        self.log_text.tag_config('WARNING', foreground='#f1c40f')
        self.log_text.tag_config('ERROR', foreground='#e74c3c')
        self.log_text.tag_config('DEBUG', foreground='#3498db')
        self.log_text.tag_config('DEFAULT', foreground='#ecf0f1')
    
    def load_config(self):
        """Load configuration from file"""
        config_file = Path('tradnet_config.json')
        if config_file.exists():
            try:
                with open(config_file, 'r') as f:
                    self.config = json.load(f)
                self.append_log('✅ Configuration loaded', 'INFO')
            except Exception as e:
                self.append_log(f'⚠️ Failed to load config: {e}', 'WARNING')
                self.config = {}
        else:
            self.append_log('⚠️ Config file not found', 'WARNING')
            self.config = {}
    
    def open_config(self):
        """Open configuration editor"""
        config_window = tk.Toplevel(self.root)
        config_window.title("Configuration Editor")
        config_window.geometry("600x500")
        config_window.configure(bg='#2c3e50')
        
        # Create scrollable text area for JSON
        text_frame = tk.Frame(config_window, bg='#2c3e50')
        text_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        config_text = scrolledtext.ScrolledText(
            text_frame,
            font=('Consolas', 10),
            bg='#1a1a1a',
            fg='#ecf0f1',
            wrap='none'
        )
        config_text.pack(fill='both', expand=True)
        
        # Load current config
        if self.config:
            config_text.insert('1.0', json.dumps(self.config, indent=2))
        
        # Buttons
        btn_frame = tk.Frame(config_window, bg='#2c3e50')
        btn_frame.pack(fill='x', padx=10, pady=10)
        
        def save_config():
            try:
                new_config = json.loads(config_text.get('1.0', 'end'))
                with open('tradnet_config.json', 'w') as f:
                    json.dump(new_config, f, indent=2)
                self.config = new_config
                self.append_log('✅ Configuration saved', 'INFO')
                config_window.destroy()
            except json.JSONDecodeError as e:
                messagebox.showerror('Error', f'Invalid JSON: {e}')
        
        save_btn = tk.Button(
            btn_frame,
            text="💾 Save",
            command=save_config,
            bg='#27ae60',
            fg='white',
            font=('Arial', 10, 'bold'),
            width=10
        )
        save_btn.pack(side='right', padx=5)
        
        cancel_btn = tk.Button(
            btn_frame,
            text="❌ Cancel",
            command=config_window.destroy,
            bg='#c0392b',
            fg='white',
            font=('Arial', 10, 'bold'),
            width=10
        )
        cancel_btn.pack(side='right', padx=5)
    
    def start_bot(self):
        """Start the trading bot"""
        if self.running:
            return
        
        self.running = True
        self.start_btn.config(state='disabled')
        self.stop_btn.config(state='normal')
        self.status_label.config(text="● Status: Running", fg='#27ae60')
        
        self.append_log('🚀 Starting TradNet bot...', 'INFO')
        
        # Start bot in separate thread
        self.bot_thread = threading.Thread(target=self.run_bot, daemon=True)
        self.bot_thread.start()
    
    def run_bot(self):
        """Run the bot and capture output"""
        try:
            # Import and run the bot
            import tradnet_main
            
            # Redirect stdout/stderr
            old_stdout = sys.stdout
            old_stderr = sys.stderr
            
            sys.stdout = LogRedirector(self.log_queue)
            sys.stderr = LogRedirector(self.log_queue)
            
            try:
                # Run the bot (this will block)
                tradnet_main.main()
            except SystemExit:
                pass
            except Exception as e:
                self.log_queue.put(('log', f'❌ Bot error: {e}\n'))
                self.log_queue.put(('log', f'   {type(e).__name__}\n'))
            finally:
                sys.stdout = old_stdout
                sys.stderr = old_stderr
                
        except ImportError as e:
            self.log_queue.put(('log', f'❌ Failed to import bot: {e}\n'))
        except Exception as e:
            self.log_queue.put(('log', f'❌ Unexpected error: {e}\n'))
        finally:
            self.log_queue.put(('status', 'stopped'))
    
    def stop_bot(self):
        """Stop the trading bot"""
        if not self.running:
            return
        
        self.append_log('⏹ Stopping bot...', 'WARNING')
        
        # Create emergency stop file
        try:
            with open('EMERGENCY_STOP.txt', 'w') as f:
                f.write('STOP')
            self.append_log('✅ Emergency stop signal sent', 'INFO')
        except Exception as e:
            self.append_log(f'⚠️ Failed to create stop file: {e}', 'WARNING')
        
        # Note: The bot will stop on its next cycle check
        # We'll update UI when we detect it's stopped
    
    def clear_logs(self):
        """Clear the log viewer"""
        self.log_text.delete('1.0', 'end')
    
    def append_log(self, message, level='DEFAULT'):
        """Append a log message to the viewer"""
        timestamp = time.strftime('%H:%M:%S')
        self.log_text.insert('end', f'[{timestamp}] {message}\n', level)
        self.log_text.see('end')
    
    def process_logs(self):
        """Process log queue and update GUI"""
        try:
            while True:
                try:
                    msg_type, msg = self.log_queue.get_nowait()
                    
                    if msg_type == 'log':
                        # Determine log level based on content
                        if 'ERROR' in msg or '❌' in msg:
                            level = 'ERROR'
                        elif 'WARNING' in msg or '⚠️' in msg:
                            level = 'WARNING'
                        elif 'INFO' in msg or '✅' in msg or '🚀' in msg:
                            level = 'INFO'
                        elif 'DEBUG' in msg:
                            level = 'DEBUG'
                        else:
                            level = 'DEFAULT'
                        
                        self.append_log(msg.rstrip(), level)
                    
                    elif msg_type == 'status':
                        if msg == 'stopped':
                            self.running = False
                            self.start_btn.config(state='normal')
                            self.stop_btn.config(state='disabled')
                            self.status_label.config(text="● Status: Stopped", fg='#e74c3c')
                            self.append_log('⏹ Bot stopped', 'WARNING')
                
                except queue.Empty:
                    break
        except Exception as e:
            print(f"Log processing error: {e}")
        
        # Schedule next check
        self.root.after(100, self.process_logs)
    
    def on_closing(self):
        """Handle window closing"""
        if self.running:
            if messagebox.askokcancel("Quit", "Bot is still running. Stop it first?"):
                self.stop_bot()
                time.sleep(2)  # Give it time to stop
            else:
                return
        
        self.root.destroy()

def main():
    """Main entry point"""
    root = tk.Tk()
    app = TradNetGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()

if __name__ == '__main__':
    main()
