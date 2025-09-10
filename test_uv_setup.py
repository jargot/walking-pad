#!/usr/bin/env python3
"""
Test script to verify uv setup and dependencies
"""

import sys
import subprocess

def test_uv_sync():
    """Test syncing dependencies with uv"""
    try:
        print("🧪 Testing uv sync...")
        result = subprocess.run(['uv', 'sync'], 
                              capture_output=True, text=True, check=True)
        print("✅ uv sync successful")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ uv sync failed: {e}")
        print(f"stdout: {e.stdout}")
        print(f"stderr: {e.stderr}")
        return False
    except FileNotFoundError:
        print("❌ uv command not found")
        return False

def test_imports():
    """Test importing required modules"""
    modules_to_test = [
        'flask',
        'asyncio', 
        'yaml',
        'psycopg2',
        'ph4_walkingpad',
        'bleak',
        'dotenv'
    ]
    
    print("\n🧪 Testing module imports...")
    failed_imports = []
    
    for module in modules_to_test:
        try:
            if module == 'yaml':
                import yaml
            elif module == 'dotenv':
                from dotenv import load_dotenv
            elif module == 'psycopg2':
                import psycopg2
            elif module == 'ph4_walkingpad':
                import ph4_walkingpad
                from ph4_walkingpad.pad import WalkingPad, Controller
            elif module == 'bleak':
                from bleak import BleakScanner
            elif module == 'flask':
                from flask import Flask
            elif module == 'asyncio':
                import asyncio
                
            print(f"✅ {module} imported successfully")
        except ImportError as e:
            print(f"❌ {module} import failed: {e}")
            failed_imports.append(module)
    
    return len(failed_imports) == 0

def test_connection_manager():
    """Test importing our connection manager"""
    try:
        print("\n🧪 Testing connection_manager import...")
        from connection_manager import WalkingPadConnectionManager
        print("✅ connection_manager imported successfully")
        return True
    except ImportError as e:
        print(f"❌ connection_manager import failed: {e}")
        return False

if __name__ == "__main__":
    print("🚀 Testing uv setup for WalkingPad project")
    print("=" * 50)
    
    success = True
    
    # Test uv sync
    success &= test_uv_sync()
    
    # Test imports
    success &= test_imports()
    
    # Test connection manager
    success &= test_connection_manager()
    
    print("\n" + "=" * 50)
    if success:
        print("🎉 All tests passed! Ready to use uv with LaunchAgent")
    else:
        print("❌ Some tests failed. Check the errors above.")
    
    sys.exit(0 if success else 1)