#!/usr/bin/env python3
import os
import shutil
import sys
from pathlib import Path

def list_env_files():
    """List all available .env.* files"""
    env_files = list(Path('.').glob('.env.*'))
    if not env_files:
        print("No .env.* files found!")
        return

    print("\nAvailable environment files:")
    for i, env_file in enumerate(env_files, 1):
        print(f"{i}. {env_file.name}")

def switch_env(env_file):
    """Switch to the specified env file by copying it to .env"""
    if not os.path.exists(env_file):
        print(f"Error: {env_file} does not exist!")
        return False

    try:
        shutil.copy2(env_file, '.env')
        print(f"Successfully switched to {env_file}")
        return True
    except Exception as e:
        print(f"Error switching environment: {e}")
        return False

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python switch_env.py <env_name>")
        print("Example: python switch_env.py .env.account1")
        list_env_files()
        sys.exit(1)

    env_file = sys.argv[1]
    switch_env(env_file)
