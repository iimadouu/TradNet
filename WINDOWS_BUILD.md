# TradNet Windows Standalone Build Guide

This guide explains how to bundle the TradNet trading bot into a standalone Windows executable that doesn't require Python to be installed.

## Prerequisites

### On the Build Machine (with Python installed):
- Windows 10/11
- Python 3.8 or higher
- Administrator privileges (for some installations)
- MetaTrader 5 installed (for testing)

### On the Target Machine (where you'll run the .exe):
- Windows 10/11
- MetaTrader 5 MUST be installed (required by MetaTrader5 library)
- No Python installation needed

## Build Instructions

### Step 1: Prepare the Build Environment

1. **Install Python** (if not already installed)
   - Download from https://www.python.org/downloads/
   - During installation, check "Add Python to PATH"

2. **Install TA-Lib** (required dependency)
   - Download the appropriate wheel from: https://www.lfd.uci.edu/~gohlke/pythonlibs/#ta-lib
   - Example for Python 3.12 64-bit: `TA_Lib-0.6.4-cp312-cp312-win_amd64.whl`
   - Install it: `pip install TA_Lib-0.6.4-cp312-cp312-win_amd64.whl`

3. **Navigate to the TradNet folder**
   ```cmd
   cd C:\path\to\TradNet
   ```

### Step 2: Build the Executable

Run the build script:
```cmd
python build_windows.py
```

This script will:
- Install PyInstaller
- Install all dependencies from requirements.txt
- Build the standalone executable using PyInstaller
- Place the executable in the `dist/` folder

### Alternative: Manual Build

If the automated script fails, you can build manually:

```cmd
pip install pyinstaller
pip install -r requirements.txt
pyinstaller tradnet.spec --clean --noconfirm
```

## Distribution

After building, you'll find:
- **Executable**: `dist/TradNet.exe` (this is what you distribute)
- **Configuration**: Copy `tradnet_config.json` to the same folder as the .exe
- **Logs**: Will be created in the same folder as the .exe

## Using the Standalone Executable

### First Run Setup

1. **Copy files to target machine:**
   - `TradNet.exe`
   - `tradnet_config.json` (edit with your MT5 credentials if needed)

2. **Ensure MetaTrader 5 is installed** on the target machine
   - The bot requires MT5 to be installed at: `C:\Program Files\MetaTrader 5\terminal64.exe`
   - Or update the path in `tradnet_config.json`

3. **Run the executable:**
   - Double-click `TradNet.exe`
   - The GUI will appear

### GUI Features

- **Start Bot**: Begins the trading bot
- **Stop Bot**: Stops the bot gracefully (creates EMERGENCY_STOP.txt)
- **Config**: Opens configuration editor to modify settings
- **Clear Logs**: Clears the log viewer
- **Log Viewer**: Shows real-time bot output with color coding:
  - 🟢 Green: Info messages
  - 🟡 Yellow: Warnings
  - 🔴 Red: Errors
  - 🔵 Blue: Debug messages

### Configuration

Click the **Config** button to edit `tradnet_config.json`:
- MT5 credentials (login, password, server, path)
- Trading symbols
- Risk management settings
- Trading hours

## Troubleshooting

### Build Issues

**"TA-Lib not found" during build:**
- Make sure TA-Lib is installed before building
- Download the correct wheel for your Python version

**"Missing module" errors:**
- Ensure all dependencies in requirements.txt are installed
- Check that hidden imports in tradnet.spec are complete

**Executable too large:**
- This is normal - it includes Python interpreter and all dependencies
- Typical size: 150-300 MB

### Runtime Issues

**"MetaTrader5 not found" on target machine:**
- MT5 MUST be installed on the target machine
- The MetaTrader5 library is a wrapper around the MT5 terminal
- Update the MT5 path in tradnet_config.json if installed elsewhere

**"Missing DLL" errors:**
- Some dependencies may require Visual C++ Redistributable
- Install from: https://aka.ms/vs/17/release/vc_redist.x64.exe

**Configuration file not found:**
- Ensure tradnet_config.json is in the same folder as TradNet.exe
- The bot will create a default config if missing

**Logs not appearing:**
- Check that the bot process is running in Task Manager
- Try stopping and restarting the bot
- Check tradnet.log file in the same folder

## File Structure After Build

```
TradNet/                    # Distribution folder
├── TradNet.exe            # Main executable (distribute this)
├── tradnet_config.json    # Configuration file (distribute this)
├── tradnet.log            # Runtime log (created automatically)
├── trades_log.csv         # Trade history (created automatically)
└── EMERGENCY_STOP.txt     # Stop signal (created when stopping)
```

## Security Notes

⚠️ **Important Security Considerations:**

1. **Credentials**: The tradnet_config.json contains MT5 credentials
   - Don't distribute the config file with real credentials
   - Users should edit it with their own credentials

2. **OpenAI API Key**: If using OpenAI features
   - API keys should be added by the user, not bundled
   - Consider adding API key input in the GUI

3. **Code Signing**: For distribution
   - Consider code signing the executable to avoid Windows SmartScreen warnings
   - Unsigned executables may trigger security warnings

## Performance Tips

- The first run may be slower as dependencies extract
- Subsequent runs will be faster
- For optimal performance, exclude the distribution folder from antivirus real-time scanning
- Ensure the target machine has sufficient RAM (4GB+ recommended)

## Support

If you encounter issues:
1. Check the tradnet.log file for detailed error messages
2. Ensure MetaTrader 5 is properly installed and configured
3. Verify all dependencies were successfully installed during build
4. Test the bot with Python first to isolate build vs. code issues

## Advanced Options

### Single File vs. Folder Distribution

The current spec creates a single executable. For faster startup times, you can modify tradnet.spec to create a folder distribution:

Change the EXE section to:
```python
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='TradNet',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='TradNet',
)
```

This creates a folder with TradNet.exe and supporting DLLs for faster startup.

### Adding an Icon

To add a custom icon:
1. Create an .ico file (tradnet.ico)
2. Update tradnet.spec: `icon='tradnet.ico'`
3. Rebuild

### Console Mode for Debugging

To see console output for debugging:
1. Set `console=True` in tradnet.spec
2. Rebuild
3. The executable will show a console window alongside the GUI
