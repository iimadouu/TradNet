# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['tradnet_gui.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('tradnet_config.json', '.'),
    ],
    hiddenimports=[
        'MetaTrader5',
        'pandas',
        'numpy',
        'talib',
        'sklearn',
        'xgboost',
        'openai',
        'market_analyzer',
        'trade_executor_enhanced',
        'performance_tracker',
        'scan_manager',
        'position_autopsy',
        'trade_logger',
        'trade_autopsy_system',
        'position_agent',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='TradNet',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # Set to True for debugging
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
