"""
Build script for creating standalone Windows executable
Run this on Windows with Python installed
"""

import subprocess
import sys
import os
from pathlib import Path

def install_pyinstaller():
    """Install PyInstaller if not already installed"""
    try:
        import PyInstaller
        print("✅ PyInstaller is already installed")
    except ImportError:
        print("📦 Installing PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
        print("✅ PyInstaller installed successfully")

def install_dependencies():
    """Install all required dependencies"""
    print("📦 Installing dependencies...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    print("✅ Dependencies installed successfully")

def build_executable():
    """Build the standalone executable"""
    print("🔨 Building TradNet executable...")
    
    # Build using spec file
    subprocess.check_call([
        sys.executable, 
        "-m", 
        "PyInstaller", 
        "tradnet.spec",
        "--clean",
        "--noconfirm"
    ])
    
    print("✅ Build completed successfully!")
    print(f"📁 Executable location: dist/TradNet.exe")

def main():
    """Main build process"""
    print("=" * 60)
    print("🤖 TradNet Windows Build Script")
    print("=" * 60)
    
    # Check if running on Windows
    if sys.platform != "win32":
        print("⚠️  Warning: This script is designed for Windows.")
        print("   You can still build, but the executable may not work properly.")
        response = input("Continue anyway? (y/n): ")
        if response.lower() != 'y':
            return
    
    try:
        # Install PyInstaller
        install_pyinstaller()
        
        # Install dependencies
        install_dependencies()
        
        # Build executable
        build_executable()
        
        print("\n" + "=" * 60)
        print("🎉 Build completed successfully!")
        print("=" * 60)
        print("\n📋 Next steps:")
        print("1. Find the executable in: dist/TradNet.exe")
        print("2. Copy TradNet.exe to your desired location")
        print("3. Make sure MetaTrader 5 is installed on the target machine")
        print("4. Run TradNet.exe")
        print("\n⚠️  Note: The first run may take longer as it extracts dependencies.")
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Build failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
